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

import re
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
from ossie_studio.mcp_surface.core import PrimitiveContext, PrimitiveError, agent_name
from ossie_studio.sources import curation_actions
from ossie_studio.sources.curation import (
    AssetNotRetainedError,
    AssetOriginMissingError,
    ReviewDecisions,
    is_schema_file,
)
from ossie_studio.sources.curation_actions import CurationTarget, UnknownFieldsError, WriteResult
from ossie_studio.sources.manifest import ManifestError
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


class TriRuleArg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str = Field(description="What the pattern matches, as in get_tri_rules.")
    pattern: str
    kind: str = Field(description="'glob' or 'regex'.")
    label: str = ""


class SetTriRulesArgs(SourceWriteArgs):
    rules: list[TriRuleArg] = Field(
        description="The complete list of rules (replaces the current one; empty = none)."
    )


class ReviewFieldRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    name: str


class ApplyReviewArgs(SourceWriteArgs):
    head_sha: str = Field(description="head_sha returned by get_review.")
    apply_fields: list[ReviewFieldRef] = Field(
        default_factory=list, description="Changed retained fields to refresh from the raw."
    )
    take_fields: list[ReviewFieldRef] = Field(
        default_factory=list, description="Appeared fields to add to the asset."
    )
    remove_fields: list[ReviewFieldRef] = Field(
        default_factory=list, description="Disappeared fields to remove from the asset."
    )
    remove_objects: list[str] = Field(
        default_factory=list, description="Assets whose raw object disappeared, to remove."
    )
    retain_objects: list[str] = Field(
        default_factory=list, description="New raw objects to retain as assets."
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


def write_target(ctx: PrimitiveContext, bv: BranchView, author: str) -> CurationTarget:
    return CurationTarget(
        bv.workdir,
        bv.space,
        bv.branch.git_ref,
        bv.username,
        bv.token,
        author,
        via=f"via l'agent {agent_name(ctx)}",
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


def written_payload(result: WriteResult) -> dict[str, Any]:
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
    target = write_target(ctx, bv, user.email)
    result = await _write(
        lambda: curation_actions.retain(target, args.source, args.path, args.fields)
    )
    return {**written_payload(result), "asset": await _asset_after(ctx, args)}


async def remove_asset(ctx: PrimitiveContext, args: ObjectWriteArgs) -> dict[str, Any]:
    _check_path(args.path)
    bv, user = await open_for_write(ctx, args)
    await _source_ready(bv, args.source)
    target = write_target(ctx, bv, user.email)
    result = await _write(lambda: curation_actions.remove(target, args.source, args.path))
    return {**written_payload(result), "asset": await _asset_after(ctx, args)}


async def enrich_asset(ctx: PrimitiveContext, args: EnrichArgs) -> dict[str, Any]:
    _check_path(args.path)
    bv, user = await open_for_write(ctx, args)
    await _source_ready(bv, args.source)
    target = write_target(ctx, bv, user.email)
    object_values = args.object.model_dump(exclude_unset=True) if args.object else None
    field_values = {name: v.model_dump(exclude_unset=True) for name, v in args.fields.items()}
    result = await _write(
        lambda: curation_actions.enrich(target, args.source, args.path, object_values, field_values)
    )
    return {**written_payload(result), "asset": await _asset_after(ctx, args)}


async def refresh_assets(ctx: PrimitiveContext, args: RefreshArgs) -> dict[str, Any]:
    for path in args.paths or []:
        _check_path(path)
    bv, user = await open_for_write(ctx, args)
    await _source_ready(bv, args.source)
    target = write_target(ctx, bv, user.email)
    result = await _write(lambda: curation_actions.refresh(target, args.source, args.paths))
    return {**written_payload(result), "refreshed": [p.rsplit("/", 1)[-1] for p in result.paths]}


# --- Règles de tri et revue -------------------------------------------------------------------

_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")


async def set_tri_rules(ctx: PrimitiveContext, args: SetTriRulesArgs) -> dict[str, Any]:
    bv, user = await open_for_write(ctx, args)
    await _source_ready(bv, args.source)
    target = write_target(ctx, bv, user.email)
    try:
        result, rules = await curation_actions.set_tri_rules(
            target, args.source, [rule.model_dump() for rule in args.rules]
        )
    except curation_actions.RulesInvalidError as exc:
        raise PrimitiveError("tri_rules_invalid", fields=exc.fields) from None
    except curation_actions.SourceMissingError:
        raise PrimitiveError("source_not_found", source=args.source) from None
    except ManifestError as exc:
        raise PrimitiveError("manifest_invalid", fields=exc.fields) from None
    except WorkdirError as exc:
        raise PrimitiveError(exc.code) from None
    return {**written_payload(result), "rules": rules}


def _group(refs: list[ReviewFieldRef]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for ref in refs:
        grouped.setdefault(ref.path, []).append(ref.name)
    return grouped


async def apply_review(ctx: PrimitiveContext, args: ApplyReviewArgs) -> dict[str, Any]:
    if not _SHA_RE.match(args.head_sha):
        raise PrimitiveError("invalid_arguments", fields=["head_sha"])
    refs = [*args.apply_fields, *args.take_fields, *args.remove_fields]
    for path in (*(r.path for r in refs), *args.remove_objects, *args.retain_objects):
        _check_path(path)
    bv, user = await open_for_write(ctx, args)
    await _source_ready(bv, args.source)
    decisions = ReviewDecisions(
        apply_fields=_group(args.apply_fields),
        take_fields=_group(args.take_fields),
        remove_fields=_group(args.remove_fields),
        remove_objects=list(dict.fromkeys(args.remove_objects)),
        retain_objects=list(dict.fromkeys(args.retain_objects)),
    )
    # La revue que l'agent a lue (même calcul que get_review) : ses décisions
    # doivent toutes y correspondre, sinon rien n'est écrit.
    head_sha, review = await reads.compute_review(bv, args.source)
    if head_sha != args.head_sha:
        raise PrimitiveError("review_stale")
    invalid = curation_actions.invalid_review_decisions(review, decisions)
    if invalid:
        raise PrimitiveError("review_decision_invalid", decisions=invalid)
    target = write_target(ctx, bv, user.email)
    result = await _write(
        lambda: curation_actions.apply_review(target, args.source, args.head_sha, decisions)
    )
    after_head, after = await reads.compute_review(bv, args.source)
    after_review = reads.review_payload(after_head, after)
    return {**written_payload(result), "review": after_review}
