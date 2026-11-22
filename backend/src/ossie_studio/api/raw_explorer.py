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
import uuid
from pathlib import Path
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends

from ossie_studio.api.branches import space_credentials
from ossie_studio.api.common import SourcesRead
from ossie_studio.crypto import CryptoService
from ossie_studio.deps import ConnDep, get_crypto, get_draft_workdir
from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.errors import ApiError
from ossie_studio.parsers.base import STATUT_ERREUR
from ossie_studio.sources.curation import read_review_point
from ossie_studio.sources.explorer import (
    DEFAULT_MODE,
    MODES,
    ExplorerDocument,
    ExplorerObject,
    SourceEntries,
    SourceIndex,
    collect_source_entries,
    folder_item_count,
    index_from_entries,
    list_source_slugs,
    search_objects,
    sort_objects,
    subtree_file_counts,
)
from ossie_studio.sources.explorer_store import (
    IndexKey,
    db_get_source_entries,
    db_index_signature,
    db_list_indexed_sources,
    db_replace_index,
)
from ossie_studio.sources.paths import is_valid_source_slug
from ossie_studio.sources.scan_service import ScanFile, db_list_scan_files
from ossie_studio.spaces.branch_service import db_get_branch
from ossie_studio.spaces.workdir import WorkdirError

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/raw-explorer")

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
    key = IndexKey(space_id=space_id, branch_id=branch_id, user_id=workdir.user_id, view=view)
    if await db_index_signature(conn, key) == signature:
        return key, branch_ref
    lock = _BUILD_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        if await db_index_signature(conn, key) == signature:
            return key, branch_ref

        def read(wc: Path) -> list[SourceEntries]:
            return [
                collect_source_entries(wc, slug, review_base=read_review_point(wc, slug))
                for slug in list_source_slugs(wc)
            ]

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
