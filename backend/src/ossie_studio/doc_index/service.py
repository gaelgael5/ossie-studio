"""L'index des documents (enabler 15863091) : le tenir à jour.

Gael : l'index est « un outil de validation et un outil de recherche ». C'est un
**cache dérivé de Git** (comme l'index de l'Explorateur raw) : on peut le vider et
le reconstruire sans rien perdre, sauf les résultats de validation, qui se
recalculent.

- **Le contenu** est global par espace : un fichier, à une empreinte (blob Git),
  n'est lu et indexé qu'une fois (`ensure_contents`), pour toutes les vues.
- **Une vue** (branche publiée, brouillon d'une personne) est une liste « chemin →
  contenu » avec l'état de chaque fichier : `a_valider`, `valide`, `en_erreur`.
- **À l'enregistrement** (`record_draft_write`, appelé là où les services écrivent
  dans le brouillon, `DraftWorkdir`) : les fichiers changés prennent leur nouvelle
  empreinte dans la vue du brouillon et passent `a_valider`, comme ceux qui les
  citent (leurs références sortantes).
- **Écart entre disque et index** (`compare_view`) : un fichier nouveau ou d'empreinte
  différente est indexé et passe `a_valider` (modifications faites hors de
  l'application : fusion, pull, édition à la main) ; un fichier disparu est retiré
  de la vue ; dans les deux cas, ceux qui le citent passent `a_valider`.

Le déclenchement des passes de validation et les règles elles-mêmes sont l'objet
d'autres tickets de l'epic (la file, les règles).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import asyncpg
import structlog

from ossie_studio.doc_index import store
from ossie_studio.doc_index.extract import INDEXER_VERSION, index_file
from ossie_studio.doc_index.store import View, ViewKey
from ossie_studio.drafts.store import git_blob_sha

log = structlog.get_logger(__name__)

# Lit des contenus par empreinte (ceux qui manquent sont absents du résultat).
BlobReader = Callable[[list[str]], Awaitable[dict[str, bytes]]]


@dataclass
class Comparison:
    """Ce qu'une comparaison entre disque et index a changé dans une vue."""

    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    dependents: int = 0

    @property
    def touched(self) -> int:
        return len(self.added) + len(self.changed) + len(self.removed)


# --- Lire Git -------------------------------------------------------------------------------


async def tree_listing(cwd: Path, env: dict[str, str], rev: str = "HEAD") -> dict[str, str]:
    """Les fichiers d'une révision, chemin → empreinte, sans en lire le contenu."""
    proc = await asyncio.create_subprocess_exec(
        "git", "ls-tree", "-r", "-z", rev,
        cwd=str(cwd), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git ls-tree : {err.decode(errors='replace').strip()}")
    listing: dict[str, str] = {}
    for entry in out.split(b"\0"):
        if not entry:
            continue
        meta, _tab, path = entry.partition(b"\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == b"blob":
            listing[path.decode("utf-8", "surrogateescape")] = parts[2].decode()
    return listing


def blob_reader(cwd: Path, env: dict[str, str]) -> BlobReader:
    """Lit des contenus par empreinte en UN processus `git cat-file --batch`."""

    async def read(blobs: list[str]) -> dict[str, bytes]:
        if not blobs:
            return {}
        proc = await asyncio.create_subprocess_exec(
            "git", "cat-file", "--batch",
            cwd=str(cwd), env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
        out, _ = await proc.communicate("".join(f"{b}\n" for b in blobs).encode())
        found: dict[str, bytes] = {}
        pos = 0
        for blob in blobs:
            end = out.index(b"\n", pos)
            header = out[pos:end].split()
            pos = end + 1
            if len(header) == 3 and header[2].isdigit():
                size = int(header[2])
                if header[1] == b"blob":
                    found[blob] = out[pos : pos + size]
                pos += size + 1
        return found

    return read


# --- Contenu ---------------------------------------------------------------------------------


async def ensure_contents(
    conn: asyncpg.Connection,
    space_id: uuid.UUID,
    entries: Mapping[str, str],
    read: BlobReader,
    known: Mapping[str, bytes] | None = None,
) -> dict[str, tuple[int, list[str]]]:
    """Le contenu indexé de chaque (chemin → empreinte) : ceux déjà indexés sont
    repris tels quels ; les autres sont lus (`known` d'abord, sinon `read`) et
    indexés. Rend chemin → (identifiant du contenu, clés)."""
    found = await store.db_find_contents(conn, space_id, entries.items())
    out = {path: found[(path, blob)] for path, blob in entries.items() if (path, blob) in found}
    missing = {path: blob for path, blob in entries.items() if path not in out}
    if not missing:
        return out
    given = dict(known or {})
    to_read = sorted({b for b in missing.values() if b not in given})
    given.update(await read(to_read))
    readable: list[tuple[str, str, bytes]] = []
    for path, blob in sorted(missing.items()):
        data = given.get(blob)
        if data is None:
            log.warning("doc_index_blob_missing", path=path, blob=blob)
            continue
        readable.append((path, blob, data))
    # Analyse hors de la boucle du serveur (bug c6a4ab74) : le premier remplissage lit
    # des milliers de fichiers, et toutes les requêtes attendaient pendant ce temps.
    indexed_all = await asyncio.to_thread(
        lambda: [index_file(path, data) for path, _blob, data in readable]
    )
    for (path, blob, _data), indexed in zip(readable, indexed_all, strict=True):
        content_id = await store.db_put_content(conn, space_id, path, blob, indexed)
        out[path] = (content_id, indexed.keys)
    return out


# --- Comparer une vue au disque --------------------------------------------------------------


async def compare_view(
    conn: asyncpg.Connection,
    key: ViewKey,
    head: str | None,
    listing: Mapping[str, str],
    read: BlobReader,
    known: Mapping[str, bytes] | None = None,
) -> Comparison:
    """Met la vue d'accord avec `listing` (chemin → empreinte, ce que Git contient) et
    la marque comparée sur `head`."""
    view = await store.db_ensure_view(conn, key)
    indexed = await store.db_view_files(conn, view.id)
    # Une vue déjà comparée qui trouve des fichiers nouveaux ou changés : ils ont été
    # modifiés hors de l'application (le premier remplissage n'en détecte aucun).
    was_compared = view.compared
    result = Comparison()
    stale = {p: b for p, b in listing.items() if p not in indexed or indexed[p].blob != b}
    gone = [p for p in indexed if p not in listing]
    # Un contenu inchangé mais indexé par une version plus ancienne de l'extraction est
    # réindexé sur place (même fichier, même empreinte) : ni nouveau, ni à revalider.
    outdated = {
        p: row.blob
        for p, row in indexed.items()
        if p not in stale and p in listing and row.indexer_version != INDEXER_VERSION
    }
    if outdated:
        await ensure_contents(conn, key.space_id, outdated, read, known)
    contents = await ensure_contents(conn, key.space_id, stale, read, known)
    keys: set[str] = set()
    for path in stale:
        (result.changed if path in indexed else result.added).append(path)
        if path in indexed:
            keys.update(indexed[path].keys)
        if path in contents:
            keys.update(contents[path][1])
    for path in gone:
        keys.update(indexed[path].keys)
    await store.db_set_files(conn, view.id, {p: contents[p][0] for p in stale if p in contents})
    await store.db_remove_files(conn, view.id, gone)
    result.removed = sorted(gone)
    result.dependents = await store.db_mark_dependents(conn, view.id, keys)
    result.added.sort()
    result.changed.sort()
    detected = [*result.added, *result.changed] if was_compared else []
    await store.db_mark_compared(conn, view.id, head, len(listing), detected)
    return result


async def compare_published(
    conn: asyncpg.Connection,
    key: ViewKey,
    cwd: Path,
    env: dict[str, str],
    head: str,
) -> Comparison:
    """La vue de la branche publiée, comparée à la révision `head`."""
    listing = await tree_listing(cwd, env, head)
    return await compare_view(conn, key.published, head, listing, blob_reader(cwd, env))


async def compare_draft(
    conn: asyncpg.Connection,
    key: ViewKey,
    cwd: Path,
    env: dict[str, str],
    head: str,
    overrides: Mapping[str, bytes | None],
) -> Comparison:
    """La vue du brouillon d'une personne : la révision publiée `head`, ses objets
    par-dessus (`overrides` : chemin → contenu, None pour un fichier supprimé)."""
    listing = await tree_listing(cwd, env, head)
    known: dict[str, bytes] = {}
    for path, content in overrides.items():
        if content is None:
            listing.pop(path, None)
        else:
            blob = git_blob_sha(content)
            listing[path] = blob
            known[blob] = content
    return await compare_view(conn, key, head, listing, blob_reader(cwd, env), known)


# --- À l'enregistrement ----------------------------------------------------------------------


async def _apply(conn: asyncpg.Connection, view: View, writes: Mapping[str, bytes | None]) -> int:
    """Pose les contenus écrits dans la vue ; ceux qui les citent passent `a_valider`."""
    before = await store.db_view_files(conn, view.id, writes)
    known = {git_blob_sha(c): c for c in writes.values() if c is not None}
    entries = {p: git_blob_sha(c) for p, c in writes.items() if c is not None}

    async def nothing(_blobs: list[str]) -> dict[str, bytes]:
        return {}

    contents = await ensure_contents(conn, view.key.space_id, entries, nothing, known)
    keys: set[str] = set()
    for path in writes:
        if path in before:
            keys.update(before[path].keys)
        if path in contents:
            keys.update(contents[path][1])
    await store.db_set_files(conn, view.id, {p: contents[p][0] for p in entries if p in contents})
    await store.db_remove_files(conn, view.id, [p for p, c in writes.items() if c is None])
    return await store.db_mark_dependents(conn, view.id, keys)


async def record_draft_write(
    conn: asyncpg.Connection,
    key: ViewKey,
    head: str,
    writes: Mapping[str, bytes | None],
    overrides: Mapping[str, bytes | None],
) -> None:
    """Après un enregistrement dans le brouillon de la personne `key` : `writes`, les
    fichiers dont le contenu a changé (None : supprimé) ; `overrides`, tous les objets
    du brouillon après l'écriture.

    La vue du brouillon est tenue sur la tête publiée `head`. Si elle n'existe pas ou
    a été tirée d'une autre tête, elle repart de la vue publiée quand celle-ci est
    comparée sur `head` (recopiée, puis tous les objets du brouillon par-dessus) ;
    sinon elle ne garde que les fichiers du brouillon et attend sa comparaison au
    disque (tous ses fichiers sont alors à valider)."""
    view = await store.db_get_view(conn, key)
    if view is not None and view.compared and view.head == head:
        await _apply(conn, view, writes)
        return
    view = await store.db_ensure_view(conn, key)
    published = await store.db_get_view(conn, key.published)
    if published is not None and published.compared and published.head == head:
        await store.db_copy_view(conn, published.id, view.id)
        await _apply(conn, view, overrides)
        await store.db_mark_compared(conn, view.id, head)
        return
    await store.db_reset_view(conn, view.id)
    await _apply(conn, view, {p: c for p, c in overrides.items() if c is not None})


async def rebuild(
    conn: asyncpg.Connection,
    key: ViewKey,
    cwd: Path,
    env: dict[str, str],
    head: str,
    overrides: Mapping[str, bytes | None] | None = None,
) -> Comparison:
    """Vide la vue et la reconstruit depuis Git (les contenus déjà indexés restent :
    `store.db_clear_space` vide tout l'espace)."""
    view = await store.db_ensure_view(conn, key)
    await store.db_reset_view(conn, view.id)
    if key.user_id is None:
        return await compare_published(conn, key, cwd, env, head)
    return await compare_draft(conn, key, cwd, env, head, overrides or {})
