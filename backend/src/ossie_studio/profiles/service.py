from __future__ import annotations

import uuid

import asyncpg
import structlog

from ossie_studio.authz.catalog import ALL_CAPABILITIES, SPACE_SCOPED
from ossie_studio.models import Profile, User

log = structlog.get_logger(__name__)


class ProfileNameTakenError(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


class ProfileSlugTakenError(Exception):
    def __init__(self, slug: str) -> None:
        super().__init__(slug)
        self.slug = slug


async def db_free_profile_slug(conn: asyncpg.Connection, base: str) -> str:
    """Slug libre dérivé de `base` (suffixe -2, -3… au besoin) : pour une copie."""
    slug, counter = base, 2
    while await conn.fetchval("SELECT 1 FROM profiles WHERE slug = $1", slug):
        slug, counter = f"{base}-{counter}", counter + 1
    return slug


class ProfileNotFoundError(Exception):
    pass


async def load_capabilities(
    conn: asyncpg.Connection, user: User, space_id: uuid.UUID | None = None
) -> set[str]:
    """Capacités effectives d'un utilisateur, relues à chaque requête (jamais
    figées en session). **Union** du profil personnel et des profils portés par
    **toutes** ses équipes : une équipe ajoute des droits, jamais n'en retire.
    Administrateur si **au moins un** de ces profils est administrateur (il a alors
    toutes les capacités, y compris celles ajoutées après coup).

    Avec `space_id`, s'y ajoutent les capacités **déclinables par espace** des
    profils de ses équipes rattachées à cet espace (`space_team_profiles`). Ce
    terme ne donne jamais une capacité globale : un profil administrateur
    rattaché vaut toutes les capacités déclinables, sur cet espace seulement."""
    caps = await _global_capabilities(conn, user)
    if space_id is not None:
        caps |= await _space_capabilities(conn, user.id, space_id)
    return caps


async def _global_capabilities(conn: asyncpg.Connection, user: User) -> set[str]:
    rows = await conn.fetch(
        # Profil personnel (ignoré si NULL) + profils des équipes de la personne.
        "SELECT p.is_admin, p.capabilities FROM profiles p WHERE p.id = $1 "
        "UNION ALL "
        "SELECT p.is_admin, p.capabilities FROM user_teams ut "
        "JOIN teams t ON t.id = ut.team_id "
        "JOIN profiles p ON p.id = t.profile_id "
        "WHERE ut.user_id = $2",
        user.profile_id,
        user.id,
    )
    if not rows:
        return set()
    if any(row["is_admin"] for row in rows):
        return set(ALL_CAPABILITIES)
    caps: set[str] = set()
    for row in rows:
        caps.update(row["capabilities"] or [])
    return caps


def _scoped(rows: list[asyncpg.Record]) -> set[str]:
    caps: set[str] = set()
    for row in rows:
        caps |= set(SPACE_SCOPED) if row["is_admin"] else set(row["capabilities"] or [])
    return caps & SPACE_SCOPED


async def _space_capabilities(
    conn: asyncpg.Connection, user_id: uuid.UUID, space_id: uuid.UUID
) -> set[str]:
    rows = await conn.fetch(
        "SELECT p.is_admin, p.capabilities FROM space_team_profiles stp "
        "JOIN user_teams ut ON ut.team_id = stp.team_id "
        "JOIN profiles p ON p.id = stp.profile_id "
        "WHERE ut.user_id = $1 AND stp.space_id = $2",
        user_id,
        space_id,
    )
    return _scoped(rows)


async def load_capabilities_by_space(
    conn: asyncpg.Connection, user_id: uuid.UUID
) -> dict[uuid.UUID, set[str]]:
    """Capacités déclinables gagnées par rattachement d'équipe, espace par espace
    (sans le profil de base)."""
    rows = await conn.fetch(
        "SELECT stp.space_id, p.is_admin, p.capabilities FROM space_team_profiles stp "
        "JOIN user_teams ut ON ut.team_id = stp.team_id "
        "JOIN profiles p ON p.id = stp.profile_id "
        "WHERE ut.user_id = $1",
        user_id,
    )
    by_space: dict[uuid.UUID, list[asyncpg.Record]] = {}
    for row in rows:
        by_space.setdefault(row["space_id"], []).append(row)
    return {space: _scoped(space_rows) for space, space_rows in by_space.items()}


async def db_list_profiles(conn: asyncpg.Connection) -> list[Profile]:
    rows = await conn.fetch("SELECT * FROM profiles ORDER BY is_admin DESC, name")
    return [Profile.from_row(row) for row in rows]


async def db_get_profile(conn: asyncpg.Connection, profile_id: uuid.UUID) -> Profile | None:
    row = await conn.fetchrow("SELECT * FROM profiles WHERE id = $1", profile_id)
    return Profile.from_row(row) if row is not None else None


async def db_create_profile(
    conn: asyncpg.Connection,
    name: str,
    description: str,
    capabilities: list[str],
    slug: str | None = None,
) -> Profile:
    try:
        row = await conn.fetchrow(
            "INSERT INTO profiles (name, description, capabilities, slug) "
            "VALUES ($1, $2, $3, coalesce($4, gen_random_uuid()::text)) RETURNING *",
            name,
            description,
            capabilities,
            slug,
        )
    except asyncpg.UniqueViolationError as exc:
        if exc.constraint_name == "uq_profile_slug":
            raise ProfileSlugTakenError(slug or "") from exc
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


async def db_get_default_profile_id(conn: asyncpg.Connection) -> uuid.UUID | None:
    value: uuid.UUID | None = await conn.fetchval(
        "SELECT id FROM profiles WHERE is_default LIMIT 1"
    )
    return value


async def db_set_default_profile(conn: asyncpg.Connection, profile_id: uuid.UUID) -> Profile:
    """Marque un profil par défaut ; un seul à la fois (la marque passe)."""
    async with conn.transaction():
        await conn.execute("UPDATE profiles SET is_default = false WHERE is_default")
        row = await conn.fetchrow(
            "UPDATE profiles SET is_default = true, updated_at = now() WHERE id = $1 RETURNING *",
            profile_id,
        )
    if row is None:
        raise ProfileNotFoundError
    return Profile.from_row(row)
