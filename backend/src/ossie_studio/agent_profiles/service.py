from __future__ import annotations

import uuid

import asyncpg

from ossie_studio.mcp_surface.catalog import ALL_PRIMITIVES, READ_PRIMITIVES


async def load_agent_primitives(conn: asyncpg.Connection, profile_id: uuid.UUID) -> set[str]:
    """Primitives qu'un profil agent autorise, relues à chaque appel.

    Seules comptent les primitives encore au catalogue (un identifiant retiré du
    code ne donne plus rien). Un profil « toutes les primitives de lecture »
    reçoit en plus chaque lecture du catalogue, y compris les nouvelles ; tout
    autre profil n'a que ce qu'on lui a coché (fermé par défaut). Profil
    introuvable → rien."""
    row = await conn.fetchrow(
        "SELECT primitives, all_read FROM agent_profiles WHERE id = $1", profile_id
    )
    if row is None:
        return set()
    primitives = set(row["primitives"] or []) & ALL_PRIMITIVES
    if row["all_read"]:
        primitives |= READ_PRIMITIVES
    return primitives
