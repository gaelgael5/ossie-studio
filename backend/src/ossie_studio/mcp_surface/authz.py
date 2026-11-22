"""Point de contrôle unique des droits sur la surface MCP.

Relu à chaque appel, jamais figé : retirer une primitive à un profil agent, ou
une capacité à une personne, vaut dès l'appel suivant.
"""

from __future__ import annotations

import uuid

import asyncpg

from ossie_studio.agent_profiles.service import load_agent_primitives
from ossie_studio.auth.service import db_get_user_by_id
from ossie_studio.authz.catalog import SPACE_SCOPED
from ossie_studio.mcp_surface.caller import McpCaller
from ossie_studio.mcp_surface.catalog import BY_ID, PERSON_ONLY
from ossie_studio.models import User
from ossie_studio.profiles.service import load_capabilities, load_capabilities_by_space


async def capabilities_anywhere(conn: asyncpg.Connection, user: User) -> set[str]:
    """Capacités d'une personne, globales ou gagnées sur au moins un espace."""
    caps = await load_capabilities(conn, user)
    for space_caps in (await load_capabilities_by_space(conn, user.id)).values():
        caps |= space_caps
    return caps


async def caller_user(conn: asyncpg.Connection, caller: McpCaller) -> User | None:
    return await db_get_user_by_id(conn, caller.user_id) if caller.user_id else None


async def allowed_primitives(conn: asyncpg.Connection, caller: McpCaller) -> set[str]:
    """Primitives que l'appelant peut voir et appeler.

    Clé d'API : celles de son profil agent, hors primitives propres au brouillon
    d'une personne. Connexion OAuth : celles de son profil agent dont la
    personne a la capacité supposée, globalement ou sur au moins un espace
    (l'appel sur un espace précis est revérifié par `authorize`). Personne
    disparue → rien."""
    primitives = await load_agent_primitives(conn, caller.agent_profile_id)
    if caller.user_id is None:
        return primitives - PERSON_ONLY
    user = await caller_user(conn, caller)
    if user is None:
        return set()
    caps = await capabilities_anywhere(conn, user)
    return {p for p in primitives if BY_ID[p].capability in caps}


async def authorize(
    conn: asyncpg.Connection,
    caller: McpCaller,
    primitive_id: str,
    space_id: uuid.UUID | None = None,
) -> bool:
    """L'appelant peut-il exécuter cette primitive (sur cet espace) ?

    Pour une connexion OAuth et une capacité déclinable par espace, la personne
    doit l'avoir **sur l'espace visé** (profil de base + équipes rattachées),
    comme dans l'IHM. Une clé d'API voit tous les espaces."""
    if primitive_id not in await allowed_primitives(conn, caller):
        return False
    capability = BY_ID[primitive_id].capability
    if caller.user_id is None or space_id is None or capability not in SPACE_SCOPED:
        return True
    user = await caller_user(conn, caller)
    return user is not None and capability in await load_capabilities(conn, user, space_id)
