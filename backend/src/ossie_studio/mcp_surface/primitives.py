"""Exécution des primitives MCP : arguments attendus et code de chacune.

Une primitive ne vérifie pas les droits de l'appelant : le point de contrôle
(`authz.authorize`) l'a fait avant de l'appeler. Elle respecte en revanche les
règles des espaces (espace ou branche introuvable…) et les signale par une
`PrimitiveError` nommée, rendue telle quelle à l'agent.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import asyncpg
from pydantic import BaseModel, ConfigDict
from starlette.datastructures import State

from ossie_studio.mcp_surface.caller import McpCaller
from ossie_studio.models import User
from ossie_studio.spaces.branch_service import db_list_branches
from ossie_studio.spaces.service import db_list_spaces, list_visible_spaces


class PrimitiveError(Exception):
    """Refus nommé d'une primitive (code stable + détails), lu par l'agent."""

    def __init__(self, code: str, **detail: object) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class PrimitiveContext:
    conn: asyncpg.Connection
    caller: McpCaller
    # Connexion OAuth : la personne pour qui l'agent agit. Clé d'API : None.
    user: User | None
    state: State


class NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class Implementation:
    args_model: type[BaseModel]
    run: Callable[[PrimitiveContext, Any], Awaitable[dict[str, Any]]]


# --- Se repérer ---------------------------------------------------------------


async def list_spaces(ctx: PrimitiveContext, _args: NoArgs) -> dict[str, Any]:
    # Clé d'API : tous les espaces. Connexion OAuth : ceux que la personne voit.
    if ctx.user is None:
        spaces = await db_list_spaces(ctx.conn)
    else:
        spaces = await list_visible_spaces(ctx.conn, ctx.user)
    out: list[dict[str, Any]] = []
    for space in spaces:
        branches = await db_list_branches(ctx.conn, space.id)
        out.append(
            {
                "id": str(space.id),
                "name": space.name,
                "slug": space.slug,
                "branches": [
                    {"id": str(b.id), "name": b.git_ref, "kind": b.kind} for b in branches
                ],
            }
        )
    return {"spaces": out}


IMPLEMENTATIONS: Mapping[str, Implementation] = {
    "list_spaces": Implementation(NoArgs, list_spaces),
}
