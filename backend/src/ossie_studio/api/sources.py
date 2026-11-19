from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.branches import space_credentials
from ossie_studio.api.common import SourcesCollect, SourcesDeclare, SourcesRead
from ossie_studio.connections.service import (
    db_get_connection_by_slug,
    resolve_connection_token,
)
from ossie_studio.crypto import CryptoService
from ossie_studio.databases.service import db_get_db_connection_by_slug
from ossie_studio.deps import (
    ConnDep,
    get_crypto,
    get_http_client_factory,
    get_source_collector,
    get_source_downloader,
    get_workdir_manager,
)
from ossie_studio.errors import ApiError
from ossie_studio.forges.base import ForgeError
from ossie_studio.forges.registry import get_strategy
from ossie_studio.sources.collect import CollectionError, CollectReport, SourceCollector
from ossie_studio.sources.collections_service import (
    CollectionRun,
    db_list_collections,
    db_record_collection,
)
from ossie_studio.sources.download import SourceDownloader
from ossie_studio.sources.manifest import (
    DB_TYPES,
    GIT_TYPE,
    ManifestError,
    SourceManifest,
    manifest_from_mapping,
)
from ossie_studio.sources.paths import is_valid_source_slug
from ossie_studio.sources.service import (
    SourceEntry,
    SourceExistsError,
    SourceNotFoundError,
    declare_source,
    delete_source,
    list_sources,
    update_source,
)
from ossie_studio.spaces.branch_service import db_get_branch
from ossie_studio.spaces.workdir import WorkdirError, WorkdirManager

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/sources")

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]
WorkdirDep = Annotated[WorkdirManager, Depends(get_workdir_manager)]
DownloaderDep = Annotated[SourceDownloader, Depends(get_source_downloader)]
CollectorDep = Annotated[SourceCollector, Depends(get_source_collector)]
HttpFactoryDep = Annotated[Callable[[], httpx.AsyncClient], Depends(get_http_client_factory)]


class SourceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = ""
    label: str = ""
    type: str = ""
    connexion: str = ""  # identifiant de la connexion (forge ou base)
    parametres: dict[str, Any] = {}


class SourceUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = ""
    connexion: str = ""
    parametres: dict[str, Any] = {}


def _workdir_http_error(exc: WorkdirError) -> ApiError:
    return ApiError(exc.code, status_code=404 if exc.code == "branch_not_found" else 502)


async def _branch_slug(conn: ConnDep, space_id: uuid.UUID, branch_id: uuid.UUID) -> str:
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    return branch.slug


async def _resolve_connection(
    conn: ConnDep, source_type: str, connexion: str
) -> dict[str, object]:
    """Vérifie que la connexion (désignée par son slug) existe et est du bon genre
    pour le type de source, et rend de quoi l'afficher. Jamais de valeur secrète."""
    slug = connexion.strip()
    if not slug:
        raise ApiError("invalid_request", status_code=400, fields=["connexion_required"])
    if source_type == GIT_TYPE:
        forge = await db_get_connection_by_slug(conn, slug)
        if forge is None:
            raise ApiError("invalid_request", status_code=400, fields=["connexion_invalid"])
        return {"slug": forge.slug, "label": forge.label, "kind": "forge", "forge": forge.forge}
    # Type base : connexion de base du même moteur.
    db = await db_get_db_connection_by_slug(conn, slug)
    if db is None or db.engine != source_type:
        raise ApiError("invalid_request", status_code=400, fields=["connexion_invalid"])
    return {"slug": db.slug, "label": db.label, "kind": "db", "engine": db.engine}


async def _build_manifest(
    conn: ConnDep,
    slug: str,
    label: str,
    source_type: str,
    connexion: str,
    parametres: dict[str, Any],
) -> tuple[SourceManifest, dict[str, object]]:
    if not is_valid_source_slug(slug):
        raise ApiError("invalid_request", status_code=400, fields=["slug_invalid"])
    connection_info = await _resolve_connection(conn, source_type, connexion)
    try:
        manifest = manifest_from_mapping(
            {
                "version": 1,
                "slug": slug,
                "label": label,
                "type": source_type,
                "connexion": connection_info["slug"],
                "parametres": parametres,
            }
        )
    except ManifestError as exc:
        raise ApiError("invalid_request", status_code=400, fields=exc.fields) from None
    return manifest, connection_info


async def _validate_git_branch(
    conn: ConnDep,
    crypto: CryptoService,
    http_client_factory: Callable[[], httpx.AsyncClient],
    connexion: str,
    depot: str,
    branche: str,
) -> None:
    """Une source Git suit une **branche** du dépôt source : on refuse une branche
    inexistante (et donc un nom de tag ou de commit)."""
    connection = await db_get_connection_by_slug(conn, connexion.strip())
    assert connection is not None  # déjà validée par _resolve_connection
    token = (await resolve_connection_token(conn, crypto, connection)).reveal()
    strategy = get_strategy(connection.forge)
    async with http_client_factory() as client:
        try:
            branches = await strategy.list_branches(client, connection.url, token, depot)
        except ForgeError as exc:
            raise ApiError(exc.code, status_code=400) from None
    if branche not in branches:
        raise ApiError("invalid_request", status_code=400, fields=["branche_not_found"])


def _entry_payload(entry: SourceEntry, connection: dict[str, object] | None) -> dict[str, object]:
    if entry.manifest is None:
        return {"slug": entry.slug, "ok": False, "error": entry.error}
    manifest = entry.manifest
    return {
        "slug": manifest.slug,
        "ok": True,
        "label": manifest.label,
        "type": manifest.type,
        "connexion": connection or {"slug": manifest.connexion, "kind": "missing"},
        "parametres": manifest.parametres,
    }


async def _connection_display(
    conn: ConnDep, source_type: str, connexion: str
) -> dict[str, object] | None:
    try:
        return await _resolve_connection(conn, source_type, connexion)
    except ApiError:
        return None


@router.get("")
async def list_branch_sources(
    conn: ConnDep,
    crypto: CryptoDep,
    _: SourcesRead,
    workdir: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    slug = await _branch_slug(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        entries = await list_sources(workdir, space, slug, username, token)
    except WorkdirError as exc:
        raise _workdir_http_error(exc) from None
    sources: list[dict[str, object]] = []
    for entry in entries:
        connection = (
            await _connection_display(conn, entry.manifest.type, entry.manifest.connexion)
            if entry.manifest is not None
            else None
        )
        sources.append(_entry_payload(entry, connection))
    return {"sources": sources}


@router.post("", status_code=201)
async def declare_branch_source(
    conn: ConnDep,
    crypto: CryptoDep,
    user: SourcesDeclare,
    workdir: WorkdirDep,
    http_client_factory: HttpFactoryDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: SourceBody,
) -> dict[str, object]:
    slug = await _branch_slug(conn, space_id, branch_id)
    if body.type not in (GIT_TYPE, *DB_TYPES):
        raise ApiError("invalid_request", status_code=400, fields=["type_unknown"])
    manifest, connection = await _build_manifest(
        conn, body.slug.strip(), body.label, body.type, body.connexion, body.parametres
    )
    if manifest.type == GIT_TYPE:
        await _validate_git_branch(
            conn,
            crypto,
            http_client_factory,
            body.connexion,
            str(manifest.parametres["depot"]),
            str(manifest.parametres["branche"]),
        )
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        await declare_source(
            workdir, space, slug, username, token, user.email, user.email, manifest
        )
    except SourceExistsError:
        raise ApiError("source_slug_taken", status_code=409, slug=manifest.slug) from None
    except WorkdirError as exc:
        raise _workdir_http_error(exc) from None
    return _entry_payload(SourceEntry(manifest.slug, manifest, None), connection)


async def _existing_type(
    conn: ConnDep,
    crypto: CryptoService,
    workdir: WorkdirManager,
    space_id: uuid.UUID,
    branch_slug: str,
    slug: str,
) -> str:
    space, username, token = await space_credentials(conn, crypto, space_id)
    entries = await list_sources(workdir, space, branch_slug, username, token)
    for entry in entries:
        if entry.slug == slug and entry.manifest is not None:
            return entry.manifest.type
    raise ApiError("source_not_found", status_code=404, slug=slug)


@router.put("/{slug}")
async def update_branch_source(
    conn: ConnDep,
    crypto: CryptoDep,
    user: SourcesDeclare,
    workdir: WorkdirDep,
    http_client_factory: HttpFactoryDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    body: SourceUpdateBody,
) -> dict[str, object]:
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    source_type = await _existing_type(conn, crypto, workdir, space_id, branch_slug, slug)
    manifest, connection = await _build_manifest(
        conn, slug, body.label, source_type, body.connexion, body.parametres
    )
    if manifest.type == GIT_TYPE:
        await _validate_git_branch(
            conn,
            crypto,
            http_client_factory,
            body.connexion,
            str(manifest.parametres["depot"]),
            str(manifest.parametres["branche"]),
        )
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        await update_source(
            workdir, space, branch_slug, username, token, user.email, user.email, manifest
        )
    except SourceNotFoundError:
        raise ApiError("source_not_found", status_code=404, slug=slug) from None
    except WorkdirError as exc:
        raise _workdir_http_error(exc) from None
    return _entry_payload(SourceEntry(manifest.slug, manifest, None), connection)


@router.delete("/{slug}", status_code=204)
async def delete_branch_source(
    conn: ConnDep,
    crypto: CryptoDep,
    user: SourcesDeclare,
    workdir: WorkdirDep,
    downloader: DownloaderDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
) -> None:
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    if not is_valid_source_slug(slug):
        raise ApiError("source_not_found", status_code=404, slug=slug)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        await delete_source(
            workdir, space, branch_slug, username, token, user.email, user.email, slug
        )
    except SourceNotFoundError:
        raise ApiError("source_not_found", status_code=404, slug=slug) from None
    except WorkdirError as exc:
        raise _workdir_http_error(exc) from None
    # Le code descendu sur le volume des sources part avec la source.
    await downloader.remove(space_id, slug)


def _collect_error(code: str) -> ApiError:
    if code == "branch_behind":
        return ApiError("collect_branch_behind", status_code=409)
    if code == "branch_not_found":
        return ApiError("branch_not_found", status_code=404)
    return ApiError("collect_failed", status_code=502, cause=code)


def _report_payload(report: CollectReport) -> dict[str, object]:
    return {
        "source_commit": report.source_commit,
        "local_commit": report.local_commit,
        "added": report.added,
        "modified": report.modified,
        "deleted": report.deleted,
        "unchanged": report.unchanged,
        "read": report.read,
        "retained": report.retained,
        "duration_ms": report.duration_ms,
        "errors": [{"source": e.source, "cause": e.cause} for e in report.errors],
    }


def _run_payload(run: CollectionRun) -> dict[str, object]:
    return {
        "id": str(run.id),
        "author": run.author_email,
        "source_commit": run.source_commit,
        "local_commit": run.local_commit,
        "added": run.added,
        "modified": run.modified,
        "deleted": run.deleted,
        "unchanged": run.unchanged,
        "error_count": run.error_count,
        "duration_ms": run.duration_ms,
        "created_at": run.created_at.isoformat(),
    }


@router.post("/{slug}/collect")
async def collect_branch_source(
    conn: ConnDep,
    crypto: CryptoDep,
    user: SourcesCollect,
    workdir: WorkdirDep,
    collector: CollectorDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    force: bool = False,
) -> dict[str, object]:
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    branch_slug = branch.slug
    space, space_username, space_token = await space_credentials(conn, crypto, space_id)
    entries = await list_sources(workdir, space, branch_slug, space_username, space_token)
    entry = next((e for e in entries if e.slug == slug and e.manifest is not None), None)
    if entry is None or entry.manifest is None:
        raise ApiError("source_not_found", status_code=404, slug=slug)
    manifest = entry.manifest
    if manifest.type != GIT_TYPE:
        # Les sources de base se lisent par leur catalogue (epic Parseurs).
        raise ApiError("invalid_request", status_code=400, fields=["collect_git_only"])
    connection = await db_get_connection_by_slug(conn, manifest.connexion)
    if connection is None:
        raise ApiError("invalid_request", status_code=400, fields=["connexion_invalid"])
    strategy = get_strategy(connection.forge)
    source_token = (await resolve_connection_token(conn, crypto, connection)).reveal()
    try:
        report = await collector.collect(
            space=space,
            branch_slug=branch_slug,
            branche_label=branch_slug,
            space_username=space_username,
            space_token=space_token,
            source_slug=slug,
            source_clone_url=strategy.git_clone_url(
                connection.url, str(manifest.parametres.get("depot", ""))
            ),
            source_branch=str(manifest.parametres.get("branche", "")),
            source_username=strategy.git_username(),
            source_token=source_token,
            author_name=user.email,
            author_email=user.email,
            force=force,
        )
    except CollectionError as exc:
        raise _collect_error(exc.code) from None
    await db_record_collection(
        conn,
        space_id=space_id,
        branch_slug=branch_slug,
        source_slug=slug,
        author_email=user.email,
        report=report,
    )
    return _report_payload(report)


@router.get("/{slug}/collections")
async def list_source_collections(
    conn: ConnDep,
    _: SourcesRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
) -> dict[str, object]:
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    runs = await db_list_collections(
        conn, space_id=space_id, branch_slug=branch.slug, source_slug=slug
    )
    return {"collections": [_run_payload(r) for r in runs]}
