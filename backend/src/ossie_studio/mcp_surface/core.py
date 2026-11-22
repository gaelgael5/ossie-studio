"""Noyau des primitives MCP : contexte d'exécution, refus nommé, déclaration
d'une implémentation (arguments attendus + code)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import asyncpg
from pydantic import BaseModel, ConfigDict
from starlette.datastructures import State

from ossie_studio.mcp_surface.caller import McpCaller
from ossie_studio.models import User


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
