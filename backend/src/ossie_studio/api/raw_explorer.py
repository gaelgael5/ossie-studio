"""Explorateur raw : lecture seule de la couche raw d'un espace et d'une branche.

Découpage de l'API (latitude du ticket) : un résumé des sources, l'arbre des
dossiers d'une source, le contenu d'un dossier, une recherche. Un dossier se
charge seul : l'écran ne reçoit jamais toute une source d'un coup. Tout est
servi depuis l'index en base (`raw_explorer_index`), reconstruit seulement quand
la tête publiée de la branche ou le brouillon de l'utilisateur a changé.

Accès : capacité `sources:read` — l'explorateur est un onglet de la page
« Sources » et ne fait que lire ce que cette page déclare et collecte ; une
capacité distincte n'apporterait rien tant qu'il ne modifie rien.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends

from ossie_studio.api.branches import space_credentials
from ossie_studio.api.common import SourcesRead
from ossie_studio.api.tag_rules import TagRuleBody
from ossie_studio.connections.service import db_get_connection_by_slug
from ossie_studio.crypto import CryptoService
from ossie_studio.deps import ConnDep, get_crypto, get_draft_workdir
from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.errors import ApiError
from ossie_studio.forges.links import forge_file_url
from ossie_studio.governance.builtin_tag_rules import BUILTIN_RULES_VERSION
from ossie_studio.governance.tag_rules import active_rules, parse_rule, read_tag_removals
from ossie_studio.parsers.base import STATUT_ERREUR
from ossie_studio.sources.curation import is_schema_file, read_review_point
from ossie_studio.sources.explorer import (
    DEFAULT_MODE,
    GIT_SOURCE_TYPE,
    INDEX_FORMAT,
    MODES,
    ExplorerDocument,
    ExplorerObject,
    ObjectTag,
    SourceEntries,
    SourceIndex,
    collect_source_entries,
    folder_item_count,
    index_from_entries,
    list_source_slugs,
    object_folder,
    search_objects,
    sort_objects,
    subtree_file_counts,
)
from ossie_studio.sources.explorer_detail import SpaceObjects, document_detail, object_detail
from ossie_studio.sources.explorer_graph import ReferenceGraph
from ossie_studio.sources.explorer_history import schema_changes
from ossie_studio.sources.explorer_store import (
    IndexKey,
    db_all_source_entries,
    db_get_source_entries,
    db_index_signature,
    db_list_indexed_sources,
    db_replace_index,
)
from ossie_studio.sources.explorer_tags import apply_tag_rules, dependents_losing, index_removals
from ossie_studio.sources.manifest import ManifestError, parse_manifest
from ossie_studio.sources.paths import is_valid_source_slug
from ossie_studio.sources.scan_service import ScanFile, db_list_scan_files
from ossie_studio.spaces.branch_service import db_get_branch
from ossie_studio.spaces.workdir import WorkdirError

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/raw-explorer")

# Commits montrés dans l'onglet Historique de l'aperçu (latitude du ticket).
HISTORY_LIMIT = 20

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]
# Copie vue par la personne connectée (avec son brouillon) : ce qu'elle vient de
# collecter sans l'avoir publié se voit dans l'explorateur.
WorkdirDep = Annotated[DraftWorkdir, Depends(get_draft_workdir)]


def _object_payload(obj: ExplorerObject) -> dict[str, object]:
    return {
        "file": obj.file,
        "title": obj.title,
        "kind": obj.kind,
        "fields": obj.fields,
        "origin": obj.origin,
        "parser": obj.parser,
        "badges": list(obj.badges),
        "excluded_by": obj.excluded_by,
        "visibility": obj.visibility,
        "visibility_source": obj.visibility_source,
        "tags": [_tag_payload(t) for t in obj.tags],
    }


def _tag_payload(tag: ObjectTag) -> dict[str, str]:
    return {
        "tag": tag.tag,
        "rule": tag.rule,
        "rule_name": tag.rule_name,
        "reason": tag.reason,
        "value": tag.value,
        "via": tag.via,
        "via_title": tag.via_title,
    }


def _document_payload(doc: ExplorerDocument) -> dict[str, object]:
    return {"path": doc.path, "title": doc.title, "sections": doc.sections}


def _workdir_error(exc: WorkdirError) -> ApiError:
    return ApiError(exc.code, status_code=404 if exc.code == "branch_not_found" else 502)


def _check_mode(mode: str) -> str:
    if mode not in MODES:
        raise ApiError("raw_explorer_mode_invalid", status_code=400, mode=mode)
    return mode


async def _branch_ref(conn: ConnDep, space_id: uuid.UUID, branch_id: uuid.UUID) -> str:
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    return branch.git_ref


def _collect_state(files: list[ScanFile]) -> dict[str, object] | None:
    """État de la dernière collecte, d'après son index de travail (scan_files)."""
    if not files:
        return None
    errors = [f for f in files if f.statut == STATUT_ERREUR]
    return {
        "date": max(f.scanned_at for f in files).isoformat(),
        "files": len(files),
        "errors": len(errors),
    }


async def _scan(conn: ConnDep, space_id: uuid.UUID, branch_ref: str, slug: str) -> list[ScanFile]:
    return await db_list_scan_files(
        conn, space_id=space_id, branch_slug=branch_ref, source_slug=slug
    )


# Une reconstruction à la fois par index : dix clics pendant qu'un index se
# reconstruit attendent celle en cours au lieu d'en lancer dix.
_BUILD_LOCKS: dict[IndexKey, asyncio.Lock] = {}


async def _ensure_index(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> tuple[IndexKey, str]:
    """Clé d'un index à jour (reconstruit si sa signature a changé) et la
    référence git de la branche. Le cas courant — rien n'a bougé — ne lit que la
    base : ni vue de brouillon, ni relecture de raw/."""
    branch_ref = await _branch_ref(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        view, signature = await workdir.view_signature(
            space.id, branch_ref, space.repo_url, username, token
        )
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    # Les règles fournies par Ossie font partie du produit : leur version entre
    # dans la signature (une nouvelle livraison de règles reconstruit l'index).
    signature = f"v{INDEX_FORMAT}.{BUILTIN_RULES_VERSION}:{signature}"
    key = IndexKey(space_id=space_id, branch_id=branch_id, user_id=workdir.user_id, view=view)
    if await db_index_signature(conn, key) == signature:
        return key, branch_ref
    lock = _BUILD_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        if await db_index_signature(conn, key) == signature:
            return key, branch_ref

        def read(wc: Path) -> list[SourceEntries]:
            # Les tags se calculent ici, sur toutes les sources à la fois (une
            # chaîne d'héritage peut en traverser deux) ; les règles sont dans
            # le dépôt, donc dans la signature : les modifier reconstruit l'index.
            return apply_tag_rules(
                [
                    collect_source_entries(wc, slug, review_base=read_review_point(wc, slug))
                    for slug in list_source_slugs(wc)
                ],
                active_rules(wc),
                {(r.object, r.tag) for r in read_tag_removals(wc)},
            )

        try:
            sources = await workdir.read_branch(
                space.id, branch_ref, space.repo_url, username, token, read
            )
        except WorkdirError as exc:
            raise _workdir_error(exc) from None
        await db_replace_index(conn, key, signature, sources)
        log.info(
            "raw_explorer_index_rebuilt",
            space_id=str(space_id),
            branch=branch_ref,
            view=view,
            sources=len(sources),
            objects=sum(len(s.objects) for s in sources),
        )
    return key, branch_ref


async def _read_index(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    mode: str,
) -> tuple[SourceIndex, list[ScanFile]]:
    if not is_valid_source_slug(slug):
        raise ApiError("source_not_found", status_code=404, slug=slug)
    key, branch_ref = await _ensure_index(conn, crypto, workdir, space_id, branch_id)
    entries = await db_get_source_entries(conn, key, slug)
    if entries is None:
        raise ApiError("source_not_found", status_code=404, slug=slug)
    scan = await _scan(conn, space_id, branch_ref, slug)
    error_origins = {f.path for f in scan if f.statut == STATUT_ERREUR}
    return index_from_entries(entries, mode, error_origins), scan


@router.get("")
async def list_explorer_sources(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: SourcesRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    """Les sources de la branche, avec ce que la couche raw en contient et l'état
    de leur dernière collecte."""
    key, branch_ref = await _ensure_index(conn, crypto, workdir, space_id, branch_id)
    out: list[dict[str, object]] = []
    for source in await db_list_indexed_sources(conn, key):
        scan = await _scan(conn, space_id, branch_ref, source.slug)
        out.append(
            {
                "slug": source.slug,
                "label": source.label,
                "type": source.type,
                "collected": source.collected,
                "objects": source.objects,
                "documents": source.documents,
                "last_collect": _collect_state(scan),
            }
        )
    return {"sources": out}


@router.get("/{slug}/tree")
async def get_explorer_tree(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: SourcesRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    mode: str = DEFAULT_MODE,
) -> dict[str, object]:
    """Les dossiers d'une source pour un mode d'affichage (sans leur contenu)."""
    index, scan = await _read_index(
        conn, crypto, workdir, space_id, branch_id, slug, _check_mode(mode)
    )
    counts = subtree_file_counts(index)
    return {
        "slug": index.slug,
        "label": index.label,
        "type": index.type,
        "mode": index.mode,
        "collected": index.collected,
        "objects": len(index.objects),
        "documents": len(index.documents),
        "last_collect": _collect_state(scan),
        "folders": [
            {"path": path, "items": folder_item_count(folder), "files": counts[path]}
            for path, folder in sorted(index.folders.items())
        ],
    }


@router.get("/{slug}/folder")
async def get_explorer_folder(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: SourcesRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    mode: str = DEFAULT_MODE,
    path: str = "",
) -> dict[str, object]:
    """Contenu d'un dossier : sous-dossiers, objets et documents."""
    index, _scan_files = await _read_index(
        conn, crypto, workdir, space_id, branch_id, slug, _check_mode(mode)
    )
    folder = index.folders.get(path)
    if folder is None:
        raise ApiError("raw_explorer_folder_not_found", status_code=404, path=path)
    counts = subtree_file_counts(index)
    return {
        "path": path,
        "folders": [
            {"path": sub, "items": folder_item_count(index.folders[sub]), "files": counts[sub]}
            for sub in sorted(folder.folders, key=str.casefold)
        ],
        "objects": [_object_payload(o) for o in sort_objects(folder.objects)],
        "documents": [_document_payload(d) for d in sorted(folder.documents, key=lambda d: d.path)],
    }


@router.get("/{slug}/search")
async def search_explorer(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: SourcesRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    q: str = "",
    mode: str = DEFAULT_MODE,
) -> dict[str, object]:
    """Objets de la source dont le nom contient `q` (insensible à la casse), avec
    le dossier où les ouvrir dans le mode courant."""
    index, _scan_files = await _read_index(
        conn, crypto, workdir, space_id, branch_id, slug, _check_mode(mode)
    )
    return {
        "results": [
            {"folder": folder, **_object_payload(obj)}
            for folder, obj in search_objects(index, q[:200])
        ]
    }


# --- Aperçu de l'élément sélectionné -------------------------------------------

# Résolveur des références de l'espace, gardé par (index, signature, mode) : tant
# que l'index ne change pas, un clic ne relit pas toutes les sources.
# --- Règles de tags : décompte et aperçu, calculés sur l'index -----------------

# Exemples montrés par l'aperçu d'une règle (latitude du ticket « écran des règles »).
TAG_RULE_EXAMPLES = 8


def _rule_objects(
    sources: list[SourceEntries], rule_id: str
) -> tuple[int, int, list[dict[str, object]]]:
    """(objets tagués, dont hérités, exemples) d'une règle dans des sources taguées."""
    count = inherited = 0
    examples: list[dict[str, object]] = []
    for entries in sources:
        for obj in entries.objects:
            own = [t for t in obj.tags if t.rule == rule_id]
            if not own:
                continue
            count += 1
            is_inherited = all(t.via for t in own)
            inherited += is_inherited
            if len(examples) < TAG_RULE_EXAMPLES:
                examples.append(
                    {
                        "slug": entries.slug,
                        "file": obj.file,
                        "title": obj.title,
                        "folder": object_folder(DEFAULT_MODE, entries, obj),
                        "inherited": is_inherited,
                    }
                )
    return count, inherited, examples


@router.get("/tag-rules/usage")
async def tag_rules_usage(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: SourcesRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    """Pour chaque règle de tags, les objets qu'elle tague actuellement."""
    key, _ref = await _ensure_index(conn, crypto, workdir, space_id, branch_id)
    sources = await db_all_source_entries(conn, key)
    rule_ids = sorted({t.rule for e in sources for o in e.objects for t in o.tags})
    out: dict[str, object] = {}
    for rule_id in rule_ids:
        count, inherited, examples = _rule_objects(sources, rule_id)
        out[rule_id] = {"objects": count, "inherited": inherited, "examples": examples}
    return {"rules": out}


@router.post("/tag-rules/preview")
async def tag_rule_preview(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: SourcesRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: TagRuleBody,
) -> dict[str, object]:
    """Ce qu'une règle taguerait, avant de l'enregistrer : elle est appliquée
    seule (et active) aux objets de l'index, sans rien écrire."""
    preview_id = "preview"
    rule, errors = parse_rule(preview_id, {**body.model_dump(), "enabled": True})
    if rule is None:
        raise ApiError("tag_rule_invalid", status_code=400, fields=errors)
    key, _ref = await _ensure_index(conn, crypto, workdir, space_id, branch_id)
    entries = await db_all_source_entries(conn, key)
    # Les retraits déjà faits valent aussi pour l'aperçu (ils sont dans l'index).
    tagged = apply_tag_rules(entries, [rule], index_removals(entries))
    count, inherited, examples = _rule_objects(tagged, preview_id)
    return {"objects": count, "inherited": inherited, "examples": examples}


# Le graphe porte aussi l'index inverse des références (entrantes).
_SPACE_CACHE: dict[tuple[IndexKey, str, str], ReferenceGraph] = {}
_SPACE_CACHE_MAX = 32


async def _reference_graph(conn: ConnDep, key: IndexKey, mode: str) -> ReferenceGraph:
    signature = await db_index_signature(conn, key) or ""
    cache_key = (key, signature, mode)
    cached = _SPACE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    graph = ReferenceGraph(await db_all_source_entries(conn, key), mode)
    if len(_SPACE_CACHE) >= _SPACE_CACHE_MAX:
        _SPACE_CACHE.clear()
    _SPACE_CACHE[cache_key] = graph
    return graph


async def _space_objects(conn: ConnDep, key: IndexKey, mode: str) -> SpaceObjects:
    return (await _reference_graph(conn, key, mode)).space


def _json_object(text: str) -> dict[str, object]:
    """Un schéma illisible s'affiche vide plutôt que de faire échouer l'aperçu."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


async def _forge_link(
    conn: ConnDep, manifest_text: str | None, path: str, line: int | None
) -> str | None:
    """« Ouvrir dans la forge » : seulement pour une source Git dont la
    connexion est connue (absent pour une base ou un contrat déposé)."""
    if not manifest_text or not path:
        return None
    try:
        manifest = parse_manifest(manifest_text)
    except ManifestError:
        return None
    if manifest.type != GIT_SOURCE_TYPE:
        return None
    connection = await db_get_connection_by_slug(conn, manifest.connection)
    if connection is None:
        return None
    return forge_file_url(
        connection.forge,
        connection.url,
        str(manifest.parameters.get("repository") or ""),
        str(manifest.parameters.get("branch") or ""),
        path,
        line,
    )


@router.get("/{slug}/object")
async def get_explorer_object(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: SourcesRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    file: str,
    mode: str = DEFAULT_MODE,
) -> dict[str, object]:
    """Détail d'un objet : son schéma (lu sans matérialiser la vue) et ses
    références résolues dans tout l'espace."""
    if not is_valid_source_slug(slug) or not is_schema_file(file):
        raise ApiError("raw_explorer_object_not_found", status_code=404, file=file)
    _check_mode(mode)
    key, branch_ref = await _ensure_index(conn, crypto, workdir, space_id, branch_id)
    entries = await db_get_source_entries(conn, key, slug)
    obj = next((o for o in entries.objects if o.file == file), None) if entries else None
    if entries is None or obj is None:
        raise ApiError("raw_explorer_object_not_found", status_code=404, file=file)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        text = await workdir.read_file(
            space.id, branch_ref, space.repo_url, username, token, f"sources/{slug}/raw/{file}"
        )
        manifest_text = await workdir.read_file(
            space.id, branch_ref, space.repo_url, username, token, f"sources/{slug}/manifest.yaml"
        )
        asset_text = await workdir.read_file(
            space.id, branch_ref, space.repo_url, username, token, f"sources/{slug}/assets/{file}"
        )
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    if text is None:
        raise ApiError("raw_explorer_object_not_found", status_code=404, file=file)
    schema = _json_object(text)
    asset = _json_object(asset_text) if asset_text is not None else None
    graph = await _reference_graph(conn, key, mode)
    detail = object_detail(schema, text, obj, entries, graph.space, asset)
    detail["incoming"] = graph.incoming((slug, file))
    # Un tag hérité ouvre la classe dont il vient (son dossier dépend du mode).
    detail["tags"] = [
        {
            **_tag_payload(t),
            "via_target": graph.target((t.via.split("/", 1)[0], t.via.split("/", 1)[1]))
            if "/" in t.via
            else None,
        }
        for t in obj.tags
    ]
    # Retirer un tag le retire aussi des classes qui ne le tiennent que de cet
    # objet : l'écran l'annonce avant de confirmer.
    detail["tag_dependents"] = {
        t.tag: dependents_losing(graph.sources, f"{slug}/{file}", t.tag) for t in obj.tags
    }
    detail["removed_tags"] = [_tag_payload(t) for t in obj.removed_tags]
    origin = detail["origin"]
    detail["forge_url"] = await _forge_link(conn, manifest_text, obj.origin, origin["line"])
    return detail


@router.get("/{slug}/object/history")
async def get_explorer_object_history(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: SourcesRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    file: str,
) -> dict[str, object]:
    """Commits publiés de la branche qui ont modifié le fichier schéma, du plus
    récent au plus ancien (un brouillon n'a pas d'histoire)."""
    if not is_valid_source_slug(slug) or not is_schema_file(file):
        raise ApiError("raw_explorer_object_not_found", status_code=404, file=file)
    branch_ref = await _branch_ref(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    rel = f"sources/{slug}/raw/{file}"
    try:
        commits = await workdir.file_history(
            space.id, branch_ref, space.repo_url, username, token, rel, HISTORY_LIMIT
        )
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    manager = workdir.manager
    out: list[dict[str, object]] = []
    for c in commits:
        # Version au commit et juste avant (parent) : lues dans les objets git de
        # la copie, que file_history vient d'aligner (pas de fetch par commit).
        after = await manager.local_file_at(space.id, branch_ref, c.sha, rel)
        before = await manager.local_file_at(space.id, branch_ref, f"{c.sha}^", rel)
        changes = schema_changes(before, after)
        if not changes:
            continue  # aucun changement lisible (critère 6)
        out.append(
            {
                "sha": c.sha,
                "author": c.author,
                "date": c.date,
                "message": c.message,
                "changes": changes,
            }
        )
    return {"commits": out}


@router.get("/{slug}/document")
async def get_explorer_document(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: SourcesRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    path: str,
    mode: str = DEFAULT_MODE,
) -> dict[str, object]:
    """Un document de `raw/docs/` : titre, sections, objets cités."""
    if not is_valid_source_slug(slug):
        raise ApiError("source_not_found", status_code=404, slug=slug)
    _check_mode(mode)
    key, _branch_ref = await _ensure_index(conn, crypto, workdir, space_id, branch_id)
    entries = await db_get_source_entries(conn, key, slug)
    doc = next((d for d in entries.documents if d.path == path), None) if entries else None
    if doc is None:
        raise ApiError("raw_explorer_object_not_found", status_code=404, file=path)
    return document_detail(doc, await _space_objects(conn, key, mode))


@router.get("/{slug}/graph")
async def get_explorer_graph(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: SourcesRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    file: str,
    depth: int = 1,
    mode: str = DEFAULT_MODE,
) -> dict[str, object]:
    """Graphe des références d'un objet (sortantes à droite, entrantes à gauche,
    toutes sources de l'espace), jusqu'à la profondeur demandée (1 à 3)."""
    if not is_valid_source_slug(slug) or not is_schema_file(file):
        raise ApiError("raw_explorer_object_not_found", status_code=404, file=file)
    _check_mode(mode)
    key, _branch_ref = await _ensure_index(conn, crypto, workdir, space_id, branch_id)
    graph = (await _reference_graph(conn, key, mode)).build((slug, file), depth)
    if graph is None:
        raise ApiError("raw_explorer_object_not_found", status_code=404, file=file)
    return graph


@router.get("/{slug}/document/content")
async def get_explorer_document_content(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: SourcesRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    path: str,
) -> dict[str, object]:
    """Texte Markdown d'un document de `raw/docs/` (rendu sûr côté écran) et ses
    ressources non copiées (signalées par l'index)."""
    if not is_valid_source_slug(slug) or ".." in path.split("/") or path.startswith("/"):
        raise ApiError("raw_explorer_object_not_found", status_code=404, file=path)
    key, branch_ref = await _ensure_index(conn, crypto, workdir, space_id, branch_id)
    entries = await db_get_source_entries(conn, key, slug)
    doc = next((d for d in entries.documents if d.path == path), None) if entries else None
    if doc is None:
        raise ApiError("raw_explorer_object_not_found", status_code=404, file=path)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        text = await workdir.read_file(
            space.id, branch_ref, space.repo_url, username, token, f"sources/{slug}/raw/docs/{path}"
        )
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    if text is None:
        raise ApiError("raw_explorer_object_not_found", status_code=404, file=path)
    return {"path": doc.path, "title": doc.title, "markdown": text}
