"""Primitives MCP de lecture des référentiels (epic « Référentiels »).

Par défaut, même lecture que l'API (`referentiels/published.py`) : la version
publiée de l'espace, avec le commit publié lu. Ouvertes aux clés d'API comme aux
connexions OAuth.

Paramètre `branch` commun (ticket aa19569b) : `draft` lit la branche racine de
l'espace (là où vivent les référentiels) avec le brouillon de la personne ; ce
qui en vient est marqué `unpublished`, et `commit` est vide puisque ce contenu
n'est pas un commit publié.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import Field

from ossie_studio.errors import ApiError
from ossie_studio.mcp_surface.branch import BranchParam, check_branch, open_view
from ossie_studio.mcp_surface.core import PrimitiveContext, PrimitiveError
from ossie_studio.referentiels import published
from ossie_studio.referentiels.errors import ReferentielError
from ossie_studio.referentiels.repo import referentiel_dir
from ossie_studio.spaces.branch_service import db_get_base
from ossie_studio.spaces.workdir import WorkdirError

MAX_SEARCH = 100

Scalar = str | int | float | bool


class SpaceArgs(BranchParam):
    space_id: uuid.UUID = Field(description="Space id (from list_spaces).")


class ReferentielArgs(SpaceArgs):
    name: str = Field(description="Reference data set name (from list_referentiels).")


class SearchValuesArgs(ReferentielArgs):
    filters: dict[str, Scalar] = Field(
        default_factory=dict,
        description=(
            "Equality filters on simple top-level properties, e.g. {'pays': 'FR'}; all must match."
        ),
    )
    limit: int = Field(default=20, ge=1, le=MAX_SEARCH, description="Maximum values returned.")
    offset: int = Field(default=0, ge=0, description="Values to skip (for the next page).")


class ValueByKeyArgs(ReferentielArgs):
    key: dict[str, Scalar] = Field(
        description=(
            "Value of every key property (see 'key' in list_referentiels); a composite key "
            "needs all its properties, e.g. {'pays': 'FR', 'mode': 'site'}."
        )
    )


@dataclass(frozen=True)
class _Read[T]:
    data: T
    # Commit publié lu ; vide en vue brouillon (contenu non publié).
    commit: str
    view: str
    # Chemins du brouillon lu (vue brouillon seulement).
    draft_paths: frozenset[str] = frozenset()

    def unpublished(self, name: str) -> bool:
        prefix = referentiel_dir(name) + "/"
        return any(p.startswith(prefix) for p in self.draft_paths)

    def head(self) -> dict[str, Any]:
        # Vue publiée : exactement la réponse de l'API (même lecture, ticket e65b590b).
        if self.view == "draft":
            return {"view": "draft", "commit": self.commit}
        return {"commit": self.commit}


async def _read[T](
    ctx: PrimitiveContext, args: SpaceArgs, fn: Callable[[Path], T], empty: T
) -> _Read[T]:
    try:
        if check_branch(args.branch):
            base = await db_get_base(ctx.conn, args.space_id)
            if base is None:
                return _Read(empty, "", "draft")
            bv = await open_view(ctx, args.space_id, base.id, with_draft=True)
            return _Read(await bv.read(fn), "", "draft", bv.draft_paths)
        read = await published.read_published(
            ctx.conn, ctx.state.crypto, ctx.state.workdir_manager, args.space_id, fn, empty
        )
        return _Read(read.data, read.commit, "published")
    except ReferentielError as exc:
        raise PrimitiveError(exc.code, **exc.detail) from None
    except ApiError as exc:
        raise PrimitiveError(exc.code, **exc.args_dict) from None
    except WorkdirError as exc:
        raise PrimitiveError(exc.code) from None


def _found[T](read: _Read[T | None], name: str) -> T:
    if read.data is None:
        raise PrimitiveError("referentiel_not_found", name=name)
    return read.data


def _named(read: _Read[Any], name: str) -> dict[str, Any]:
    out = {**read.head(), "name": name}
    if read.view == "draft":
        out["unpublished"] = read.unpublished(name)
    return out


async def list_referentiels(ctx: PrimitiveContext, args: SpaceArgs) -> dict[str, Any]:
    empty: list[dict[str, Any]] = []
    read = await _read(ctx, args, published.summaries, empty)
    items = read.data
    if read.view == "draft":
        items = [{**r, "unpublished": read.unpublished(r["name"])} for r in items]
    return {**read.head(), "referentiels": items}


async def read_referentiel_model(ctx: PrimitiveContext, args: ReferentielArgs) -> dict[str, Any]:
    read = await _read(ctx, args, lambda root: published.model_of(root, args.name), None)
    return {**_named(read, args.name), "model": _found(read, args.name)}


async def search_referentiel_values(
    ctx: PrimitiveContext, args: SearchValuesArgs
) -> dict[str, Any]:
    read = await _read(
        ctx,
        args,
        lambda root: published.search_values(
            root, args.name, args.filters, args.limit, args.offset
        ),
        None,
    )
    return {**_named(read, args.name), **_found(read, args.name)}


async def get_referentiel_value(ctx: PrimitiveContext, args: ValueByKeyArgs) -> dict[str, Any]:
    read = await _read(
        ctx, args, lambda root: published.value_by_key(root, args.name, args.key), None
    )
    return {**_named(read, args.name), "value": _found(read, args.name)}
