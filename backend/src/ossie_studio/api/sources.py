from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.branches import space_credentials
from ossie_studio.api.common import SourcesCollect, SourcesDeclare, SourcesRead
from ossie_studio.config import Settings
from ossie_studio.connections.service import (
    db_get_connection_by_slug,
    resolve_connection_token,
)
from ossie_studio.crypto import CryptoService
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
from ossie_studio.forges.registry import get_strategy
from ossie_studio.sources import source_actions as actions
from ossie_studio.sources.collect import CollectionError, SourceCollector
from ossie_studio.sources.contracts import (
    ContractExistsError,
    ContractNotFoundError,
    deposit_contract_fn,
    read_contract_bytes,
    read_contracts_view,
    remove_contract_fn,
)
from ossie_studio.sources.curation_assets import source_classifications
from ossie_studio.sources.download import SourceDownloader
from ossie_studio.sources.explorer import DEFAULT_MODE
from ossie_studio.sources.explorer_service import ensure_index, reference_graph
from ossie_studio.sources.jobs import DONE, FAILED, CollectJobs
from ossie_studio.sources.manifest import (
    GIT_TYPE,
)
from ossie_studio.sources.paths import is_valid_source_slug
from ossie_studio.sources.scan_service import (
    ScanFile,
    db_list_scan_files,
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
from ossie_studio.sources.source_error import SourceError
from ossie_studio.spaces.branch_service import db_get_branch
from ossie_studio.spaces.workdir import WorkdirError

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/sources")


async def source_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """Un refus du service des sources (`source_actions`, ticket 1920df67) rendu comme
    tout refus de l'écran : le code et ses détails, avec son statut HTTP."""
    assert isinstance(exc, SourceError)
    error = ApiError(exc.code, status_code=exc.status, **exc.args_dict)
    return JSONResponse({"error": error.code, "error_args": error.args_dict}, error.status_code)


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


def _workdir_http_error(exc: WorkdirError) -> ApiError:
    return ApiError(exc.code, status_code=404 if exc.code == "branch_not_found" else 502)


async def _branch_slug(conn: ConnDep, space_id: uuid.UUID, branch_id: uuid.UUID) -> str:
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    return branch.git_ref


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
        # Niveau de classification de chaque source, dérivé de ses assets
        # (ticket 5e8680d7, critère 9), valeurs posées par règle comprises
        # (ticket 568a0547) : lues dans l'index de l'explorateur, à jour.
        key, signature = await ensure_index(conn, workdir, space, branch_id, slug, username, token)
        graph = await reference_graph(conn, key, DEFAULT_MODE, signature)
        levels = await workdir.read_branch(
            space.id,
            slug,
            space.repo_url,
            username,
            token,
            lambda root: source_classifications(root, graph),
        )
    except WorkdirError as exc:
        raise _workdir_http_error(exc) from None
    sources: list[dict[str, object]] = []
    for entry in entries:
        connection = (
            await actions.connection_display(conn, entry.manifest.type, entry.manifest.connection)
            if entry.manifest is not None
            else None
        )
        sources.append(
            {
                **actions.entry_payload(entry, connection),
                "classification": levels.get(entry.slug, []),
            }
        )
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
    manifest, connection = await actions.prepare_manifest(
        conn,
        crypto,
        http_client_factory,
        slug=body.slug.strip(),
        label=body.label,
        source_type=body.type,
        connection=body.connection,
        parameters=body.parameters,
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
    return actions.entry_payload(SourceEntry(manifest.slug, manifest, None), connection)


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
    existing = await actions.existing_manifest(conn, crypto, workdir, space_id, branch_slug, slug)
    manifest, connection = await actions.prepare_manifest(
        conn,
        crypto,
        http_client_factory,
        slug=slug,
        label=body.label,
        source_type=existing.type,
        connection=body.connection,
        parameters=body.parameters,
        # Les règles de tri se gèrent à part : une édition de source les préserve.
        triage=existing.triage,
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
    return actions.entry_payload(SourceEntry(manifest.slug, manifest, None), connection)


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
    await actions.forget_source(conn, downloader, space_id, branch_slug, slug)


COLLECT_WAIT_SECONDS = 20.0


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
    job = actions.start_collect(
        request.app.state, crypto, user, space_id, branch_id, branch.git_ref, slug, force
    )
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
    job = jobs.get(actions.job_key(space_id, branch_id, user, slug))
    return job.payload() if job is not None else {"state": "idle"}


@router.post("/{slug}/openapi-search")
async def search_branch_source_contracts(
    conn: ConnDep,
    crypto: CryptoDep,
    _: SourcesCollect,
    workdir: WorkdirDep,
    collector: CollectorDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
) -> dict[str, object]:
    """« Rechercher les contrats » d'une source Git (ticket 56be38c6) : les
    contrats OpenAPI que la collecte prendrait, sans rien écrire dans le
    brouillon. Le dépôt est descendu (ou mis à jour) sur le volume des sources,
    comme pour une collecte : d'où la capacité de collecter."""
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    manifest = await actions.existing_manifest(conn, crypto, workdir, space_id, branch_slug, slug)
    if manifest.type != GIT_TYPE:
        raise ApiError("invalid_request", status_code=400, fields=["not_git_source"])
    connection = await db_get_connection_by_slug(conn, manifest.connection)
    if connection is None:
        raise ApiError("invalid_request", status_code=400, fields=["connection_invalid"])
    space, _username, _token = await space_credentials(conn, crypto, space_id)
    strategy = get_strategy(connection.forge)
    source_token = (await resolve_connection_token(conn, crypto, connection)).reveal()
    try:
        found = await collector.find_contracts(
            space=space,
            source_slug=slug,
            source_clone_url=strategy.git_clone_url(
                connection.url, str(manifest.parameters.get("repository", ""))
            ),
            source_branch=str(manifest.parameters.get("branch", "")),
            source_username=strategy.git_username(),
            source_token=source_token,
            directories=actions.str_list(manifest.parameters.get("directories")),
            exclusions=actions.str_list(manifest.parameters.get("exclusions")),
        )
    except CollectionError as exc:
        raise actions.collect_error(exc.code) from None
    return {
        "contracts": [
            {
                "path": c.path,
                "status": c.status,
                "title": c.title,
                "version": c.version,
                "openapi": c.openapi,
                "error": c.error,
            }
            for c in found
        ]
    }


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
    return actions.analysis_payload(actions.analyze(body.content.encode("utf-8"), settings))


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
    return {"contracts": [actions.contract_payload(v) for v in views]}


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
    await actions.require_openapi_source(conn, crypto, workdir, space_id, branch_slug, slug)
    raw = body.content.encode("utf-8")
    version = body.version.strip()
    entry, analysis = actions.prepare_deposit(raw, version, body.filename, user.email, settings)
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
    return actions.analysis_payload(analysis)


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
    await actions.require_openapi_source(conn, crypto, workdir, space_id, branch_slug, slug)
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
