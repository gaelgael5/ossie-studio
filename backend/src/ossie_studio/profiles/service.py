from __future__ import annotations

import uuid

import asyncpg
import structlog

from ossie_studio.authz.catalog import ALL_CAPABILITIES
from ossie_studio.models import Profile, User

log = structlog.get_logger(__name__)


class ProfileNameTakenError(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


class ProfileNotFoundError(Exception):
    pass


async def load_capabilities(conn: asyncpg.Connection, user: User) -> set[str]:
    """Capacités effectives d'un utilisateur, relues à chaque requête (jamais
    figées en session). Le profil administrateur a toutes les capacités, y
    compris celles ajoutées après coup."""
    if user.profile_id is None:
        return set()
    row = await conn.fetchrow(
        "SELECT is_admin, capabilities FROM profiles WHERE id = $1", user.profile_id
    )
    if row is None:
        return set()
    if row["is_admin"]:
        return set(ALL_CAPABILITIES)
    return set(row["capabilities"] or [])


async def db_list_profiles(conn: asyncpg.Connection) -> list[Profile]:
    rows = await conn.fetch("SELECT * FROM profiles ORDER BY is_admin DESC, name")
    return [Profile.from_row(row) for row in rows]


async def db_get_profile(conn: asyncpg.Connection, profile_id: uuid.UUID) -> Profile | None:
    row = await conn.fetchrow("SELECT * FROM profiles WHERE id = $1", profile_id)
    return Profile.from_row(row) if row is not None else None


async def db_create_profile(
    conn: asyncpg.Connection, name: str, description: str, capabilities: list[str]
) -> Profile:
    try:
        row = await conn.fetchrow(
            "INSERT INTO profiles (name, description, capabilities) "
            "VALUES ($1, $2, $3) RETURNING *",
            name,
            description,
            capabilities,
        )
    except asyncpg.UniqueViolationError as exc:
        raise ProfileNameTakenError(name) from exc
    return Profile.from_row(row)


async def db_update_profile(
    conn: asyncpg.Connection,
    profile_id: uuid.UUID,
    name: str,
    description: str,
    capabilities: list[str],
) -> Profile:
    try:
        row = await conn.fetchrow(
            "UPDATE profiles SET name = $2, description = $3, capabilities = $4, "
            "updated_at = now() WHERE id = $1 RETURNING *",
            profile_id,
            name,
            description,
            capabilities,
        )
    except asyncpg.UniqueViolationError as exc:
        raise ProfileNameTakenError(name) from exc
    if row is None:
        raise ProfileNotFoundError
    return Profile.from_row(row)


async def db_delete_profile(conn: asyncpg.Connection, profile_id: uuid.UUID) -> bool:
    result: str = await conn.execute("DELETE FROM profiles WHERE id = $1", profile_id)
    return result != "DELETE 0"


async def db_count_users_with_profile(conn: asyncpg.Connection, profile_id: uuid.UUID) -> int:
    value = await conn.fetchval("SELECT count(*) FROM users WHERE profile_id = $1", profile_id)
    return int(value or 0)
