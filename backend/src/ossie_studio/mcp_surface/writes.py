"""Primitives d'écriture : l'agent travaille dans le brouillon de la personne.

Règles (epic « Primitives MCP d'écriture ») :
- réservées à un agent connecté pour une personne : jamais proposées à une clé
  d'API (`person_only` au catalogue), et refusées ici aussi sans personne ;
- toute écriture va dans le brouillon de la personne, par le même service que
  l'écran (`sources/curation_actions.py`) : une seule règle métier ;
- la réponse dit ce qui a été écrit (révision du brouillon, chemins touchés) et
  l'état de l'objet après, lu en vue brouillon (`unpublished`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ossie_studio.mcp_surface import reads
from ossie_studio.mcp_surface.branch import (
    BranchArgs,
    BranchView,
    ObjectArgs,
    open_branch,
    source_dir,
)
from ossie_studio.mcp_surface.core import PrimitiveContext, PrimitiveError
from ossie_studio.sources import curation_actions
from ossie_studio.sources.curation import (
    AssetNotRetainedError,
    AssetOriginMissingError,
    is_schema_file,
)
from ossie_studio.sources.curation_actions import CurationTarget, UnknownFieldsError, WriteResult
from ossie_studio.spaces.workdir import WorkdirError

# --- Arguments --------------------------------------------------------------------------


class SourceWriteArgs(BranchArgs):
    source: str = Field(description="Source slug (from list_sources).")


class ObjectWriteArgs(SourceWriteArgs):
    path: str = Field(description="Schema file name, e.g. 'customer.json' (from the lists).")


class RetainArgs(ObjectWriteArgs):
    fields: list[str] | None = Field(
        default=None, description="Fields to retain; omit to retain the whole object."
    )


class EnrichValues(BaseModel):
    """Seules les clés données changent ; une valeur vide retire la clé."""

    model_config = ConfigDict(extra="forbid")
    business_name: str | None = None
    description: str | None = None
    concept: str | None = None
    tags: list[str] | None = None


class EnrichArgs(ObjectWriteArgs):
    object: EnrichValues | None = Field(
        default=None, description="Enrichment of the object itself."
    )
    fields: dict[str, EnrichValues] = Field(
        default_factory=dict, description="Enrichment per field name."
    )


class RefreshArgs(SourceWriteArgs):
    paths: list[str] | None = Field(
        default=None, description="Assets to refresh; omit to refresh all retained assets."
    )


# --- Mécanique commune ---------------------------------------------------------------------


async def open_for_write(ctx: PrimitiveContext, args: BranchArgs) -> tuple[BranchView, Any]:
    """Branche ouverte en vue brouillon, et la personne pour qui écrire.

    Deuxième barrière derrière le catalogue : sans personne (clé d'API), refus."""
    if ctx.user is None:
        raise PrimitiveError("draft_requires_person")
    return await open_branch(ctx, args, "draft"), ctx.user


def _target(ctx: PrimitiveContext, bv: BranchView, author: str) -> CurationTarget:
    return CurationTarget(
        bv.workdir,
        bv.space,
        bv.branch.git_ref,
        bv.username,
        bv.token,
        author,
        via=f"via l'agent {ctx.caller.client_id or ctx.caller.ref}",
        strict=True,
    )


async def _source_ready(bv: BranchView, slug: str) -> None:
    directory = source_dir(slug)
    if not await bv.read(lambda root: (root / directory).is_dir()):
        raise PrimitiveError("source_not_found", source=slug)


def _check_path(path: str) -> None:
    if not is_schema_file(path):
        raise PrimitiveError("curation_path_invalid", path=path)


async def _write(action: Callable[[], Awaitable[WriteResult]]) -> WriteResult:
    """Exécute le geste ; traduit ses refus en refus nommés pour l'agent."""
    try:
        return await action()
    except UnknownFieldsError as exc:
        raise PrimitiveError("field_not_found", fields=exc.fields) from None
    except AssetOriginMissingError as exc:
        raise PrimitiveError("asset_origin_missing", path=exc.path) from None
    except AssetNotRetainedError as exc:
        raise PrimitiveError("asset_not_retained", path=exc.path) from None
    except WorkdirError as exc:
        raise PrimitiveError(exc.code) from None


def _written(result: WriteResult) -> dict[str, Any]:
    return {
        "view": "draft",
        "changed": result.revision is not None,
        "revision": result.revision,
        "changed_paths": result.paths,
    }


async def _asset_after(ctx: PrimitiveContext, args: ObjectWriteArgs) -> dict[str, Any] | None:
    """L'asset tel qu'il est après l'écriture (vue brouillon), None s'il n'existe plus."""
    try:
        return await reads.read_asset(
            ctx,
            ObjectArgs(
                space_id=args.space_id,
                branch_id=args.branch_id,
                view="draft",
                source=args.source,
                path=args.path,
            ),
        )
    except PrimitiveError as exc:
        if exc.code == "asset_not_retained":
            return None
        raise


# --- Curation -------------------------------------------------------------------------------


async def retain_asset(ctx: PrimitiveContext, args: RetainArgs) -> dict[str, Any]:
    _check_path(args.path)
    bv, user = await open_for_write(ctx, args)
    await _source_ready(bv, args.source)
    target = _target(ctx, bv, user.email)
    result = await _write(
        lambda: curation_actions.retain(target, args.source, args.path, args.fields)
    )
    return {**_written(result), "asset": await _asset_after(ctx, args)}


async def remove_asset(ctx: PrimitiveContext, args: ObjectWriteArgs) -> dict[str, Any]:
    _check_path(args.path)
    bv, user = await open_for_write(ctx, args)
    await _source_ready(bv, args.source)
    target = _target(ctx, bv, user.email)
    result = await _write(lambda: curation_actions.remove(target, args.source, args.path))
    return {**_written(result), "asset": await _asset_after(ctx, args)}


async def enrich_asset(ctx: PrimitiveContext, args: EnrichArgs) -> dict[str, Any]:
    _check_path(args.path)
    bv, user = await open_for_write(ctx, args)
    await _source_ready(bv, args.source)
    target = _target(ctx, bv, user.email)
    object_values = args.object.model_dump(exclude_unset=True) if args.object else None
    field_values = {name: v.model_dump(exclude_unset=True) for name, v in args.fields.items()}
    result = await _write(
        lambda: curation_actions.enrich(
            target, args.source, args.path, object_values, field_values
        )
    )
    return {**_written(result), "asset": await _asset_after(ctx, args)}


async def refresh_assets(ctx: PrimitiveContext, args: RefreshArgs) -> dict[str, Any]:
    for path in args.paths or []:
        _check_path(path)
    bv, user = await open_for_write(ctx, args)
    await _source_ready(bv, args.source)
    target = _target(ctx, bv, user.email)
    result = await _write(lambda: curation_actions.refresh(target, args.source, args.paths))
    return {**_written(result), "refreshed": [p.rsplit("/", 1)[-1] for p in result.paths]}
