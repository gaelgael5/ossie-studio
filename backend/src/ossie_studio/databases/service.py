from __future__ import annotations

import uuid
from datetime import UTC, datetime

import asyncpg
import structlog

from ossie_studio.models import DbConnection
from ossie_studio.slugs import slugify

log = structlog.get_logger(__name__)


class DbConnectionDuplicateError(Exception):
    """Une connexion existe déjà sur ce moteur, cette cible et cette identité."""


class DbConnectionSlugTakenError(Exception):
    """Le slug saisi désigne déjà une autre connexion de base."""


async def db_list_db_connections(conn: asyncpg.Connection) -> list[DbConnection]:
    rows = await conn.fetch("SELECT * FROM db_connections ORDER BY label")
    return [DbConnection.from_row(row) for row in rows]


async def db_get_db_connection(
    conn: asyncpg.Connection, connection_id: uuid.UUID
) -> DbConnection | None:
    row = await conn.fetchrow("SELECT * FROM db_connections WHERE id = $1", connection_id)
    return DbConnection.from_row(row) if row is not None else None


async def db_get_db_connection_by_slug(
    conn: asyncpg.Connection, slug: str
) -> DbConnection | None:
    row = await conn.fetchrow("SELECT * FROM db_connections WHERE slug = $1", slug)
    return DbConnection.from_row(row) if row is not None else None


async def db_db_connection_slug_exists(conn: asyncpg.Connection, slug: str) -> bool:
    return bool(await conn.fetchval("SELECT 1 FROM db_connections WHERE slug = $1", slug))


async def _free_db_slug(conn: asyncpg.Connection, base: str) -> str:
    slug = base
    counter = 2
    while await conn.fetchval("SELECT 1 FROM db_connections WHERE slug = $1", slug):
        slug = f"{base}-{counter}"
        counter += 1
    return slug


async def db_create_db_connection(
    conn: asyncpg.Connection,
    *,
    label: str,
    engine: str,
    target: str,
    params: dict[str, object],
    secret_slug: str,
    identity: str,
    server_version: str,
    slug: str = "",
) -> DbConnection:
    """Enregistre la connexion, qui désigne un secret existant par son slug. La
    clé étrangère garantit que le secret existe. Doublon → rien n'est écrit."""
    # Slug saisi : pris tel quel (validé et vérifié libre par l'appelant). Absent :
    # déduit du libellé et rendu libre par suffixe.
    if not slug:
        slug = await _free_db_slug(conn, slugify(label, fallback="base"))
    try:
        row = await conn.fetchrow(
            "INSERT INTO db_connections (label, slug, engine, target, params, secret_slug, "
            "identity, server_version, last_status, last_checked_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'valid', $9) RETURNING *",
            label,
            slug,
            engine,
            target,
            params,
            secret_slug,
            identity,
            server_version,
            datetime.now(UTC),
        )
    except asyncpg.UniqueViolationError as exc:
        if exc.constraint_name == "uq_db_connection_slug":
            raise DbConnectionSlugTakenError from exc
        raise DbConnectionDuplicateError from exc
    log.info("db_connection_created", engine=engine, identity=identity)
    return DbConnection.from_row(row)


async def db_update_db_connection_status(
    conn: asyncpg.Connection,
    connection_id: uuid.UUID,
    *,
    status: str,
    identity: str | None,
    server_version: str | None,
    error_code: str | None,
) -> None:
    await conn.execute(
        "UPDATE db_connections SET last_status = $2, last_error_code = $3, "
        "identity = COALESCE($4, identity), server_version = COALESCE($5, server_version), "
        "last_checked_at = $6 WHERE id = $1",
        connection_id,
        status,
        error_code,
        identity,
        server_version,
        datetime.now(UTC),
    )


async def db_delete_db_connection(
    conn: asyncpg.Connection, connection_id: uuid.UUID
) -> str | None:
    """Supprime la seule connexion ; rend le slug de son secret. None si absente.
    Le secret n'est jamais supprimé ici."""
    connection = await db_get_db_connection(conn, connection_id)
    if connection is None:
        return None
    await conn.execute("DELETE FROM db_connections WHERE id = $1", connection_id)
    log.info("db_connection_deleted", engine=connection.engine)
    return connection.secret_slug
