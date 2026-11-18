from __future__ import annotations

import asyncio
import uuid

import asyncpg
import structlog

from ossie_studio.models import ROLE_ADMIN, User
from ossie_studio.security import (
    hash_password,
    verify_password,
    waste_time_like_a_verification,
)

log = structlog.get_logger(__name__)

_UNIQUE_VIOLATION = "23505"


class BootstrapConflictError(Exception):
    """Un administrateur issu de l'écran de bootstrap existe déjà."""


async def db_count_users(conn: asyncpg.Connection) -> int:
    value = await conn.fetchval("SELECT count(*) FROM users")
    return int(value)


async def db_get_user_by_email(conn: asyncpg.Connection, email: str) -> User | None:
    row = await conn.fetchrow("SELECT * FROM users WHERE email = $1", email)
    return User.from_row(row) if row is not None else None


async def db_get_user_by_id(conn: asyncpg.Connection, user_id: uuid.UUID) -> User | None:
    row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    return User.from_row(row) if row is not None else None


async def db_create_bootstrap_admin(
    conn: asyncpg.Connection, email: str, password_hash: str
) -> User:
    """Insère l'administrateur de bootstrap ; la base tranche l'unicité.

    Deux créations concurrentes passent toutes les deux une lecture préalable :
    c'est l'index unique partiel sur `is_bootstrap` qui refuse la seconde.
    """
    try:
        row = await conn.fetchrow(
            "INSERT INTO users (email, password_hash, role, is_bootstrap, profile_id) "
            "VALUES ($1, $2, $3, true, (SELECT id FROM profiles WHERE is_admin)) RETURNING *",
            email,
            password_hash,
            ROLE_ADMIN,
        )
    except asyncpg.UniqueViolationError as exc:
        raise BootstrapConflictError from exc
    return User.from_row(row)


async def resolve_bootstrap_admin(conn: asyncpg.Connection, email: str, password: str) -> User:
    """Crée le premier administrateur et rend le compte prêt à l'emploi."""
    password_hash = await asyncio.to_thread(hash_password, password)
    user = await db_create_bootstrap_admin(conn, email, password_hash)
    log.info("bootstrap_admin_created", user_id=str(user.id))
    return user


async def resolve_login(conn: asyncpg.Connection, email: str, password: str) -> User | None:
    """Authentifie en local ; None si email inconnu ou mot de passe faux."""
    user = await db_get_user_by_email(conn, email)
    if user is None:
        await asyncio.to_thread(waste_time_like_a_verification)
        return None
    if not await asyncio.to_thread(verify_password, user.password_hash, password):
        return None
    return user
