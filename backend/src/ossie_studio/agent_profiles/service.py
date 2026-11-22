from __future__ import annotations

import uuid
from dataclasses import dataclass

import asyncpg

from ossie_studio.mcp_surface.catalog import ALL_PRIMITIVES, BY_ID, READ_PRIMITIVES
from ossie_studio.models import User
from ossie_studio.profiles.service import load_capabilities


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
    return _effective(row["primitives"] or [], row["all_read"])


def _effective(primitives: list[str], all_read: bool) -> set[str]:
    granted = set(primitives) & ALL_PRIMITIVES
    return granted | READ_PRIMITIVES if all_read else granted


@dataclass(frozen=True)
class AgentProfile:
    id: uuid.UUID
    slug: str
    name: str
    description: str
    primitives: list[str]
    all_read: bool
    is_builtin: bool

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> AgentProfile:
        return cls(
            id=row["id"],
            slug=row["slug"],
            name=row["name"],
            description=row["description"],
            primitives=list(row["primitives"] or []),
            all_read=row["all_read"],
            is_builtin=row["is_builtin"],
        )

    def effective_primitives(self) -> set[str]:
        """Même règle que `load_agent_primitives`, sur un profil déjà lu."""
        return _effective(self.primitives, self.all_read)


async def db_list_agent_profiles(conn: asyncpg.Connection) -> list[AgentProfile]:
    rows = await conn.fetch("SELECT * FROM agent_profiles ORDER BY is_builtin DESC, name")
    return [AgentProfile.from_row(row) for row in rows]


async def db_get_agent_profile(
    conn: asyncpg.Connection, profile_id: uuid.UUID
) -> AgentProfile | None:
    row = await conn.fetchrow("SELECT * FROM agent_profiles WHERE id = $1", profile_id)
    return AgentProfile.from_row(row) if row is not None else None


async def primitives_beyond(
    conn: asyncpg.Connection, user: User, profile: AgentProfile
) -> list[str]:
    """Primitives du profil dont la capacité supposée manque à la personne.

    Garde contre l'élévation : on ne donne à un compte de service (clé d'API)
    que ce qu'on a soi-même. Une clé voit tous les espaces, donc seules
    comptent les capacités **globales** de la personne (profil de base et
    profils de ses équipes), pas celles gagnées sur un seul espace."""
    return beyond_capabilities(profile, await load_capabilities(conn, user))


def beyond_capabilities(profile: AgentProfile, capabilities: set[str]) -> list[str]:
    return sorted(
        p for p in profile.effective_primitives() if BY_ID[p].capability not in capabilities
    )
