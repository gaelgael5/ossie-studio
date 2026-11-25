"""Profils agents : quelles primitives MCP un agent peut appeler.

Un profil agent ne s'assigne jamais à une personne : il se choisit à l'émission
d'une clé d'API ou à la connexion OAuth d'un client. Le catalogue des
primitives vit dans le code (`mcp_surface/catalog.py`) ; un profil n'en stocke
que des identifiants, et l'option « toutes les primitives de lecture ».

Les ensembles du catalogue sont relus à chaque calcul (attribut du module), pour
qu'une primitive ajoutée au catalogue ne soit donnée qu'aux profils qui la
reçoivent par règle (fermé par défaut).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import asyncpg

from ossie_studio.mcp_surface import catalog
from ossie_studio.models import User
from ossie_studio.profiles.service import load_capabilities


class AgentProfileNameTakenError(Exception):
    pass


class AgentProfileSlugTakenError(Exception):
    pass


def _effective(primitives: list[str], all_read: bool) -> set[str]:
    granted = set(primitives) & catalog.ALL_PRIMITIVES
    return granted | catalog.READ_PRIMITIVES if all_read else granted


async def load_agent_primitives(conn: asyncpg.Connection, profile_id: uuid.UUID) -> set[str]:
    """Primitives qu'un profil agent autorise, relues à chaque appel.

    Seules comptent les primitives encore au catalogue (un identifiant retiré du
    code ne donne plus rien). Un profil « toutes les primitives de lecture »
    reçoit en plus chaque lecture du catalogue, y compris les nouvelles ; tout
    autre profil n'a que ce qu'on lui a coché (fermé par défaut). Profil
    introuvable ou supprimé → rien."""
    row = await conn.fetchrow(
        "SELECT primitives, all_read FROM agent_profiles WHERE id = $1 AND deleted_at IS NULL",
        profile_id,
    )
    if row is None:
        return set()
    return _effective(row["primitives"] or [], row["all_read"])


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

    def key_primitives(self) -> set[str]:
        """Ce que ce profil donne à une clé d'API (sans personne, donc sans brouillon)."""
        return self.effective_primitives() - catalog.PERSON_ONLY


@dataclass(frozen=True)
class AgentProfileUsage:
    """Clés et connexions ACTIVES qui portent un profil agent."""

    keys: list[str]  # libellés des clés
    connections: list[str]  # « client (personne) »

    @property
    def in_use(self) -> bool:
        return bool(self.keys or self.connections)


async def db_list_agent_profiles(conn: asyncpg.Connection) -> list[AgentProfile]:
    rows = await conn.fetch(
        "SELECT * FROM agent_profiles WHERE deleted_at IS NULL ORDER BY is_builtin DESC, name"
    )
    return [AgentProfile.from_row(row) for row in rows]


async def db_get_agent_profile(
    conn: asyncpg.Connection, profile_id: uuid.UUID
) -> AgentProfile | None:
    row = await conn.fetchrow(
        "SELECT * FROM agent_profiles WHERE id = $1 AND deleted_at IS NULL", profile_id
    )
    return AgentProfile.from_row(row) if row is not None else None


def _taken(exc: asyncpg.UniqueViolationError) -> Exception:
    if exc.constraint_name == "uq_agent_profile_slug":
        return AgentProfileSlugTakenError()
    return AgentProfileNameTakenError()


async def db_create_agent_profile(
    conn: asyncpg.Connection,
    *,
    name: str,
    slug: str,
    description: str,
    primitives: list[str],
    all_read: bool,
) -> AgentProfile:
    try:
        row = await conn.fetchrow(
            "INSERT INTO agent_profiles (name, slug, description, primitives, all_read) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING *",
            name,
            slug,
            description,
            primitives,
            all_read,
        )
    except asyncpg.UniqueViolationError as exc:
        raise _taken(exc) from exc
    return AgentProfile.from_row(row)


async def db_update_agent_profile(
    conn: asyncpg.Connection,
    profile_id: uuid.UUID,
    *,
    name: str,
    description: str,
    primitives: list[str],
    all_read: bool,
) -> AgentProfile:
    try:
        row = await conn.fetchrow(
            "UPDATE agent_profiles SET name = $2, description = $3, primitives = $4, "
            "all_read = $5, updated_at = now() WHERE id = $1 AND deleted_at IS NULL RETURNING *",
            profile_id,
            name,
            description,
            primitives,
            all_read,
        )
    except asyncpg.UniqueViolationError as exc:
        raise _taken(exc) from exc
    assert row is not None
    return AgentProfile.from_row(row)


async def db_delete_agent_profile(conn: asyncpg.Connection, profile_id: uuid.UUID) -> None:
    await conn.execute(
        "UPDATE agent_profiles SET deleted_at = now() WHERE id = $1 AND deleted_at IS NULL",
        profile_id,
    )


async def db_free_agent_profile_slug(conn: asyncpg.Connection, base: str) -> str:
    """Slug libre parmi les profils actifs (suffixe -2, -3… au besoin)."""
    slug, counter = base, 2
    while await conn.fetchval(
        "SELECT 1 FROM agent_profiles WHERE slug = $1 AND deleted_at IS NULL", slug
    ):
        slug, counter = f"{base}-{counter}", counter + 1
    return slug


async def db_agent_profile_usage(
    conn: asyncpg.Connection, profile_id: uuid.UUID
) -> AgentProfileUsage:
    keys = await conn.fetch(
        "SELECT label FROM api_keys WHERE agent_profile_id = $1 AND revoked_at IS NULL "
        "ORDER BY label",
        profile_id,
    )
    connections = await conn.fetch(
        "SELECT cl.client_name, cl.client_id, u.email FROM oauth_connections c "
        "JOIN oauth_clients cl ON cl.client_id = c.client_id "
        "JOIN users u ON u.id = c.user_id "
        "WHERE c.agent_profile_id = $1 AND c.revoked_at IS NULL ORDER BY u.email",
        profile_id,
    )
    return AgentProfileUsage(
        keys=[r["label"] for r in keys],
        connections=[f"{r['client_name'] or r['client_id']} ({r['email']})" for r in connections],
    )


# --- Garde contre l'élévation -----------------------------------------------------------


def beyond_capabilities(primitives: set[str], capabilities: set[str]) -> list[str]:
    """Primitives dont la capacité humaine supposée manque."""
    return sorted(p for p in primitives if catalog.BY_ID[p].capability not in capabilities)


async def primitives_beyond(
    conn: asyncpg.Connection, user: User, profile: AgentProfile
) -> list[str]:
    """Primitives que le profil donnerait à une clé, dont la capacité supposée
    manque à la personne.

    Garde contre l'élévation : on ne donne à un compte de service (clé d'API)
    que ce qu'on a soi-même. Une clé voit tous les espaces, donc seules
    comptent les capacités **globales** de la personne (profil de base et
    profils de ses équipes), pas celles gagnées sur un seul espace."""
    return beyond_capabilities(profile.key_primitives(), await load_capabilities(conn, user))


async def added_beyond(
    conn: asyncpg.Connection, user: User, before: set[str], after: set[str]
) -> list[str]:
    """Garde contre l'élévation à la modification d'un profil : les primitives
    qu'il donnerait EN PLUS à une clé doivent supposer des capacités que la
    personne a elle-même (globalement). Sinon, modifier un profil déjà porté par
    une clé serait un moyen de donner à ce compte de service plus que ses
    propres droits. Les primitives propres au brouillon d'une personne ne
    comptent pas : une clé ne les reçoit jamais, un agent pour une personne
    reste borné par elle."""
    added = (after - before) - catalog.PERSON_ONLY
    return beyond_capabilities(added, await load_capabilities(conn, user))
