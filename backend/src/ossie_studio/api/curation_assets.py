"""API de la page « Curation » : assets retenus et rédaction de leurs descriptions.

Lecture seule ici : l'écriture des descriptions passe par la route existante de
l'enrichissement (`PUT …/sources/{slug}/curation/enrich`, mêmes droits, même
journal), pour ne pas dupliquer la règle (ticket docflow aeb2e429).
Accès : `curation:read`, comme l'enrichissement.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from ossie_studio.api.branches import space_credentials
from ossie_studio.api.common import CurationRead
from ossie_studio.crypto import CryptoService
from ossie_studio.deps import ConnDep, get_crypto, get_draft_workdir
from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.errors import ApiError
from ossie_studio.sources.curation import is_schema_file
from ossie_studio.sources.curation_assets import (
    AssetSummary,
    asset_view,
    read_assets,
    summarize,
)
from ossie_studio.sources.explorer import DEFAULT_MODE
from ossie_studio.sources.explorer_graph import ReferenceGraph
from ossie_studio.sources.explorer_service import ensure_index, reference_graph
from ossie_studio.sources.paths import ASSETS_DIR, is_valid_source_slug, source_rel_dir
from ossie_studio.spaces.branch_service import db_get_branch
from ossie_studio.spaces.workdir import WorkdirError

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/curation-assets")

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]
WorkdirDep = Annotated[DraftWorkdir, Depends(get_draft_workdir)]

# Commits montrés dans l'onglet Historique d'un asset.
HISTORY_LIMIT = 20


def _workdir_error(exc: WorkdirError) -> ApiError:
    return ApiError(exc.code, status_code=404 if exc.code == "branch_not_found" else 502)


async def _load(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> tuple[dict[tuple[str, str], dict[str, Any]], ReferenceGraph]:
    """Assets du brouillon de la personne et graphe de l'explorateur (même vue)."""
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        key, signature = await ensure_index(
            conn, workdir, space, branch_id, branch.git_ref, username, token
        )
        assets = await workdir.read_branch(
            space.id, branch.git_ref, space.repo_url, username, token, read_assets
        )
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    graph = await reference_graph(conn, key, DEFAULT_MODE, signature)
    return assets, graph


def _summary_payload(summary: AssetSummary) -> dict[str, object]:
    return asdict(summary)


@router.get("")
async def list_curation_assets(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: CurationRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    assets, graph = await _load(conn, crypto, workdir, space_id, branch_id)
    labels = {s.slug: s.label for s in graph.sources}
    summaries = summarize(assets, graph)
    sources = sorted({s.source for s in summaries})
    return {
        "sources": [{"slug": slug, "label": labels.get(slug) or slug} for slug in sources],
        "assets": [_summary_payload(s) for s in summaries],
    }


def _check(source: str, path: str) -> None:
    if not is_valid_source_slug(source) or not is_schema_file(path):
        raise ApiError("asset_not_retained", status_code=404, path=path)


@router.get("/asset")
async def get_curation_asset(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: CurationRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    source: str,
    path: str,
) -> dict[str, object]:
    _check(source, path)
    assets, graph = await _load(conn, crypto, workdir, space_id, branch_id)
    asset = assets.get((source, path))
    if asset is None:
        raise ApiError("asset_not_retained", status_code=404, path=path)
    view = asset_view((source, path), asset, assets, graph)
    return {
        **_summary_payload(view.summary),
        "description": view.description,
        "business_name": view.business_name,
        "properties": [asdict(p) for p in view.properties],
    }


@router.get("/asset/history")
async def get_curation_asset_history(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: CurationRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    source: str,
    path: str,
) -> dict[str, object]:
    """Commits publiés de la branche qui ont touché l'asset (un brouillon n'a
    pas d'histoire), du plus récent au plus ancien."""
    _check(source, path)
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    space, username, token = await space_credentials(conn, crypto, space_id)
    rel = str(Path(source_rel_dir(source)) / ASSETS_DIR / path)
    try:
        commits = await workdir.file_history(
            space.id, branch.git_ref, space.repo_url, username, token, rel, HISTORY_LIMIT
        )
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    return {
        "commits": [
            {"sha": c.sha, "author": c.author, "date": c.date, "message": c.message}
            for c in commits
        ]
    }
