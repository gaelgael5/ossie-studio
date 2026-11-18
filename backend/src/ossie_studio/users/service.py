from __future__ import annotations

import unicodedata
import uuid

import asyncpg
import structlog

log = structlog.get_logger(__name__)


class UserNotFoundError(Exception):
    pass


class AdminLockoutError(Exception):
    """Dernier utilisateur avec le profil Administrateur : on ne le lui retire pas."""


def fold(text: str) -> str:
    """Minuscules, accents repliés : base d'une recherche insensible à la casse
    et aux accents, sans extension Postgres."""
    normalized = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in normalized if not unicodedata.combining(c))


def search_key(display_name: str, email: str) -> str:
    return fold(f"{display_name} {email}".strip())


def _like_term(query: str) -> str:
    # Échappe les jokers LIKE ; recherche « contient ».
    escaped = fold(query.strip()).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def db_touch_login(conn: asyncpg.Connection, user_id: uuid.UUID) -> None:
    await conn.execute("UPDATE users SET last_login_at = now() WHERE id = $1", user_id)


async def db_search_users(
    conn: asyncpg.Connection,
    query: str,
    profile_id: uuid.UUID | None,
    limit: int,
    offset: int,
    team_id: uuid.UUID | None = None,
) -> tuple[list[asyncpg.Record], int]:
    has_query = query.strip() != ""
    term = _like_term(query)
    rows = await conn.fetch(
        "SELECT u.id, u.email, u.display_name, (u.password_hash IS NULL) AS is_oidc, "
        "u.last_login_at, u.profile_id, u.profile_pinned, "
        "p.name AS profile_name, p.is_admin AS profile_is_admin, "
        "u.team_id, u.team_pinned, tm.name AS team_name, tm.origin AS team_origin "
        "FROM users u LEFT JOIN profiles p ON p.id = u.profile_id "
        "LEFT JOIN teams tm ON tm.id = u.team_id "
        "WHERE (NOT $1 OR u.search_key LIKE $2 ESCAPE '\\') "
        "AND ($3::uuid IS NULL OR u.profile_id = $3) "
        "AND ($6::uuid IS NULL OR u.team_id = $6) "
        "ORDER BY u.email LIMIT $4 OFFSET $5",
        has_query,
        term,
        profile_id,
        limit,
        offset,
        team_id,
    )
    total = await conn.fetchval(
        "SELECT count(*) FROM users u "
        "WHERE (NOT $1 OR u.search_key LIKE $2 ESCAPE '\\') "
        "AND ($3::uuid IS NULL OR u.profile_id = $3) "
        "AND ($4::uuid IS NULL OR u.team_id = $4)",
        has_query,
        term,
        profile_id,
        team_id,
    )
    return list(rows), int(total or 0)


async def db_count_admin_users(conn: asyncpg.Connection) -> int:
    value = await conn.fetchval(
        "SELECT count(*) FROM users u JOIN profiles p ON p.id = u.profile_id WHERE p.is_admin"
    )
    return int(value or 0)


async def db_assign_profile(
    conn: asyncpg.Connection, user_id: uuid.UUID, profile_id: uuid.UUID
) -> tuple[uuid.UUID | None, bool]:
    """Assigne un profil ; renvoie l'ancien profil et s'il était administrateur,
    après avoir vérifié l'anti-verrouillage."""
    async with conn.transaction():
        current = await conn.fetchrow(
            "SELECT u.profile_id, p.is_admin FROM users u "
            "LEFT JOIN profiles p ON p.id = u.profile_id WHERE u.id = $1",
            user_id,
        )
        if current is None:
            raise UserNotFoundError
        old_profile_id = current["profile_id"]
        was_admin = bool(current["is_admin"])
        target = await conn.fetchrow("SELECT is_admin FROM profiles WHERE id = $1", profile_id)
        if target is None:
            raise UserNotFoundError  # profil cible absent → traité comme 404 côté API
        # Retirer l'administrateur au dernier administrateur actif : refusé.
        if was_admin and not target["is_admin"] and await db_count_admin_users(conn) <= 1:
            raise AdminLockoutError
        # Une assignation manuelle fixe le profil : les correspondances OIDC ne
        # le remplacent plus tant que la marque reste.
        await conn.execute(
            "UPDATE users SET profile_id = $2, profile_pinned = true WHERE id = $1",
            user_id,
            profile_id,
        )
    return old_profile_id, was_admin


async def db_set_pin(conn: asyncpg.Connection, user_id: uuid.UUID, pinned: bool) -> bool:
    result: str = await conn.execute(
        "UPDATE users SET profile_pinned = $2 WHERE id = $1", user_id, pinned
    )
    return result != "UPDATE 0"


async def db_count_pinned_users(conn: asyncpg.Connection) -> int:
    value = await conn.fetchval("SELECT count(*) FROM users WHERE profile_pinned")
    return int(value or 0)
