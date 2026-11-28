"""Primitives de préparation de la publication : conflits avec le publié,
impacts, journal de validation.

Même service que l'écran et la publication (`drafts/preparation.py`), mêmes
refus nommés. Réservées à un agent connecté pour une personne : il n'y a de
brouillon que le sien. Plus strict que l'écran : résoudre un objet qui n'a pas
bougé côté publié, ou choisir sur une propriété qui n'est pas en conflit, est
refusé. Les décisions (impact vérifié) gardent le nom de l'agent ; il apparaît dans
le message du commit de publication. Le journal de validation suit la règle d'or
(ticket d7139b17) : rien ne s'y accepte. Il rend le même état que la page de
publication (ticket 48618517) : demander une passe, sans l'attendre.

Rien ne se publie ici.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ossie_studio.doc_index.queue import PUBLICATION_PAGE, request_pass
from ossie_studio.doc_index.state import DEFAULT_LIMIT, MAX_LIMIT
from ossie_studio.doc_index.store import ViewKey
from ossie_studio.drafts import preparation
from ossie_studio.drafts.merge import MISSING, ObjectMerge
from ossie_studio.drafts.store import db_get_draft
from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.errors import ApiError
from ossie_studio.mcp_surface.branch import BranchArgs, branch_of
from ossie_studio.mcp_surface.core import PrimitiveContext, PrimitiveError, agent_name
from ossie_studio.mcp_surface.reads import person_of, person_workdir
from ossie_studio.models import SpaceBranch

# --- Arguments ------------------------------------------------------------------------


class MergeConflictsArgs(BranchArgs):
    path: str | None = Field(
        default=None,
        description=(
            "One object of the draft: give it to get its three full versions (base, published, "
            "yours) and the proposed merge."
        ),
    )


class ValueChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Any = Field(description="The value to keep at this pointer (any JSON).")


class ResolveMergeArgs(BranchArgs):
    path: str = Field(description="The object in conflict (from get_merge_conflicts).")
    resolutions: dict[str, Literal["theirs", "mine"] | ValueChoice] = Field(
        default_factory=dict,
        description=(
            "For each conflicting JSON pointer: 'theirs' (published version), 'mine' (the "
            "user's draft) or {'value': ...}. Text conflict: use the pointer '' (whole object)."
        ),
    )
    delete: bool | None = Field(
        default=None,
        description=(
            "Object deleted on one side only: true accepts the deletion, false keeps the object."
        ),
    )


class ValidationLogArgs(BranchArgs):
    filter: Literal["all", "problems", "errors", "warnings", "to_validate", "mine"] = Field(
        default="problems",
        description=(
            "Which files to list: all, problems (errors or warnings), errors, warnings, "
            "to_validate, or mine (the user's draft)."
        ),
    )
    search: str = Field(default="", description="Keep files whose path, key or name contains it.")
    limit: int = Field(
        default=DEFAULT_LIMIT, ge=0, le=MAX_LIMIT, description="At most this many files."
    )


class ImpactArgs(BranchArgs):
    path: str = Field(description="The impacted object (from list_impacts).")


# --- Mécanique commune ------------------------------------------------------------------


async def _workdir(ctx: PrimitiveContext, args: BranchArgs) -> tuple[DraftWorkdir, SpaceBranch]:
    """Le brouillon de la personne sur la branche (refus sans personne)."""
    user = person_of(ctx)
    _space, branch, _username, _token = await branch_of(ctx, args.space_id, args.branch_id)
    return person_workdir(ctx, user, branch), branch


async def _has_draft(ctx: PrimitiveContext, workdir: DraftWorkdir, branch: SpaceBranch) -> bool:
    draft = await db_get_draft(ctx.conn, workdir.user_id, branch.id)
    return draft is not None and draft.space_id == branch.space_id


def _refusal(exc: ApiError) -> PrimitiveError:
    """Refus de l'écran → refus nommé pour l'agent (même code, mêmes détails)."""
    return PrimitiveError(exc.code, **exc.args_dict)


async def _run[T](action: Callable[[], Awaitable[T]]) -> T:
    """Exécute le service ; ses refus deviennent des refus nommés."""
    try:
        return await action()
    except ApiError as exc:
        raise _refusal(exc) from None


def _json(value: object) -> object:
    return None if value is MISSING else value


def _expects(merged: ObjectMerge) -> str:
    """Ce que resolve_merge attend pour cet objet."""
    if merged.kind == "conflict":
        return "resolutions"
    if merged.kind == "text_conflict":
        return "resolutions['']"
    return "delete"


def _pending_payload(pending: preparation.PendingMerge) -> dict[str, Any]:
    merged = pending.merged
    item: dict[str, Any] = {
        "path": pending.obj.path,
        "state": pending.obj.state,
        "kind": merged.kind,
        "expects": _expects(merged),
    }
    if merged.kind == "conflict":
        item["conflicts"] = preparation.conflicts_payload(merged)
    else:
        # Repli texte ou objet supprimé d'un côté : les versions entières.
        item["base"] = _json(merged.base)
        item["theirs"] = _json(merged.theirs)
        item["mine"] = _json(merged.mine)
    return item


# --- Fusion -------------------------------------------------------------------------------


async def get_merge_conflicts(ctx: PrimitiveContext, args: MergeConflictsArgs) -> dict[str, Any]:
    workdir, branch = await _workdir(ctx, args)
    if args.path is None and not await _has_draft(ctx, workdir, branch):
        return {"view": "draft", "conflicts": [], "auto_mergeable": []}
    _draft, objects, versions = await _run(
        lambda: preparation.merge_context(
            ctx.conn, ctx.state.crypto, workdir, branch.space_id, branch.id
        )
    )
    if args.path is not None:
        try:
            obj = preparation.find_object(objects, args.path)
        except ApiError as exc:
            raise _refusal(exc) from None
        return {"view": "draft", **preparation.merge_detail(obj, versions[args.path])}
    pending = preparation.pending_merges(objects, versions)
    return {
        "view": "draft",
        "conflicts": [_pending_payload(p) for p in pending if p.needs_decision],
        "auto_mergeable": [p.obj.path for p in pending if not p.needs_decision],
    }


async def resolve_merge(ctx: PrimitiveContext, args: ResolveMergeArgs) -> dict[str, Any]:
    workdir, branch = await _workdir(ctx, args)
    resolutions = {
        pointer: choice.model_dump() if isinstance(choice, ValueChoice) else choice
        for pointer, choice in args.resolutions.items()
    }
    _draft, merged = await _run(
        lambda: preparation.resolve_merge(
            ctx.conn,
            ctx.state.crypto,
            workdir,
            branch.space_id,
            branch.id,
            args.path,
            resolutions,
            args.delete,
            strict=True,
        )
    )
    return {"view": "draft", "path": args.path, "kind": merged.kind, "changed_paths": [args.path]}


async def auto_merge(ctx: PrimitiveContext, args: BranchArgs) -> dict[str, Any]:
    workdir, branch = await _workdir(ctx, args)
    if not await _has_draft(ctx, workdir, branch):
        return {"view": "draft", "merged": [], "conflicts": [], "changed_paths": []}
    result = await _run(
        lambda: preparation.auto_merge_locked(
            ctx.conn, ctx.state.crypto, workdir, branch.space_id, branch.id
        )
    )
    return {"view": "draft", **result, "changed_paths": result["merged"]}


# --- Impacts ------------------------------------------------------------------------------


async def list_impacts(ctx: PrimitiveContext, args: BranchArgs) -> dict[str, Any]:
    workdir, branch = await _workdir(ctx, args)
    _draft, _objects, impacts = await _run(
        lambda: preparation.draft_impacts(
            ctx.conn, ctx.state.crypto, workdir, branch.space_id, branch.id
        )
    )
    return {
        "view": "draft",
        "impacts": [i.payload() for i in impacts],
        "pending": sum(1 for i in impacts if not i.verified),
    }


async def verify_impact(ctx: PrimitiveContext, args: ImpactArgs) -> dict[str, Any]:
    workdir, branch = await _workdir(ctx, args)
    await _run(
        lambda: preparation.verify_impact(
            ctx.conn,
            ctx.state.crypto,
            workdir,
            branch.space_id,
            branch.id,
            args.path,
            via=agent_name(ctx),
        )
    )
    return {"view": "draft", "path": args.path, "verified": True}


# --- Journal de validation ------------------------------------------------------------------


async def get_validation_log(ctx: PrimitiveContext, args: ValidationLogArgs) -> dict[str, Any]:
    workdir, branch = await _workdir(ctx, args)
    key = ViewKey(branch.space_id, branch.id, workdir.user_id)
    await request_pass(ctx.conn, key, PUBLICATION_PAGE)
    state = await preparation.draft_state(ctx.conn, workdir.user_id, branch.space_id, branch.id)
    return state.payload(args.filter, args.search.strip(), args.limit)
