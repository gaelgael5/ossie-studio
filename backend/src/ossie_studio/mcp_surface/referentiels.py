"""Primitives MCP de lecture des référentiels publiés (epic « Référentiels »).

Même lecture que l'API (`referentiels/published.py`) : version publiée
uniquement, jamais un brouillon ; chaque réponse porte le commit publié lu.
Ouvertes aux clés d'API comme aux connexions OAuth (pas de brouillon en jeu).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ossie_studio.errors import ApiError
from ossie_studio.mcp_surface.core import PrimitiveContext, PrimitiveError
from ossie_studio.referentiels import published
from ossie_studio.referentiels.errors import ReferentielError
from ossie_studio.spaces.workdir import WorkdirError

MAX_SEARCH = 100

Scalar = str | int | float | bool


class SpaceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
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


async def _read[T](
    ctx: PrimitiveContext, space_id: uuid.UUID, fn: Callable[[Path], T], empty: T
) -> published.Published[T]:
    try:
        return await published.read_published(
            ctx.conn, ctx.state.crypto, ctx.state.workdir_manager, space_id, fn, empty
        )
    except ReferentielError as exc:
        raise PrimitiveError(exc.code, **exc.detail) from None
    except ApiError as exc:
        raise PrimitiveError(exc.code, **exc.args_dict) from None
    except WorkdirError as exc:
        raise PrimitiveError(exc.code) from None


def _found[T](read: published.Published[T | None], name: str) -> T:
    if read.data is None:
        raise PrimitiveError("referentiel_not_found", name=name)
    return read.data


async def list_referentiels(ctx: PrimitiveContext, args: SpaceArgs) -> dict[str, Any]:
    empty: list[dict[str, Any]] = []
    read = await _read(ctx, args.space_id, published.summaries, empty)
    return {"commit": read.commit, "referentiels": read.data}


async def read_referentiel_model(ctx: PrimitiveContext, args: ReferentielArgs) -> dict[str, Any]:
    read = await _read(ctx, args.space_id, lambda root: published.model_of(root, args.name), None)
    return {"commit": read.commit, "name": args.name, "model": _found(read, args.name)}


async def search_referentiel_values(
    ctx: PrimitiveContext, args: SearchValuesArgs
) -> dict[str, Any]:
    read = await _read(
        ctx,
        args.space_id,
        lambda root: published.search_values(
            root, args.name, args.filters, args.limit, args.offset
        ),
        None,
    )
    return {"commit": read.commit, "name": args.name, **_found(read, args.name)}


async def get_referentiel_value(ctx: PrimitiveContext, args: ValueByKeyArgs) -> dict[str, Any]:
    read = await _read(
        ctx, args.space_id, lambda root: published.value_by_key(root, args.name, args.key), None
    )
    return {"commit": read.commit, "name": args.name, "value": _found(read, args.name)}
