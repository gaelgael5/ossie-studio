"""Copie de travail **vue par un utilisateur** : la branche publiée, avec son
brouillon par-dessus. Façade aux mêmes signatures que l'ancien
`WorkdirManager.read_branch` / `commit_to_branch`, pour que les services
existants (sources, curation, collecte) n'aient rien d'autre à changer que le
type de l'objet reçu : toute écriture va désormais dans le brouillon de la
personne, jamais dans un commit."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import asyncpg
import structlog

from ossie_studio.doc_index.service import (
    Comparison,
    compare_draft,
    compare_published,
    record_draft_write,
)
from ossie_studio.doc_index.store import View, ViewKey, db_get_view, db_view_outdated
from ossie_studio.drafts.store import (
    DraftChange,
    DraftObject,
    DraftObjectTooLargeError,
    DraftPathError,
    db_get_draft,
    db_list_objects,
    db_replace_draft,
)
from ossie_studio.parsers.conventions import SCHEMA_SUFFIX
from ossie_studio.spaces.gitcli import run_git, show_files
from ossie_studio.spaces.views import (
    DraftState,
    DraftViewManager,
    ViewContext,
    capture_changes,
)
from ossie_studio.spaces.workdir import (
    _COMMIT_PREFIX,
    _VERSION_FETCH_REFSPEC,
    _VERSION_PREFIX,
    PathCommit,
    UnpublishedCommit,
    VersionRestored,
    WorkdirError,
    WorkdirManager,
)

log = structlog.get_logger(__name__)

_T = TypeVar("_T")


@dataclass(frozen=True)
class ObjectVersions:
    current_blob: str | None
    theirs: bytes | None
    base: bytes | None


async def _blob_bytes(copy: Path, env: dict[str, str], blob: str | None) -> bytes | None:
    if blob is None:
        return None
    proc = await asyncio.create_subprocess_exec(
        "git",
        "cat-file",
        "blob",
        blob,
        cwd=str(copy),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    return out if proc.returncode == 0 else None


async def _stale(conn: asyncpg.Connection, view: View | None, head: str) -> bool:
    """La vue de l'index est-elle à comparer de nouveau à Git (ticket 06171fd3) : jamais
    comparée, tirée d'une autre tête, ou indexée par une extraction plus ancienne ?"""
    if view is None or not view.compared or view.head != head:
        return True
    return await db_view_outdated(conn, view.id)


class DraftWorkdir:
    """Lectures et écritures d'**un** utilisateur sur **une** branche."""

    def __init__(
        self,
        manager: WorkdirManager,
        views: DraftViewManager,
        pool: asyncpg.Pool,
        user_id: uuid.UUID,
        branch_id: uuid.UUID,
        *,
        published_only: bool = False,
        conn: asyncpg.Connection | None = None,
    ) -> None:
        self._manager = manager
        self._views = views
        self._pool = pool
        self.user_id = user_id
        self.branch_id = branch_id
        # Vue « publiée » demandée par l'écran (?view=published) : les lectures
        # ignorent le brouillon. Les écritures, elles, vont toujours au brouillon.
        self.published_only = published_only
        # Connexion de la requête, réutilisée : en prendre une seconde au pool
        # pendant que la requête tient déjà la sienne épuiserait le pool sous
        # charge (interblocage). Hors requête (tests, outils), le pool sert.
        self._conn = conn

    @asynccontextmanager
    async def _db(self) -> AsyncIterator[asyncpg.Connection]:
        if self._conn is not None:
            yield self._conn
        else:
            async with self._pool.acquire() as conn:
                yield conn

    async def _state(self) -> DraftState:
        async with self._db() as conn:
            draft = await db_get_draft(conn, self.user_id, self.branch_id)
            if draft is None:
                return DraftState(draft_id=None, revision=0, objects=[])
            return DraftState(
                draft_id=draft.id,
                revision=draft.revision,
                objects=await db_list_objects(conn, draft.id),
            )

    @property
    def manager(self) -> WorkdirManager:
        return self._manager

    def draft_lock(self, space_id: uuid.UUID, slug: str) -> asyncio.Lock:
        return self._views.lock_for(space_id, slug, self.user_id)

    async def discard_view(self, space_id: uuid.UUID, slug: str) -> None:
        await self._views.discard(space_id, slug, self.user_id)

    async def view_signature(
        self, space_id: uuid.UUID, slug: str, repo_url: str, username: str, token: str | None
    ) -> tuple[str, str]:
        """(vue, signature) de ce que verrait une lecture : `published` ou
        `merged`, et une empreinte qui change dès que le contenu lu peut changer
        (tête publiée de la branche, brouillon). Bon marché : la tête vient d'un
        fetch limité dans le temps (`published_head`), le brouillon d'une ligne
        de `drafts` — sans charger ses objets ni matérialiser la vue. Sert aux
        caches dérivés (index de l'explorateur raw)."""
        head = await self._manager.published_head(space_id, slug, repo_url, username, token)
        if self.published_only:
            return "published", head
        async with self._db() as conn:
            draft = await db_get_draft(conn, self.user_id, self.branch_id)
        if draft is None:
            return "merged", f"{head}:-"
        # L'identifiant du brouillon en plus de sa révision : un brouillon publié
        # puis recréé repart à une révision déjà vue.
        return "merged", f"{head}:{draft.id}:{draft.revision}"

    async def prefix_signature(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        prefix: str,
    ) -> tuple[str, list[str]]:
        """Empreinte de ce qu'une lecture verrait sous `prefix`, et les chemins du
        brouillon sous ce préfixe (ticket 0373780f, index de la liste des référentiels).

        Plus fine que `view_signature` : celle-ci change à chaque révision du
        brouillon, donc à chaque collecte de source ou tag posé, alors que la liste
        des référentiels ne dépend que de `referentiels/`. Ici : la tête publiée
        (fetch limité dans le temps) et l'empreinte des seuls objets du brouillon
        sous le préfixe, calculée par la base (`md5`) sans rapatrier les contenus.
        Les chemins sont rendus quel que soit le mode : « non publié » se lit
        toujours sur le brouillon, même en vue publiée."""
        head = await self._manager.published_head(space_id, slug, repo_url, username, token)
        async with self._db() as conn:
            draft = await db_get_draft(conn, self.user_id, self.branch_id)
            rows = (
                []
                if draft is None
                else await conn.fetch(
                    "SELECT path, md5(content) AS digest FROM draft_objects "
                    "WHERE draft_id = $1 AND starts_with(path, $2) ORDER BY path",
                    draft.id,
                    prefix,
                )
            )
        paths = [r["path"] for r in rows]
        if self.published_only:
            return f"published:{head}", paths
        digest = hashlib.sha256(
            "\n".join(f"{r['path']} {r['digest'] or '-'}" for r in rows).encode()
        ).hexdigest()
        return f"merged:{head}:{digest}", paths

    async def read_file(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        rel_path: str,
    ) -> str | None:
        """Un fichier tel que le voit l'utilisateur, **sans matérialiser la vue** :
        sa version de brouillon s'il l'a modifié (None s'il l'a supprimé), sinon
        celle de la tête publiée, lue dans les objets git de la copie. Pour les
        lectures fréquentes d'un seul fichier (aperçu de l'explorateur raw)."""
        head = await self._manager.published_head(space_id, slug, repo_url, username, token)
        if not self.published_only:
            async with self._db() as conn:
                draft = await db_get_draft(conn, self.user_id, self.branch_id)
                if draft is not None:
                    row = await conn.fetchrow(
                        "SELECT content FROM draft_objects WHERE draft_id = $1 AND path = $2",
                        draft.id,
                        rel_path,
                    )
                    if row is not None:
                        content = row["content"]
                        return bytes(content).decode("utf-8", "replace") if content else None
        return await self._manager.local_file_at(space_id, slug, head, rel_path)

    async def has_draft(self) -> bool:
        async with self._db() as conn:
            return await db_get_draft(conn, self.user_id, self.branch_id) is not None

    async def _store(
        self, space_id: uuid.UUID, ctx: ViewContext, before: DraftState
    ) -> tuple[bool, DraftState]:
        """Capte la vue, remplace le brouillon ; rend (modifié ?, nouvel état)."""
        changes = await capture_changes(ctx)
        previous = {o.path: o.content for o in before.objects}
        current = {c.path: c.content for c in changes}
        if previous == current:
            return False, before
        try:
            async with self._db() as conn:
                draft = await db_replace_draft(
                    conn,
                    user_id=self.user_id,
                    space_id=space_id,
                    branch_id=self.branch_id,
                    head_commit=ctx.head,
                    changes=changes,
                )
                await self._index(conn, space_id, ctx, before, changes)
                if draft is None:
                    return True, DraftState(draft_id=None, revision=0, objects=[])
                objects = await db_list_objects(conn, draft.id)
        except DraftPathError:
            raise WorkdirError("draft_path_invalid") from None
        except DraftObjectTooLargeError:
            raise WorkdirError("draft_object_too_large") from None
        return True, DraftState(draft_id=draft.id, revision=draft.revision, objects=objects)

    async def _index(
        self,
        conn: asyncpg.Connection,
        space_id: uuid.UUID,
        ctx: ViewContext,
        before: DraftState,
        changes: list[DraftChange],
    ) -> None:
        """L'index des documents (enabler 15863091) suit chaque enregistrement, par
        n'importe quel écrivain (écran, agent, import, collecte) : c'est le seul point
        où les services écrivent dans le brouillon. Les fichiers dont le contenu a
        changé prennent leur nouvelle empreinte et passent `a_valider`, comme ceux qui
        les citent. Un cache : son échec est journalisé, jamais celui de l'écriture."""
        previous = {o.path: o for o in before.objects}
        current = {c.path: c for c in changes}
        writes: dict[str, bytes | None] = {}
        for path in previous.keys() | current.keys():
            old, new = previous.get(path), current.get(path)
            if new is not None:
                if old is None or old.content != new.content:
                    writes[path] = new.content
            elif old is not None:
                # Retour à la version publiée : son contenu est celui de la tête.
                writes[path] = await _blob_bytes(ctx.path, ctx.env, old.base_blob)
        if not writes:
            return
        key = ViewKey(space_id, self.branch_id, self.user_id)
        overrides = {c.path: c.content for c in changes}
        try:
            async with conn.transaction():
                await record_draft_write(conn, key, ctx.head, writes, overrides)
        except Exception:  # un cache ne fait jamais échouer une écriture
            log.exception("doc_index_write_failed", space_id=str(space_id))

    # --- Lectures ------------------------------------------------------------

    async def compare_index(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
    ) -> tuple[Comparison, Comparison]:
        """Compare à Git, dans l'index des documents (enabler 15863091), la vue publiée
        de la branche puis celle du brouillon de la personne : un fichier nouveau ou
        changé est indexé et passe `a_valider`, un fichier disparu est retiré ; ceux
        qui les citent passent `a_valider`. Rend (publiée, brouillon)."""

        async def fn(ctx: ViewContext, state: DraftState) -> tuple[Comparison, Comparison]:
            overrides = {o.path: o.content for o in state.objects}
            key = ViewKey(space_id, self.branch_id, self.user_id)
            async with self._db() as conn, conn.transaction():
                published = await compare_published(conn, key, ctx.path, ctx.env, ctx.head)
                draft = await compare_draft(conn, key, ctx.path, ctx.env, ctx.head, overrides)
            return published, draft

        return await self.run_in_view(space_id, slug, repo_url, username, token, fn)

    async def indexed_view(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
    ) -> ViewKey:
        """La vue que lit la personne, dans l'index des documents, d'accord avec Git
        (ticket 06171fd3) : comparée de nouveau seulement si la tête de la branche a
        bougé depuis (ou jamais comparée). Les écritures du brouillon la tiennent à
        jour d'elles-mêmes. Sans brouillon, ou en lecture publiée : la vue publiée
        (comme `read_branch`)."""
        published = ViewKey(space_id, self.branch_id)
        if self.published_only or not await self.has_draft():
            async with self._manager.read_copy(space_id, slug, repo_url, username, token) as (
                root,
                env,
                head,
            ):
                async with self._db() as conn:
                    if await _stale(conn, await db_get_view(conn, published), head):
                        async with conn.transaction():
                            await compare_published(conn, published, root, env, head)
            return published
        key = ViewKey(space_id, self.branch_id, self.user_id)

        async def fn(ctx: ViewContext, state: DraftState) -> ViewKey:
            overrides = {o.path: o.content for o in state.objects}
            async with self._db() as conn:
                base_stale = await _stale(conn, await db_get_view(conn, published), ctx.head)
                mine_stale = await _stale(conn, await db_get_view(conn, key), ctx.head)
                async with conn.transaction():
                    if base_stale:
                        await compare_published(conn, key, ctx.path, ctx.env, ctx.head)
                    if mine_stale:
                        await compare_draft(conn, key, ctx.path, ctx.env, ctx.head, overrides)
            return key

        return await self.run_in_view(space_id, slug, repo_url, username, token, fn)

    async def run_in_view(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        fn: Callable[[ViewContext, DraftState], Awaitable[_T]],
    ) -> _T:
        """Exécute `fn` dans la vue de la personne (branche publiée + son brouillon),
        sous le verrou de la vue, avec l'état de son brouillon. Rien n'est écrit dans
        le brouillon (une passe de validation, enabler 6b171b74)."""

        async def run(ctx: ViewContext) -> tuple[_T, None]:
            return await fn(ctx, await self._state()), None

        return await self._views.run(
            space_id=space_id,
            slug=slug,
            repo_url=repo_url,
            username=username,
            token=token,
            user_id=self.user_id,
            load=self._state,
            fn=run,
        )

    async def read_branch(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        read_fn: Callable[[Path], _T],
        *,
        published: bool = False,
    ) -> _T:
        """Lit la branche telle que la voit l'utilisateur (avec son brouillon),
        ou telle qu'elle est publiée (`published=True`, ou pas de brouillon)."""
        if published or self.published_only or not await self.has_draft():
            return await self._manager.read_branch(
                space_id, slug, repo_url, username, token, read_fn
            )

        async def fn(ctx: ViewContext) -> tuple[_T, DraftState | None]:
            return read_fn(ctx.path), None

        return await self._views.run(
            space_id=space_id,
            slug=slug,
            repo_url=repo_url,
            username=username,
            token=token,
            user_id=self.user_id,
            load=self._state,
            fn=fn,
        )

    async def review_inputs(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        raw_rel: str,
        tree_fn: Callable[[Path], _T],
    ) -> tuple[str, dict[str, str | None], _T]:
        """Entrées d'une revue (ticket docflow aad1d3c6) : la tête publiée, le
        brut **publié** des seuls fichiers de `raw_rel` que mon brouillon touche
        (nom → texte, None s'il n'existe pas côté publié), et ce que `tree_fn`
        lit dans ma vue. La base de comparaison est la branche publiée : ce que
        mon brouillon n'a pas touché y est identique à ma vue, inutile de le relire."""
        if self.published_only or not await self.has_draft():
            head, tree = await self._manager.review_inputs(
                space_id, slug, repo_url, username, token, tree_fn
            )
            return head, {}, tree
        prefix = f"{raw_rel.rstrip('/')}/"
        state = await self._state()
        touched = [
            o.path
            for o in state.objects
            if o.path.startswith(prefix)
            and "/" not in o.path[len(prefix) :]
            and o.path.endswith(SCHEMA_SUFFIX)
        ]

        async def fn(ctx: ViewContext) -> tuple[tuple[str, dict[str, str | None], _T], None]:
            texts = await show_files(ctx.path, ctx.env, ctx.head, touched)
            published = {path[len(prefix) :]: text for path, text in texts.items()}
            return (ctx.head, published, tree_fn(ctx.path)), None

        return await self._views.run(
            space_id=space_id,
            slug=slug,
            repo_url=repo_url,
            username=username,
            token=token,
            user_id=self.user_id,
            load=self._state,
            fn=fn,
        )

    async def published_blobs(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        paths: list[str],
    ) -> dict[str, str]:
        """Blob actuel de chaque chemin à la tête publiée (absent = n'y existe pas)."""
        if not paths:
            return {}
        async with self._manager.read_copy(space_id, slug, repo_url, username, token) as (
            copy,
            env,
            _head,
        ):
            code, out, _ = await run_git(["ls-tree", "-r", "-z", "HEAD", "--", *paths], copy, env)
        blobs: dict[str, str] = {}
        for line in out.split("\0") if code == 0 else []:
            if line:
                meta, path = line.split("\t", 1)
                blobs[path] = meta.split()[2]
        return blobs

    async def object_versions(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        objects: list[DraftObject],
    ) -> dict[str, ObjectVersions]:
        """Pour chaque objet du brouillon : blob et contenu actuels côté publié,
        et contenu de sa version de base (lus dans la copie de lecture)."""
        out: dict[str, ObjectVersions] = {}
        if not objects:
            return out
        async with self._manager.read_copy(space_id, slug, repo_url, username, token) as (
            copy,
            env,
            _head,
        ):
            paths = [o.path for o in objects]
            code, tree, _ = await run_git(["ls-tree", "-r", "-z", "HEAD", "--", *paths], copy, env)
            current: dict[str, str] = {}
            for line in tree.split("\0") if code == 0 else []:
                if line:
                    meta, path = line.split("\t", 1)
                    current[path] = meta.split()[2]
            for obj in objects:
                blob = current.get(obj.path)
                out[obj.path] = ObjectVersions(
                    current_blob=blob,
                    theirs=await _blob_bytes(copy, env, blob),
                    base=await _blob_bytes(copy, env, obj.base_blob),
                )
        return out

    async def file_history(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        rel_path: str | list[str],
        limit: int = 50,
    ) -> list[UnpublishedCommit]:
        # L'historique est celui du travail publié : un brouillon n'a pas d'histoire.
        return await self._manager.file_history(
            space_id, slug, repo_url, username, token, rel_path, limit
        )

    async def path_log(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        rel_path: str,
        limit: int = 200,
    ) -> list[PathCommit]:
        # Historique publié : un brouillon n'a pas d'histoire.
        return await self._manager.path_log(
            space_id, slug, repo_url, username, token, rel_path, limit
        )

    async def file_at(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        sha: str,
        rel_path: str,
    ) -> str | None:
        return await self._manager.file_at(space_id, slug, repo_url, username, token, sha, rel_path)

    # --- Écritures : toujours dans le brouillon ---------------------------------

    async def commit_to_branch(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        author_name: str,
        author_email: str,
        message: str,
        write_fn: Callable[[Path], None],
        *,
        expect_head: str | None = None,
    ) -> str | None:
        """Applique `write_fn` dans la vue de l'utilisateur et range le résultat
        dans son brouillon. Rend un repère de révision (`draft:<n>`) si le
        brouillon a changé, None sinon (jamais de « commit vide »). Aucun commit,
        aucun push. `expect_head` : tête publiée attendue (revue) ; si elle a
        bougé, refus `review_stale` avant d'écrire."""

        async def fn(ctx: ViewContext) -> tuple[str | None, DraftState | None]:
            if expect_head is not None and ctx.head != expect_head:
                raise WorkdirError("review_stale")
            before = await self._state()
            write_fn(ctx.path)
            changed, state = await self._store(space_id, ctx, before)
            log.info(
                "draft_write",
                space_id=str(space_id),
                slug=slug,
                changed=changed,
                intent=message[:80],
            )
            return (f"draft:{state.revision}" if changed else None), state

        return await self._views.run(
            space_id=space_id,
            slug=slug,
            repo_url=repo_url,
            username=username,
            token=token,
            user_id=self.user_id,
            load=self._state,
            fn=fn,
        )

    async def restore_version(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        number: int,
        author_name: str,
        author_email: str,
    ) -> VersionRestored:
        """Revenir à une version = remettre son arbre **dans le brouillon** (il se
        publie ensuite comme toute modification). L'historique n'est pas réécrit."""

        async def fn(ctx: ViewContext) -> tuple[VersionRestored, DraftState | None]:
            await run_git(["fetch", "-q", "origin", _VERSION_FETCH_REFSPEC], ctx.path, ctx.env)
            tag = f"{_VERSION_PREFIX}{number}"
            code, commit_out, _ = await run_git(["rev-list", "-n", "1", tag], ctx.path, ctx.env)
            if code != 0 or not commit_out.strip():
                raise WorkdirError("version_not_found")
            reach, _, _ = await run_git(
                ["merge-base", "--is-ancestor", commit_out.strip(), "HEAD"], ctx.path, ctx.env
            )
            if reach != 0:
                raise WorkdirError("version_not_found")
            _, subject, _ = await run_git(
                ["tag", "-l", "--format=%(contents:subject)", tag], ctx.path, ctx.env
            )
            label = subject.strip()
            before = await self._state()
            await run_git(["read-tree", "--reset", "-u", f"{tag}^{{tree}}"], ctx.path, ctx.env)
            changed, state = await self._store(space_id, ctx, before)
            if not changed and not state.objects:
                return VersionRestored(number, label, already_current=True, created=False), state
            log.info(
                "version_restored_to_draft",
                space_id=str(space_id),
                number=number,
                intent=f"{_COMMIT_PREFIX}Retour à la version {number}",
            )
            return VersionRestored(number, label, already_current=False, created=changed), state

        return await self._views.run(
            space_id=space_id,
            slug=slug,
            repo_url=repo_url,
            username=username,
            token=token,
            user_id=self.user_id,
            load=self._state,
            fn=fn,
        )
