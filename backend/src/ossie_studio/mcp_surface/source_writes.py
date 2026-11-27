"""Primitives des sources : déclarer, modifier, supprimer, collecter, contrats.

Décision de Gael (22 novembre 2026, option 1) : ces primitives appellent
**telles quelles** les fonctions de l'écran des sources (`api/sources.py`, fichier
de la collecte modifié en parallèle par un autre chantier), sans les modifier.
Une seule règle métier, mais une dépendance à des fonctions internes d'un module
d'API : le ticket « Service commun des sources pour l'écran et l'agent »
(1920df67) la retirera. Les refus de l'écran (`ApiError`) deviennent des refus
nommés pour l'agent, avec les mêmes codes.

Comme toute écriture : réservées à un agent connecté pour une personne, dans le
brouillon de la personne.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from ossie_studio.api import sources as sources_api
from ossie_studio.drafts.store import db_get_draft, db_list_objects
from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.errors import ApiError
from ossie_studio.mcp_surface.branch import (
    BranchArgs,
    BranchView,
    branch_of,
    source_dir,
)
from ossie_studio.mcp_surface.core import PrimitiveContext, PrimitiveError
from ossie_studio.mcp_surface.writes import SourceWriteArgs, open_for_write
from ossie_studio.models import User
from ossie_studio.parsers.openapi import OpenApiError, is_valid_version_name
from ossie_studio.parsers.openapi import analyze as analyze_contract
from ossie_studio.sources.contracts import (
    ContractEntry,
    ContractExistsError,
    ContractNotFoundError,
    deposit_contract_fn,
    remove_contract_fn,
)
from ossie_studio.sources.jobs import FAILED, CollectJobs
from ossie_studio.sources.manifest import DB_TYPES, GIT_TYPE, OPENAPI_TYPE, SourceManifest
from ossie_studio.sources.paths import is_valid_source_slug
from ossie_studio.sources.progress import CollectProgress
from ossie_studio.sources.scan_service import db_delete_scan_files
from ossie_studio.sources.service import (
    SourceEntry,
    SourceExistsError,
    SourceNotFoundError,
    declare_source,
    delete_source,
    update_source,
)
from ossie_studio.spaces.workdir import WorkdirError

# L'appel attend la collecte ce temps-là au plus ; au-delà, il rend l'état et
# l'agent suit avec get_collect_status (la collecte continue en tâche de fond).
COLLECT_WAIT_SECONDS = 10.0

_ABSENT = object()


# --- Arguments --------------------------------------------------------------------------


class DeclareSourceArgs(BranchArgs):
    slug: str = Field(description="Slug of the new source (lowercase, digits, single hyphens).")
    label: str
    type: str = Field(description="'git', a database engine, or 'openapi'.")
    connection: str = Field(default="", description="Connection slug (none for 'openapi').")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Type-specific parameters, as in the manifest (kind 'source').",
    )


class UpdateSourceArgs(SourceWriteArgs):
    label: str
    connection: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class CollectSourceArgs(SourceWriteArgs):
    force: bool = Field(default=False, description="Collect even if nothing changed.")


class DepositContractArgs(SourceWriteArgs):
    version: str = Field(description="Version name of the contract, e.g. 'v1'.")
    content: str = Field(description="The OpenAPI contract, as YAML or JSON text.")
    filename: str = ""


class ContractVersionArgs(SourceWriteArgs):
    version: str


# --- Outils ---------------------------------------------------------------------------------


def _refusal(exc: ApiError) -> PrimitiveError:
    """Refus de l'écran → refus nommé pour l'agent (même code, mêmes détails)."""
    return PrimitiveError(exc.code, **exc.args_dict)


async def _snapshot(ctx: PrimitiveContext, user: User, bv: BranchView) -> dict[str, object]:
    """Objets du brouillon de la personne (chemin → contenu), pour dire ce qui a changé."""
    draft = await db_get_draft(ctx.conn, user.id, bv.branch.id)
    if draft is None or draft.space_id != bv.space.id:
        return {}
    return {o.path: o.content for o in await db_list_objects(ctx.conn, draft.id)}


def _changed(before: dict[str, object], after: dict[str, object]) -> list[str]:
    return sorted(
        p for p in set(before) | set(after) if before.get(p, _ABSENT) != after.get(p, _ABSENT)
    )


async def _git_checks(ctx: PrimitiveContext, manifest: SourceManifest, connection: str) -> None:
    if manifest.type != GIT_TYPE:
        return
    sources_api._check_git_parsers(manifest)
    await sources_api._validate_git_branch(
        ctx.conn,
        ctx.state.crypto,
        ctx.state.http_client_factory,
        connection,
        str(manifest.parameters["repository"]),
        str(manifest.parameters["branch"]),
    )


def _source_payload(entry: SourceEntry, connection: dict[str, object] | None) -> dict[str, Any]:
    payload: dict[str, Any] = sources_api._entry_payload(entry, connection)
    return payload


# --- Déclarer, modifier, supprimer ------------------------------------------------------------


async def declare_source_primitive(
    ctx: PrimitiveContext, args: DeclareSourceArgs
) -> dict[str, Any]:
    bv, user = await open_for_write(ctx, args)
    if args.type not in (GIT_TYPE, *DB_TYPES, OPENAPI_TYPE):
        raise PrimitiveError("invalid_request", fields=["type_unknown"])
    before = await _snapshot(ctx, user, bv)
    try:
        manifest, connection = await sources_api._build_manifest(
            ctx.conn, args.slug.strip(), args.label, args.type, args.connection, args.parameters
        )
        await _git_checks(ctx, manifest, args.connection)
        await declare_source(
            bv.workdir,
            bv.space,
            bv.branch.git_ref,
            bv.username,
            bv.token,
            user.email,
            user.email,
            manifest,
        )
    except ApiError as exc:
        raise _refusal(exc) from None
    except SourceExistsError:
        raise PrimitiveError("source_slug_taken", slug=args.slug.strip()) from None
    except WorkdirError as exc:
        raise PrimitiveError(exc.code) from None
    return {
        "view": "draft",
        "changed_paths": _changed(before, await _snapshot(ctx, user, bv)),
        "source": _source_payload(SourceEntry(manifest.slug, manifest, None), connection),
    }


async def update_source_primitive(ctx: PrimitiveContext, args: UpdateSourceArgs) -> dict[str, Any]:
    bv, user = await open_for_write(ctx, args)
    before = await _snapshot(ctx, user, bv)
    try:
        existing = await sources_api._existing_manifest(
            ctx.conn, ctx.state.crypto, bv.workdir, bv.space.id, bv.branch.git_ref, args.source
        )
        manifest, connection = await sources_api._build_manifest(
            ctx.conn,
            args.source,
            args.label,
            existing.type,
            args.connection,
            args.parameters,
            # Les règles de tri se gèrent à part : une modification les préserve.
            triage=existing.triage,
        )
        await _git_checks(ctx, manifest, args.connection)
        await update_source(
            bv.workdir,
            bv.space,
            bv.branch.git_ref,
            bv.username,
            bv.token,
            user.email,
            user.email,
            manifest,
        )
    except ApiError as exc:
        raise _refusal(exc) from None
    except SourceNotFoundError:
        raise PrimitiveError("source_not_found", source=args.source) from None
    except WorkdirError as exc:
        raise PrimitiveError(exc.code) from None
    return {
        "view": "draft",
        "changed_paths": _changed(before, await _snapshot(ctx, user, bv)),
        "source": _source_payload(SourceEntry(manifest.slug, manifest, None), connection),
    }


async def delete_source_primitive(ctx: PrimitiveContext, args: SourceWriteArgs) -> dict[str, Any]:
    if not is_valid_source_slug(args.source):
        raise PrimitiveError("source_not_found", source=args.source)
    bv, user = await open_for_write(ctx, args)
    before = await _snapshot(ctx, user, bv)
    try:
        await delete_source(
            bv.workdir,
            bv.space,
            bv.branch.git_ref,
            bv.username,
            bv.token,
            user.email,
            user.email,
            args.source,
        )
    except SourceNotFoundError:
        raise PrimitiveError("source_not_found", source=args.source) from None
    except WorkdirError as exc:
        raise PrimitiveError(exc.code) from None
    # Comme l'écran : le code descendu et l'index de collecte partent avec la source.
    await ctx.state.source_downloader.remove(bv.space.id, args.source)
    await db_delete_scan_files(
        ctx.conn, space_id=bv.space.id, branch_slug=bv.branch.git_ref, source_slug=args.source
    )
    return {"view": "draft", "changed_paths": _changed(before, await _snapshot(ctx, user, bv))}


# --- Collecte --------------------------------------------------------------------------------


def _jobs(ctx: PrimitiveContext) -> CollectJobs:
    jobs: CollectJobs = ctx.state.collect_jobs
    return jobs


def _job_payload(job_payload: dict[str, object], source: str) -> dict[str, Any]:
    return {"view": "draft", "source": source, **job_payload}


async def collect_source(ctx: PrimitiveContext, args: CollectSourceArgs) -> dict[str, Any]:
    directory = source_dir(args.source)
    bv, user = await open_for_write(ctx, args)
    if not await bv.read(lambda root: (root / directory).is_dir()):
        raise PrimitiveError("source_not_found", source=args.source)
    state = ctx.state
    space_id, branch_id, branch_ref = bv.space.id, bv.branch.id, bv.branch.git_ref

    async def run(progress: CollectProgress) -> dict[str, object]:
        # Même tâche de fond que l'écran, avec sa propre connexion.
        async with state.pool.acquire() as job_conn:
            workdir = DraftWorkdir(
                state.workdir_manager,
                state.draft_views,
                state.pool,
                user.id,
                branch_id,
                conn=job_conn,
            )
            report: dict[str, object] = await sources_api._collect(
                job_conn,
                state.crypto,
                user,
                workdir,
                state.source_collector,
                space_id,
                branch_ref,
                args.source,
                args.force,
                progress,
            )
            return report

    # La clé de l'écran : une collecte lancée par l'agent se voit à l'écran, et
    # inversement ; une collecte en cours n'est pas relancée.
    job = _jobs(ctx).start(sources_api._job_key(space_id, branch_id, user, args.source), run)
    assert job.task is not None
    await asyncio.wait({job.task}, timeout=COLLECT_WAIT_SECONDS)
    if job.state == FAILED and job.error is not None:
        raise PrimitiveError(str(job.error["error"]), **dict(job.error["error_args"]))  # type: ignore[call-overload]
    return _job_payload(job.payload(), args.source)


async def get_collect_status(ctx: PrimitiveContext, args: SourceWriteArgs) -> dict[str, Any]:
    if ctx.user is None:
        raise PrimitiveError("draft_requires_person")
    space, branch, _username, _token = await branch_of(ctx, args.space_id, args.branch_id)
    job = _jobs(ctx).get(sources_api._job_key(space.id, branch.id, ctx.user, args.source))
    return _job_payload(job.payload() if job is not None else {"state": "idle"}, args.source)


# --- Contrats OpenAPI ----------------------------------------------------------------------------


async def _require_openapi(ctx: PrimitiveContext, bv: BranchView, source: str) -> None:
    try:
        await sources_api._require_openapi_source(
            ctx.conn, ctx.state.crypto, bv.workdir, bv.space.id, bv.branch.git_ref, source
        )
    except ApiError as exc:
        raise _refusal(exc) from None


async def deposit_contract(ctx: PrimitiveContext, args: DepositContractArgs) -> dict[str, Any]:
    bv, user = await open_for_write(ctx, args)
    await _require_openapi(ctx, bv, args.source)
    raw = args.content.encode("utf-8")
    version = args.version.strip()
    try:
        sources_api._check_contract_size(raw, ctx.state.settings)
        if not is_valid_version_name(version):
            raise ApiError("contract_version_invalid", status_code=400)
        try:
            analysis = analyze_contract(raw)
        except OpenApiError as exc:
            raise sources_api._contract_error(exc) from None
    except ApiError as exc:
        raise _refusal(exc) from None
    entry = ContractEntry(
        version=version,
        file=f"{version}.yaml",
        original_file=args.filename.strip() or f"{version}.yaml",
        openapi=analysis.openapi,
        declared_version=analysis.declared_version,
        schema_count=analysis.schema_count,
        operation_count=analysis.operation_count,
        uploaded_at=datetime.now(UTC).isoformat(),
        author=user.email,
    )
    before = await _snapshot(ctx, user, bv)
    try:
        # L'agent ne remplace jamais une version existante : il la retire d'abord
        # (geste destructif, confirmé par le client), puis la redépose.
        await bv.workdir.commit_to_branch(
            bv.space.id,
            bv.branch.git_ref,
            bv.space.repo_url,
            bv.username,
            bv.token,
            user.email,
            user.email,
            f"dépose le contrat {args.source} {version}",
            deposit_contract_fn(args.source, entry, raw, replace=False),
        )
    except ContractExistsError:
        raise PrimitiveError("contract_version_taken", version=version) from None
    except WorkdirError as exc:
        raise PrimitiveError(exc.code) from None
    return {
        "view": "draft",
        "changed_paths": _changed(before, await _snapshot(ctx, user, bv)),
        "contract": sources_api._analysis_payload(analysis),
    }


async def remove_contract(ctx: PrimitiveContext, args: ContractVersionArgs) -> dict[str, Any]:
    bv, user = await open_for_write(ctx, args)
    await _require_openapi(ctx, bv, args.source)
    before = await _snapshot(ctx, user, bv)
    try:
        await bv.workdir.commit_to_branch(
            bv.space.id,
            bv.branch.git_ref,
            bv.space.repo_url,
            bv.username,
            bv.token,
            user.email,
            user.email,
            f"retire le contrat {args.source} {args.version}",
            remove_contract_fn(args.source, args.version),
        )
    except ContractNotFoundError:
        raise PrimitiveError("contract_not_found", version=args.version) from None
    except WorkdirError as exc:
        raise PrimitiveError(exc.code) from None
    return {"view": "draft", "changed_paths": _changed(before, await _snapshot(ctx, user, bv))}
