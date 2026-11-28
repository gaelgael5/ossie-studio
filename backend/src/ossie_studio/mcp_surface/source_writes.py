"""Primitives des sources : déclarer, modifier, supprimer, collecter, contrats.

Elles appellent le service des sources commun à l'écran et à l'agent
(`sources/source_actions.py`, ticket 1920df67) : une seule règle métier, une
validation à un seul endroit. Ses refus (`SourceError`) deviennent des refus nommés
pour l'agent, avec les mêmes codes et les mêmes détails que l'écran.

Comme toute écriture : réservées à un agent connecté pour une personne, dans le
brouillon de la personne.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import Field

from ossie_studio.drafts.store import db_get_draft, db_list_objects
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
from ossie_studio.sources import source_actions as actions
from ossie_studio.sources.contracts import (
    ContractExistsError,
    ContractNotFoundError,
    deposit_contract_fn,
    remove_contract_fn,
)
from ossie_studio.sources.jobs import FAILED, CollectJobs
from ossie_studio.sources.paths import is_valid_source_slug
from ossie_studio.sources.service import (
    SourceEntry,
    SourceExistsError,
    SourceNotFoundError,
    declare_source,
    delete_source,
    update_source,
)
from ossie_studio.sources.source_error import SourceError
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


def _refusal(exc: ApiError | SourceError) -> PrimitiveError:
    """Refus du service des sources (ou de l'espace) → refus nommé pour l'agent (même
    code, mêmes détails que l'écran)."""
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


# --- Déclarer, modifier, supprimer ------------------------------------------------------------


async def declare_source_primitive(
    ctx: PrimitiveContext, args: DeclareSourceArgs
) -> dict[str, Any]:
    bv, user = await open_for_write(ctx, args)
    before = await _snapshot(ctx, user, bv)
    try:
        manifest, connection = await actions.prepare_manifest(
            ctx.conn,
            ctx.state.crypto,
            ctx.state.http_client_factory,
            slug=args.slug.strip(),
            label=args.label,
            source_type=args.type,
            connection=args.connection,
            parameters=args.parameters,
        )
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
    except (ApiError, SourceError) as exc:
        raise _refusal(exc) from None
    except SourceExistsError:
        raise PrimitiveError("source_slug_taken", slug=args.slug.strip()) from None
    except WorkdirError as exc:
        raise PrimitiveError(exc.code) from None
    return {
        "view": "draft",
        "changed_paths": _changed(before, await _snapshot(ctx, user, bv)),
        "source": actions.entry_payload(SourceEntry(manifest.slug, manifest, None), connection),
    }


async def update_source_primitive(ctx: PrimitiveContext, args: UpdateSourceArgs) -> dict[str, Any]:
    bv, user = await open_for_write(ctx, args)
    before = await _snapshot(ctx, user, bv)
    try:
        existing = await actions.existing_manifest(
            ctx.conn, ctx.state.crypto, bv.workdir, bv.space.id, bv.branch.git_ref, args.source
        )
        manifest, connection = await actions.prepare_manifest(
            ctx.conn,
            ctx.state.crypto,
            ctx.state.http_client_factory,
            slug=args.source,
            label=args.label,
            source_type=existing.type,
            connection=args.connection,
            parameters=args.parameters,
            # Les règles de tri se gèrent à part : une modification les préserve.
            triage=existing.triage,
        )
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
    except (ApiError, SourceError) as exc:
        raise _refusal(exc) from None
    except SourceNotFoundError:
        raise PrimitiveError("source_not_found", source=args.source) from None
    except WorkdirError as exc:
        raise PrimitiveError(exc.code) from None
    return {
        "view": "draft",
        "changed_paths": _changed(before, await _snapshot(ctx, user, bv)),
        "source": actions.entry_payload(SourceEntry(manifest.slug, manifest, None), connection),
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
    await actions.forget_source(
        ctx.conn, ctx.state.source_downloader, bv.space.id, bv.branch.git_ref, args.source
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
    # Même tâche de fond que l'écran, même clé : une collecte lancée par l'agent se voit
    # à l'écran, et inversement ; une collecte en cours n'est pas relancée.
    job = actions.start_collect(
        ctx.state,
        ctx.state.crypto,
        user,
        bv.space.id,
        bv.branch.id,
        bv.branch.git_ref,
        args.source,
        args.force,
    )
    assert job.task is not None
    await asyncio.wait({job.task}, timeout=COLLECT_WAIT_SECONDS)
    if job.state == FAILED and job.error is not None:
        raise PrimitiveError(str(job.error["error"]), **dict(job.error["error_args"]))  # type: ignore[call-overload]
    return _job_payload(job.payload(), args.source)


async def get_collect_status(ctx: PrimitiveContext, args: SourceWriteArgs) -> dict[str, Any]:
    if ctx.user is None:
        raise PrimitiveError("draft_requires_person")
    space, branch, _username, _token = await branch_of(ctx, args.space_id, args.branch_id)
    job = _jobs(ctx).get(actions.job_key(space.id, branch.id, ctx.user, args.source))
    return _job_payload(job.payload() if job is not None else {"state": "idle"}, args.source)


# --- Contrats OpenAPI ----------------------------------------------------------------------------


async def _require_openapi(ctx: PrimitiveContext, bv: BranchView, source: str) -> None:
    try:
        await actions.require_openapi_source(
            ctx.conn, ctx.state.crypto, bv.workdir, bv.space.id, bv.branch.git_ref, source
        )
    except (ApiError, SourceError) as exc:
        raise _refusal(exc) from None


async def deposit_contract(ctx: PrimitiveContext, args: DepositContractArgs) -> dict[str, Any]:
    bv, user = await open_for_write(ctx, args)
    await _require_openapi(ctx, bv, args.source)
    raw = args.content.encode("utf-8")
    version = args.version.strip()
    try:
        entry, analysis = actions.prepare_deposit(
            raw, version, args.filename, user.email, ctx.state.settings
        )
    except SourceError as exc:
        raise _refusal(exc) from None
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
        "contract": actions.analysis_payload(analysis),
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
