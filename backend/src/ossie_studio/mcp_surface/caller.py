"""L'appelant d'une requête MCP : qui il est, et pour qui il agit.

La garde d'authentification (`guard.py`) le résout à partir du jeton présenté,
puis le pose dans une variable de contexte lue par les primitives. Deux sortes :

- **clé d'API** (`key`) : compte de service, personne derrière ; droits = le
  profil agent de la clé ;
- **connexion OAuth** (`oauth`) : un client agit pour une personne ; droits =
  le profil agent choisi, borné par les capacités de cette personne.

Les résolveurs (un par sorte de jeton) sont branchés par les tickets clés d'API
et OAuth ; sans résolveur, aucun jeton n'est accepté (fermé par défaut).
"""

from __future__ import annotations

import contextvars
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

import asyncpg

CallerKind = Literal["key", "oauth"]


@dataclass(frozen=True)
class McpCaller:
    kind: CallerKind
    # Identifiant de la clé ou de la connexion : sert au journal, jamais le jeton.
    ref: str
    agent_profile_id: uuid.UUID
    # Connexion OAuth : la personne pour qui l'agent agit, et son client.
    user_id: uuid.UUID | None = None
    client_id: str | None = None

    def log_fields(self) -> dict[str, str | None]:
        return {
            "caller_kind": self.kind,
            "caller_ref": self.ref,
            "user_id": str(self.user_id) if self.user_id else None,
            "client_id": self.client_id,
        }


# Résolveur : jeton présenté → appelant, ou None s'il ne le reconnaît pas.
CallerResolver = Callable[[asyncpg.Connection, str], Awaitable[McpCaller | None]]

current_caller: contextvars.ContextVar[McpCaller | None] = contextvars.ContextVar(
    "mcp_current_caller", default=None
)
