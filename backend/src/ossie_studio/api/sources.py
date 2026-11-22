from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any

import asyncpg
import httpx
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.branches import space_credentials
from ossie_studio.api.common import SourcesCollect, SourcesDeclare, SourcesRead
from ossie_studio.config import Settings
from ossie_studio.connections.service import (
    db_get_connection_by_slug,
    resolve_connection_token,
)
from ossie_studio.crypto import CryptoService
from ossie_studio.databases.registry import get_engine
from ossie_studio.databases.service import db_get_db_connection_by_slug
from ossie_studio.deps import (
    ConnDep,
    get_crypto,
    get_draft_workdir,
    get_http_client_factory,
    get_settings,
    get_source_collector,
    get_source_downloader,
)
from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.errors import ApiError
from ossie_studio.forges.base import ForgeError
from ossie_studio.forges.registry import get_strategy
from ossie_studio.models import User
from ossie_studio.parsers.openapi import (
    Analysis,
    OpenApiError,
    is_valid_version_name,
)
from ossie_studio.parsers.openapi import (
    analyze as analyze_contract,
)
from ossie_studio.parsers.params_service import db_get_plugin_params
from ossie_studio.parsers.registry import conflicting_glob, unknown_code_parseur
from ossie_studio.secretstore.service import resolve_secret_value
from ossie_studio.sources.collect import CollectionError, CollectReport, SourceCollector
from ossie_studio.sources.contracts import (
    ContractEntry,
    ContractExistsError,
    ContractNotFoundError,
    ContractView,
    deposit_contract_fn,
    read_contract_bytes,
    read_contracts_view,
    remove_contract_fn,
)
from ossie_studio.sources.download import SourceDownloader
from ossie_studio.sources.jobs import DONE, FAILED, CollectJobs, JobKey
from ossie_studio.sources.manifest import (
    DB_TYPES,
    GIT_TYPE,
    MANIFEST_VERSION,
    OPENAPI_TYPE,
    ManifestError,
    SourceManifest,
    manifest_from_mapping,
)
from ossie_studio.sources.paths import is_valid_source_slug
from ossie_studio.sources.progress import CollectProgress
from ossie_studio.sources.scan_service import (
    ScanFile,
    db_delete_scan_files,
    db_list_scan_files,
    db_replace_scan_files,
)
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
from ossie_studio.spaces.workdir import WorkdirError

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/sources")

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]
# Copie vue par la personne connectée : lectures avec son brouillon, écritures
# dans son brouillon (jamais de commit).
WorkdirDep = Annotated[DraftWorkdir, Depends(get_draft_workdir)]
DownloaderDep = Annotated[SourceDownloader, Depends(get_source_downloader)]
CollectorDep = Annotated[SourceCollector, Depends(get_source_collector)]
HttpFactoryDep = Annotated[Callable[[], httpx.AsyncClient], Depends(get_http_client_factory)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


class SourceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = ""
    label: str = ""
    type: str = ""
    connection: str = ""  # identifiant de la connexion (forge ou base)
    parameters: dict[str, Any] = {}


class SourceUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = ""
    connection: str = ""
    parameters: dict[str, Any] = {}


class AnalyzeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = ""
    content: str = ""  # contenu texte du contrat (YAML ou JSON)


class DepositBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = ""
    content: str = ""
    version: str = ""
    replace: bool = False


def _str_list(value: object) -> list[str] | None:
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [str(v) for v in value]
    return None


def _workdir_http_error(exc: WorkdirError) -> ApiError:
    return ApiError(exc.code, status_code=404 if exc.code == "branch_not_found" else 502)


async def _branch_slug(conn: ConnDep, space_id: uuid.UUID, branch_id: uuid.UUID) -> str:
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    return branch.git_ref


async def _resolve_connection(
    conn: ConnDep, source_type: str, connection: str
) -> dict[str, object]:
    """Vérifie que la connexion (désignée par son slug) existe et est du bon genre
    pour le type de source, et rend de quoi l'afficher. Jamais de valeur secrète."""
    slug = connection.strip()
    if not slug:
        raise ApiError("invalid_request", status_code=400, fields=["connection_required"])
    if source_type == GIT_TYPE:
        forge = await db_get_connection_by_slug(conn, slug)
        if forge is None:
            raise ApiError("invalid_request", status_code=400, fields=["connection_invalid"])
        return {"slug": forge.slug, "label": forge.label, "kind": "forge", "forge": forge.forge}
    # Type base : connexion de base du même moteur.
    db = await db_get_db_connection_by_slug(conn, slug)
    if db is None or db.engine != source_type:
        raise ApiError("invalid_request", status_code=400, fields=["connection_invalid"])
    return {"slug": db.slug, "label": db.label, "kind": "db", "engine": db.engine}


async def _build_manifest(
    conn: ConnDep,
    slug: str,
    label: str,
    source_type: str,
    connection: str,
    parameters: dict[str, Any],
    triage: list[dict[str, Any]] | None = None,
) -> tuple[SourceManifest, dict[str, object] | None]:
    if not is_valid_source_slug(slug):
        raise ApiError("invalid_request", status_code=400, fields=["slug_invalid"])
    # Source par dépôt : pas de connexion à résoudre.
    connection_info: dict[str, object] | None = None
    if source_type != OPENAPI_TYPE:
        connection_info = await _resolve_connection(conn, source_type, connection)
    try:
        manifest = manifest_from_mapping(
            {
                "version": MANIFEST_VERSION,
                "slug": slug,
                "label": label,
                "type": source_type,
                "connection": connection_info["slug"] if connection_info is not None else "",
                "parameters": parameters,
                "triage": triage or [],
            }
        )
    except ManifestError as exc:
        raise ApiError("invalid_request", status_code=400, fields=exc.fields) from None
    return manifest, connection_info


def _check_git_parsers(manifest: SourceManifest) -> None:
    """Refuse un parseur inconnu (en le nommant) ou deux parseurs qui se disputent
    le même motif (en nommant le motif)."""
    parsers = _str_list(manifest.parameters.get("parsers")) or []
    unknown = unknown_code_parseur(parsers)
    if unknown is not None:
        raise ApiError("parseur_unknown", status_code=400, id=unknown)
    conflict = conflicting_glob(parsers)
    if conflict is not None:
        raise ApiError("parseur_conflict", status_code=400, pattern=conflict)


async def _validate_git_branch(
    conn: ConnDep,
    crypto: CryptoService,
    http_client_factory: Callable[[], httpx.AsyncClient],
    connection_slug: str,
    repository: str,
    branch: str,
) -> None:
    """Une source Git suit une **branche** du dépôt source : on refuse une branche
    inexistante (et donc un nom de tag ou de commit)."""
    connection = await db_get_connection_by_slug(conn, connection_slug.strip())
    assert connection is not None  # déjà validée par _resolve_connection
    token = (await resolve_connection_token(conn, crypto, connection)).reveal()
    strategy = get_strategy(connection.forge)
    async with http_client_factory() as client:
        try:
            branches = await strategy.list_branches(client, connection.url, token, repository)
        except ForgeError as exc:
            raise ApiError(exc.code, status_code=400) from None
    if branch not in branches:
        raise ApiError("invalid_request", status_code=400, fields=["branch_not_found"])


def _entry_payload(entry: SourceEntry, connection: dict[str, object] | None) -> dict[str, object]:
    if entry.manifest is None:
        return {"slug": entry.slug, "ok": False, "error": entry.error}
    manifest = entry.manifest
    if manifest.type == OPENAPI_TYPE:
        # Source par dépôt : pas de connexion (ni manquante, ni à afficher).
        shown: dict[str, object] = {"kind": "none"}
    else:
        shown = connection or {"slug": manifest.connection, "kind": "missing"}
    return {
        "slug": manifest.slug,
        "ok": True,
        "label": manifest.label,
        "type": manifest.type,
        "connection": shown,
        "parameters": manifest.parameters,
    }


async def _connection_display(
    conn: ConnDep, source_type: str, connection: str
) -> dict[str, object] | None:
    try:
        return await _resolve_connection(conn, source_type, connection)
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
            await _connection_display(conn, entry.manifest.type, entry.manifest.connection)
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
    if body.type not in (GIT_TYPE, *DB_TYPES, OPENAPI_TYPE):
        raise ApiError("invalid_request", status_code=400, fields=["type_unknown"])
    manifest, connection = await _build_manifest(
        conn, body.slug.strip(), body.label, body.type, body.connection, body.parameters
    )
    if manifest.type == GIT_TYPE:
        _check_git_parsers(manifest)
        await _validate_git_branch(
            conn,
            crypto,
            http_client_factory,
            body.connection,
            str(manifest.parameters["repository"]),
            str(manifest.parameters["branch"]),
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


async def _existing_manifest(
    conn: ConnDep,
    crypto: CryptoService,
    workdir: DraftWorkdir,
    space_id: uuid.UUID,
    branch_slug: str,
    slug: str,
) -> SourceManifest:
    space, username, token = await space_credentials(conn, crypto, space_id)
    entries = await list_sources(workdir, space, branch_slug, username, token)
    for entry in entries:
        if entry.slug == slug and entry.manifest is not None:
            return entry.manifest
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
    existing = await _existing_manifest(conn, crypto, workdir, space_id, branch_slug, slug)
    manifest, connection = await _build_manifest(
        conn,
        slug,
        body.label,
        existing.type,
        body.connection,
        body.parameters,
        # Les règles de tri se gèrent à part : une édition de source les préserve.
        triage=existing.triage,
    )
    if manifest.type == GIT_TYPE:
        _check_git_parsers(manifest)
        await _validate_git_branch(
            conn,
            crypto,
            http_client_factory,
            body.connection,
            str(manifest.parameters["repository"]),
            str(manifest.parameters["branch"]),
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
    # Le code descendu sur le volume des sources et l'index de collecte partent
    # avec la source.
    await downloader.remove(space_id, slug)
    await db_delete_scan_files(conn, space_id=space_id, branch_slug=branch_slug, source_slug=slug)


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
        "errors": [
            {"source": e.source, "cause": e.cause, "detail": e.detail} for e in report.errors
        ],
    }


# Une collecte courte répond directement (rapport) ; au-delà, 202 et l'écran
# suit l'état par GET /collect — la collecte continue même si la page est
# rechargée ou fermée.
COLLECT_WAIT_SECONDS = 20.0


def _job_key(space_id: uuid.UUID, branch_id: uuid.UUID, user: User, slug: str) -> JobKey:
    return (space_id, branch_id, user.id, slug)


@router.post("/{slug}/collect")
async def collect_branch_source(
    request: Request,
    response: Response,
    conn: ConnDep,
    crypto: CryptoDep,
    user: SourcesCollect,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    force: bool = False,
) -> dict[str, object]:
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    state = request.app.state
    branch_ref = branch.git_ref

    async def run(progress: CollectProgress) -> dict[str, object]:
        # Connexion propre à la tâche : celle de la requête se ferme avec elle.
        async with state.pool.acquire() as job_conn:
            workdir = DraftWorkdir(
                state.workdir_manager,
                state.draft_views,
                state.pool,
                user.id,
                branch_id,
                conn=job_conn,
            )
            return await _collect(
                job_conn,
                crypto,
                user,
                workdir,
                state.source_collector,
                space_id,
                branch_ref,
                slug,
                force,
                progress,
            )

    jobs: CollectJobs = state.collect_jobs
    job = jobs.start(_job_key(space_id, branch_id, user, slug), run)
    assert job.task is not None
    # asyncio.wait n'annule pas la tâche si le client se déconnecte.
    await asyncio.wait({job.task}, timeout=COLLECT_WAIT_SECONDS)
    if job.state == DONE and job.report is not None:
        return job.report
    if job.state == FAILED and job.error is not None:
        raise ApiError(
            str(job.error["error"]),
            status_code=int(str(job.error["status"])),
            **dict(job.error["error_args"]),  # type: ignore[call-overload]
        )
    response.status_code = 202
    return job.payload()


@router.get("/{slug}/collect")
async def collect_status(
    request: Request,
    user: SourcesCollect,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
) -> dict[str, object]:
    """État de la dernière collecte de cette source par cette personne."""
    jobs: CollectJobs = request.app.state.collect_jobs
    job = jobs.get(_job_key(space_id, branch_id, user, slug))
    return job.payload() if job is not None else {"state": "idle"}


async def _collect(
    conn: asyncpg.Connection,
    crypto: CryptoService,
    user: User,
    workdir: DraftWorkdir,
    collector: SourceCollector,
    space_id: uuid.UUID,
    branch_slug: str,
    slug: str,
    force: bool,
    progress: CollectProgress,
) -> dict[str, object]:
    """La collecte elle-même (tâche de fond, connexion propre)."""
    space, space_username, space_token = await space_credentials(conn, crypto, space_id)
    entries = await list_sources(workdir, space, branch_slug, space_username, space_token)
    entry = next((e for e in entries if e.slug == slug and e.manifest is not None), None)
    if entry is None or entry.manifest is None:
        raise ApiError("source_not_found", status_code=404, slug=slug)
    manifest = entry.manifest
    plugin_params = await db_get_plugin_params(conn)
    if manifest.type == OPENAPI_TYPE:
        try:
            report = await collector.collect_openapi(
                workdir=workdir,
                space=space,
                branch_slug=branch_slug,
                branch_label=branch_slug,
                space_username=space_username,
                space_token=space_token,
                source_slug=slug,
                author_name=user.email,
                author_email=user.email,
                force=force,
                progress=progress,
                plugin_params=plugin_params,
            )
        except CollectionError as exc:
            raise _collect_error(exc.code) from None
    elif manifest.type == GIT_TYPE:
        connection = await db_get_connection_by_slug(conn, manifest.connection)
        if connection is None:
            raise ApiError("invalid_request", status_code=400, fields=["connection_invalid"])
        strategy = get_strategy(connection.forge)
        source_token = (await resolve_connection_token(conn, crypto, connection)).reveal()
        try:
            report = await collector.collect(
                workdir=workdir,
                space=space,
                branch_slug=branch_slug,
                branch_label=branch_slug,
                space_username=space_username,
                space_token=space_token,
                source_slug=slug,
                source_clone_url=strategy.git_clone_url(
                    connection.url, str(manifest.parameters.get("repository", ""))
                ),
                source_branch=str(manifest.parameters.get("branch", "")),
                source_username=strategy.git_username(),
                source_token=source_token,
                author_name=user.email,
                author_email=user.email,
                force=force,
                progress=progress,
                parsers=_str_list(manifest.parameters.get("parsers")),
                directories=_str_list(manifest.parameters.get("directories")),
                exclusions=_str_list(manifest.parameters.get("exclusions")),
                plugin_params=plugin_params,
            )
        except CollectionError as exc:
            raise _collect_error(exc.code) from None
    elif manifest.type in DB_TYPES:
        db = await db_get_db_connection_by_slug(conn, manifest.connection)
        if db is None or db.engine != manifest.type:
            raise ApiError("invalid_request", status_code=400, fields=["connection_invalid"])
        secret_value = (await resolve_secret_value(conn, crypto, db.secret_slug)).reveal()
        db_params = {k: str(v) for k, v in db.params.items()}
        database = str(manifest.parameters.get("database", "") or db_params.get("database", ""))
        db_params["database"] = database
        try:
            report = await collector.collect_sql(
                workdir=workdir,
                space=space,
                branch_slug=branch_slug,
                branch_label=branch_slug,
                space_username=space_username,
                space_token=space_token,
                source_slug=slug,
                engine=get_engine(db.engine),
                dialect=db.engine,
                db_params=db_params,
                secret_value=secret_value,
                schemas=_str_list(manifest.parameters.get("schemas")),
                exclusions=_str_list(manifest.parameters.get("exclusions")),
                author_name=user.email,
                author_email=user.email,
                force=force,
                progress=progress,
            )
        except CollectionError as exc:
            raise _collect_error(exc.code) from None
    else:
        raise ApiError("invalid_request", status_code=400, fields=["collect_unsupported"])
    # Index de travail : l'inventaire des fichiers vus et leur statut de traitement.
    await db_replace_scan_files(
        conn, space_id=space_id, branch_slug=branch_slug, source_slug=slug, files=report.files
    )
    return _report_payload(report)


@router.get("/{slug}/collections")
async def list_source_collections(
    conn: ConnDep,
    crypto: CryptoDep,
    _: SourcesRead,
    workdir: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
) -> dict[str, object]:
    """Historique de la source : les commits publiés qui ont touché son répertoire
    (collectes comprises). Il est porté par Git, plus par une table : rien ne se
    perd si l'application est perdue."""
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    if not is_valid_source_slug(slug):
        raise ApiError("source_not_found", status_code=404, slug=slug)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        commits = await workdir.file_history(
            space_id, branch.git_ref, space.repo_url, username, token, f"sources/{slug}", 100
        )
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    return {
        "collections": [
            {"sha": c.sha, "author": c.author, "date": c.date, "message": c.message}
            for c in commits
        ]
    }


def _scan_payload(scan: ScanFile) -> dict[str, object]:
    return {
        "path": scan.path,
        "crc": scan.crc,
        "cible": scan.cible,
        "statut": scan.statut,
        "message": scan.message,
        "scanned_at": scan.scanned_at.isoformat(),
    }


@router.get("/{slug}/scan")
async def list_source_scan(
    conn: ConnDep,
    _: SourcesRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
) -> dict[str, object]:
    """Index de travail de la dernière collecte : un fichier source par ligne,
    avec son empreinte, sa cible et son statut de traitement."""
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    files = await db_list_scan_files(
        conn, space_id=space_id, branch_slug=branch.git_ref, source_slug=slug
    )
    return {"fichiers": [_scan_payload(f) for f in files]}


# --- Contrats OpenAPI déposés ------------------------------------------------


def _check_contract_size(raw: bytes, settings: Settings) -> None:
    if len(raw) > settings.openapi_max_contract_bytes:
        max_mo = settings.openapi_max_contract_bytes // (1024 * 1024)
        raise ApiError("contract_too_large", status_code=400, max=max_mo)


def _contract_error(exc: OpenApiError) -> ApiError:
    code = {
        "not_openapi": "contract_not_openapi",
        "yaml_invalid": "contract_yaml_invalid",
        "swagger_unsupported": "contract_swagger_unsupported",
        "ref_missing": "contract_ref_missing",
    }.get(exc.code, "contract_not_openapi")
    if exc.code == "ref_missing":
        return ApiError(code, status_code=400, ref=exc.detail)
    return ApiError(code, status_code=400)


def _analysis_payload(analysis: Analysis) -> dict[str, object]:
    return {
        "openapi": analysis.openapi,
        "title": analysis.title,
        "declared_version": analysis.declared_version,
        "schema_count": analysis.schema_count,
        "operation_count": analysis.operation_count,
        "external_refs": analysis.external_refs,
        "proposed_version": analysis.proposed_version,
    }


def _contract_payload(view: ContractView) -> dict[str, object]:
    e = view.entry
    return {
        "version": e.version,
        "file": e.file,
        "original_file": e.original_file,
        "openapi": e.openapi,
        "declared_version": e.declared_version,
        "schema_count": e.schema_count,
        "operation_count": e.operation_count,
        "uploaded_at": e.uploaded_at,
        "author": e.author,
        "collected": view.collected,
    }


async def _require_openapi_source(
    conn: ConnDep,
    crypto: CryptoService,
    workdir: DraftWorkdir,
    space_id: uuid.UUID,
    branch_slug: str,
    slug: str,
) -> None:
    space, username, token = await space_credentials(conn, crypto, space_id)
    entries = await list_sources(workdir, space, branch_slug, username, token)
    entry = next((e for e in entries if e.slug == slug and e.manifest is not None), None)
    if entry is None or entry.manifest is None:
        raise ApiError("source_not_found", status_code=404, slug=slug)
    if entry.manifest.type != OPENAPI_TYPE:
        raise ApiError("invalid_request", status_code=400, fields=["not_openapi_source"])


@router.post("/{slug}/contracts/analyze")
async def analyze_branch_contract(
    _: SourcesDeclare,
    settings: SettingsDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    body: AnalyzeBody,
) -> dict[str, object]:
    """Analyse un fichier déposé sans rien écrire : l'interface montre l'analyse
    avant l'enregistrement."""
    raw = body.content.encode("utf-8")
    _check_contract_size(raw, settings)
    try:
        analysis = analyze_contract(raw)
    except OpenApiError as exc:
        raise _contract_error(exc) from None
    return _analysis_payload(analysis)


@router.get("/{slug}/contracts")
async def list_branch_contracts(
    conn: ConnDep,
    crypto: CryptoDep,
    _: SourcesRead,
    workdir: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
) -> dict[str, object]:
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        views = await workdir.read_branch(
            space.id,
            branch_slug,
            space.repo_url,
            username,
            token,
            lambda wc: read_contracts_view(wc, slug),
        )
    except WorkdirError as exc:
        raise _workdir_http_error(exc) from None
    return {"contracts": [_contract_payload(v) for v in views]}


@router.post("/{slug}/contracts", status_code=201)
async def deposit_branch_contract(
    conn: ConnDep,
    crypto: CryptoDep,
    user: SourcesDeclare,
    workdir: WorkdirDep,
    settings: SettingsDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    body: DepositBody,
) -> dict[str, object]:
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    await _require_openapi_source(conn, crypto, workdir, space_id, branch_slug, slug)
    raw = body.content.encode("utf-8")
    _check_contract_size(raw, settings)
    version = body.version.strip()
    if not is_valid_version_name(version):
        raise ApiError("contract_version_invalid", status_code=400)
    try:
        analysis = analyze_contract(raw)
    except OpenApiError as exc:
        raise _contract_error(exc) from None
    entry = ContractEntry(
        version=version,
        file=f"{version}.yaml",
        original_file=body.filename.strip() or f"{version}.yaml",
        openapi=analysis.openapi,
        declared_version=analysis.declared_version,
        schema_count=analysis.schema_count,
        operation_count=analysis.operation_count,
        uploaded_at=datetime.now(UTC).isoformat(),
        author=user.email,
    )
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        await workdir.commit_to_branch(
            space.id,
            branch_slug,
            space.repo_url,
            username,
            token,
            user.email,
            user.email,
            f"dépose le contrat {slug} {version}",
            deposit_contract_fn(slug, entry, raw, replace=body.replace),
        )
    except ContractExistsError:
        raise ApiError("contract_version_taken", status_code=409, version=version) from None
    except WorkdirError as exc:
        raise _workdir_http_error(exc) from None
    return _analysis_payload(analysis)


@router.delete("/{slug}/contracts/{version}", status_code=204)
async def remove_branch_contract(
    conn: ConnDep,
    crypto: CryptoDep,
    user: SourcesDeclare,
    workdir: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    version: str,
) -> None:
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    await _require_openapi_source(conn, crypto, workdir, space_id, branch_slug, slug)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        await workdir.commit_to_branch(
            space.id,
            branch_slug,
            space.repo_url,
            username,
            token,
            user.email,
            user.email,
            f"retire le contrat {slug} {version}",
            remove_contract_fn(slug, version),
        )
    except ContractNotFoundError:
        raise ApiError("contract_not_found", status_code=404, version=version) from None
    except WorkdirError as exc:
        raise _workdir_http_error(exc) from None


@router.get("/{slug}/contracts/{version}/download")
async def download_branch_contract(
    conn: ConnDep,
    crypto: CryptoDep,
    _: SourcesRead,
    workdir: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    version: str,
) -> Response:
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)

    def read(wc: Any) -> tuple[bytes, str] | None:
        content = read_contract_bytes(wc, slug, version)
        if content is None:
            return None
        view = next((v for v in read_contracts_view(wc, slug) if v.entry.version == version), None)
        name = view.entry.original_file if view is not None else f"{version}.yaml"
        return content, name

    try:
        result = await workdir.read_branch(
            space.id, branch_slug, space.repo_url, username, token, read
        )
    except WorkdirError as exc:
        raise _workdir_http_error(exc) from None
    if result is None:
        raise ApiError("contract_not_found", status_code=404, version=version)
    content, name = result
    safe_name = name.replace('"', "").replace("\n", "").replace("\r", "")
    return Response(
        content=content,
        media_type="application/yaml",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )
