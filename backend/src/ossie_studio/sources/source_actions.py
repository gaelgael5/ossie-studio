"""Service des sources, commun à l'écran et à l'agent (ticket docflow 1920df67).

Décidé par Gael le 22 novembre 2026 (« 1 maintenant. créé un ticket pour après pour
l'option 2 »), lancé le 28 novembre : la logique des sources sort des routes de
`api/sources.py` pour que l'écran **et** les primitives MCP appellent les mêmes
fonctions — une validation n'existe qu'à un endroit.

Ce service ne connaît ni FastAPI ni MCP. Ses refus sont des `SourceError` (code,
statut, détails), que chaque appelant traduit : l'écran en `ApiError` (gestionnaire
d'erreurs de l'application), l'agent en refus nommé. Les exceptions des couches du
dessous (`SourceExistsError`, `SourceNotFoundError`, `ContractExistsError`,
`ContractNotFoundError`, `WorkdirError`) passent telles quelles : chaque appelant les
traduit comme avant.

Une règle diffère volontairement entre l'écran et l'agent : l'agent ne remplace
jamais une version de contrat existante (`replace` reste à l'appelant).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import asyncpg
import httpx

from ossie_studio.api.branches import space_credentials
from ossie_studio.config import Settings
from ossie_studio.connections.service import (
    db_get_connection_by_slug,
    resolve_connection_token,
)
from ossie_studio.crypto import CryptoService
from ossie_studio.databases.registry import get_engine
from ossie_studio.databases.service import db_get_db_connection_by_slug
from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.forges.base import ForgeError
from ossie_studio.forges.registry import get_strategy
from ossie_studio.models import User
from ossie_studio.parsers.openapi import Analysis, OpenApiError, is_valid_version_name
from ossie_studio.parsers.openapi import analyze as analyze_contract
from ossie_studio.parsers.params_service import db_get_plugin_params
from ossie_studio.parsers.registry import conflicting_glob, unknown_code_parseur
from ossie_studio.secretstore.service import resolve_secret_value
from ossie_studio.sources.collect import CollectionError, CollectReport, SourceCollector
from ossie_studio.sources.contracts import ContractEntry, ContractView
from ossie_studio.sources.download import SourceDownloader
from ossie_studio.sources.jobs import CollectJob, CollectJobs, JobKey
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
from ossie_studio.sources.scan_service import db_delete_scan_files, db_replace_scan_files
from ossie_studio.sources.service import SourceEntry, list_sources
from ossie_studio.sources.source_error import SourceError


def str_list(value: object) -> list[str] | None:
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [str(v) for v in value]
    return None


# --- Déclarer et modifier une source -------------------------------------------------------


def check_source_type(source_type: str) -> None:
    if source_type not in (GIT_TYPE, *DB_TYPES, OPENAPI_TYPE):
        raise SourceError("invalid_request", 400, fields=["type_unknown"])


async def resolve_connection(
    conn: asyncpg.Connection, source_type: str, connection: str
) -> dict[str, object]:
    """Vérifie que la connexion (désignée par son slug) existe et est du bon genre
    pour le type de source, et rend de quoi l'afficher. Jamais de valeur secrète."""
    slug = connection.strip()
    if not slug:
        raise SourceError("invalid_request", 400, fields=["connection_required"])
    if source_type == GIT_TYPE:
        forge = await db_get_connection_by_slug(conn, slug)
        if forge is None:
            raise SourceError("invalid_request", 400, fields=["connection_invalid"])
        return {"slug": forge.slug, "label": forge.label, "kind": "forge", "forge": forge.forge}
    # Type base : connexion de base du même moteur.
    db = await db_get_db_connection_by_slug(conn, slug)
    if db is None or db.engine != source_type:
        raise SourceError("invalid_request", 400, fields=["connection_invalid"])
    return {"slug": db.slug, "label": db.label, "kind": "db", "engine": db.engine}


async def connection_display(
    conn: asyncpg.Connection, source_type: str, connection: str
) -> dict[str, object] | None:
    try:
        return await resolve_connection(conn, source_type, connection)
    except SourceError:
        return None


async def build_manifest(
    conn: asyncpg.Connection,
    slug: str,
    label: str,
    source_type: str,
    connection: str,
    parameters: dict[str, Any],
    triage: list[dict[str, Any]] | None = None,
) -> tuple[SourceManifest, dict[str, object] | None]:
    if not is_valid_source_slug(slug):
        raise SourceError("invalid_request", 400, fields=["slug_invalid"])
    # Source par dépôt : pas de connexion à résoudre.
    connection_info: dict[str, object] | None = None
    if source_type != OPENAPI_TYPE:
        connection_info = await resolve_connection(conn, source_type, connection)
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
        raise SourceError("invalid_request", 400, fields=exc.fields) from None
    return manifest, connection_info


def check_git_parsers(manifest: SourceManifest) -> None:
    """Refuse un parseur inconnu (en le nommant) ou deux parseurs qui se disputent
    le même motif (en nommant le motif)."""
    parsers = str_list(manifest.parameters.get("parsers")) or []
    unknown = unknown_code_parseur(parsers)
    if unknown is not None:
        raise SourceError("parseur_unknown", 400, id=unknown)
    conflict = conflicting_glob(parsers)
    if conflict is not None:
        raise SourceError("parseur_conflict", 400, pattern=conflict)


async def validate_git_branch(
    conn: asyncpg.Connection,
    crypto: CryptoService,
    http_client_factory: Callable[[], httpx.AsyncClient],
    connection_slug: str,
    repository: str,
    branch: str,
) -> None:
    """Une source Git suit une **branche** du dépôt source : on refuse une branche
    inexistante (et donc un nom de tag ou de commit)."""
    connection = await db_get_connection_by_slug(conn, connection_slug.strip())
    assert connection is not None  # déjà validée par resolve_connection
    token = (await resolve_connection_token(conn, crypto, connection)).reveal()
    strategy = get_strategy(connection.forge)
    async with http_client_factory() as client:
        try:
            branches = await strategy.list_branches(client, connection.url, token, repository)
        except ForgeError as exc:
            raise SourceError(exc.code, 400) from None
    if branch not in branches:
        raise SourceError("invalid_request", 400, fields=["branch_not_found"])


async def prepare_manifest(
    conn: asyncpg.Connection,
    crypto: CryptoService,
    http_client_factory: Callable[[], httpx.AsyncClient],
    *,
    slug: str,
    label: str,
    source_type: str,
    connection: str,
    parameters: dict[str, Any],
    triage: list[dict[str, Any]] | None = None,
) -> tuple[SourceManifest, dict[str, object] | None]:
    """Le manifeste d'une déclaration ou d'une modification, validé : type, slug,
    connexion, paramètres ; pour une source Git, ses parseurs et sa branche."""
    check_source_type(source_type)
    manifest, connection_info = await build_manifest(
        conn, slug, label, source_type, connection, parameters, triage
    )
    if manifest.type == GIT_TYPE:
        check_git_parsers(manifest)
        await validate_git_branch(
            conn,
            crypto,
            http_client_factory,
            connection,
            str(manifest.parameters["repository"]),
            str(manifest.parameters["branch"]),
        )
    return manifest, connection_info


def entry_payload(entry: SourceEntry, connection: dict[str, object] | None) -> dict[str, object]:
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
        # Nombre de règles de tri : la liste des sources les montre, comme les
        # répertoires exclus (elles se règlent dans la configuration de la source).
        "triage_count": len(manifest.triage),
    }


async def existing_manifest(
    conn: asyncpg.Connection,
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
    raise SourceError("source_not_found", 404, slug=slug)


async def forget_source(
    conn: asyncpg.Connection,
    downloader: SourceDownloader,
    space_id: uuid.UUID,
    branch_slug: str,
    slug: str,
) -> None:
    """Après la suppression d'une source : le code descendu sur le volume des sources
    et l'index de collecte partent avec elle."""
    await downloader.remove(space_id, slug)
    await db_delete_scan_files(conn, space_id=space_id, branch_slug=branch_slug, source_slug=slug)


# --- Collecter -----------------------------------------------------------------------------


def job_key(space_id: uuid.UUID, branch_id: uuid.UUID, user: User, slug: str) -> JobKey:
    """La clé d'une collecte : la même pour l'écran et l'agent, si bien qu'une collecte
    lancée par l'un se voit chez l'autre, et qu'une collecte en cours n'est pas
    relancée."""
    return (space_id, branch_id, user.id, slug)


def collect_error(code: str) -> SourceError:
    if code == "branch_behind":
        return SourceError("collect_branch_behind", 409)
    if code == "branch_not_found":
        return SourceError("branch_not_found", 404)
    return SourceError("collect_failed", 502, cause=code)


def report_payload(report: CollectReport) -> dict[str, object]:
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


def start_collect(
    state: Any,
    crypto: CryptoService,
    user: User,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    branch_ref: str,
    slug: str,
    force: bool,
) -> CollectJob:
    """Lance la collecte en tâche de fond, rattachée à la personne (ou rend celle qui
    tourne déjà). `state` : l'état de l'application (pool, copies de travail,
    collecteur, tâches)."""

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
            return await collect(
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
    return jobs.start(job_key(space_id, branch_id, user, slug), run)


async def collect(
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
        raise SourceError("source_not_found", 404, slug=slug)
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
            raise collect_error(exc.code) from None
    elif manifest.type == GIT_TYPE:
        connection = await db_get_connection_by_slug(conn, manifest.connection)
        if connection is None:
            raise SourceError("invalid_request", 400, fields=["connection_invalid"])
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
                parsers=str_list(manifest.parameters.get("parsers")),
                directories=str_list(manifest.parameters.get("directories")),
                exclusions=str_list(manifest.parameters.get("exclusions")),
                plugin_params=plugin_params,
            )
        except CollectionError as exc:
            raise collect_error(exc.code) from None
    elif manifest.type in DB_TYPES:
        db = await db_get_db_connection_by_slug(conn, manifest.connection)
        if db is None or db.engine != manifest.type:
            raise SourceError("invalid_request", 400, fields=["connection_invalid"])
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
                schemas=str_list(manifest.parameters.get("schemas")),
                exclusions=str_list(manifest.parameters.get("exclusions")),
                author_name=user.email,
                author_email=user.email,
                force=force,
                progress=progress,
            )
        except CollectionError as exc:
            raise collect_error(exc.code) from None
    else:
        raise SourceError("invalid_request", 400, fields=["collect_unsupported"])
    # Index de travail : l'inventaire des fichiers vus et leur statut de traitement.
    await db_replace_scan_files(
        conn, space_id=space_id, branch_slug=branch_slug, source_slug=slug, files=report.files
    )
    return report_payload(report)


# --- Contrats OpenAPI déposés --------------------------------------------------------------


def check_contract_size(raw: bytes, settings: Settings) -> None:
    if len(raw) > settings.openapi_max_contract_bytes:
        max_mo = settings.openapi_max_contract_bytes // (1024 * 1024)
        raise SourceError("contract_too_large", 400, max=max_mo)


def contract_error(exc: OpenApiError) -> SourceError:
    code = {
        "not_openapi": "contract_not_openapi",
        "yaml_invalid": "contract_yaml_invalid",
        "swagger_unsupported": "contract_swagger_unsupported",
        "ref_missing": "contract_ref_missing",
    }.get(exc.code, "contract_not_openapi")
    if exc.code == "ref_missing":
        return SourceError(code, 400, ref=exc.detail)
    return SourceError(code, 400)


def analyze(raw: bytes, settings: Settings) -> Analysis:
    """L'analyse d'un contrat, sans rien écrire : taille, puis forme OpenAPI."""
    check_contract_size(raw, settings)
    try:
        return analyze_contract(raw)
    except OpenApiError as exc:
        raise contract_error(exc) from None


def analysis_payload(analysis: Analysis) -> dict[str, object]:
    return {
        "openapi": analysis.openapi,
        "title": analysis.title,
        "declared_version": analysis.declared_version,
        "schema_count": analysis.schema_count,
        "operation_count": analysis.operation_count,
        "external_refs": analysis.external_refs,
        "proposed_version": analysis.proposed_version,
    }


def contract_payload(view: ContractView) -> dict[str, object]:
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


async def require_openapi_source(
    conn: asyncpg.Connection,
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
        raise SourceError("source_not_found", 404, slug=slug)
    if entry.manifest.type != OPENAPI_TYPE:
        raise SourceError("invalid_request", 400, fields=["not_openapi_source"])


def prepare_deposit(
    raw: bytes, version: str, filename: str, author: str, settings: Settings
) -> tuple[ContractEntry, Analysis]:
    """Ce qu'un dépôt range : taille, nom de version, analyse du contrat, puis l'entrée
    de l'index (faits du dépôt). `version` est déjà débarrassée de ses espaces."""
    check_contract_size(raw, settings)
    if not is_valid_version_name(version):
        raise SourceError("contract_version_invalid", 400)
    try:
        analysis = analyze_contract(raw)
    except OpenApiError as exc:
        raise contract_error(exc) from None
    entry = ContractEntry(
        version=version,
        file=f"{version}.yaml",
        original_file=filename.strip() or f"{version}.yaml",
        openapi=analysis.openapi,
        declared_version=analysis.declared_version,
        schema_count=analysis.schema_count,
        operation_count=analysis.operation_count,
        uploaded_at=datetime.now(UTC).isoformat(),
        author=author,
    )
    return entry, analysis
