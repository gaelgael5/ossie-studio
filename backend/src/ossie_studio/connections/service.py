from __future__ import annotations

import uuid
from datetime import UTC, datetime

import asyncpg
import structlog

from ossie_studio.crypto import CryptoService, SecretValue
from ossie_studio.forges.base import ForgeIdentity
from ossie_studio.models import Connection

log = structlog.get_logger(__name__)


class ConnectionDuplicateError(Exception):
    """Une connexion existe déjà sur cette forge, cette URL et cette identité."""


class ConnectionNotFoundError(Exception):
    pass


async def db_list_connections(conn: asyncpg.Connection) -> list[Connection]:
    rows = await conn.fetch("SELECT * FROM connections ORDER BY label")
    return [Connection.from_row(row) for row in rows]


async def db_get_connection(
    conn: asyncpg.Connection, connection_id: uuid.UUID
) -> Connection | None:
    row = await conn.fetchrow("SELECT * FROM connections WHERE id = $1", connection_id)
    return Connection.from_row(row) if row is not None else None


async def db_create_connection_with_secret(
    conn: asyncpg.Connection,
    crypto: CryptoService,
    *,
    label: str,
    forge: str,
    url: str,
    token: str,
    identity: ForgeIdentity,
) -> Connection:
    """Enregistre la connexion ET son jeton (comme secret) d'un seul tenant.

    Le jeton vit dans la table des secrets, catégorie = forge ; la connexion
    n'en garde que le slug. En cas de doublon, rien n'est écrit (ni secret, ni
    connexion) — la transaction est annulée.
    """
    secret_slug = f"forge-{forge}-{uuid.uuid4().hex[:12]}"
    encrypted = crypto.encrypt(SecretValue(token))
    try:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO secrets (slug, label, category, value_encrypted) "
                "VALUES ($1, $2, $3, $4)",
                secret_slug,
                f"Jeton {forge} — {label}",
                forge,
                encrypted,
            )
            row = await conn.fetchrow(
                "INSERT INTO connections (label, forge, url, secret_slug, identity, "
                "last_status, last_checked_at) VALUES ($1, $2, $3, $4, $5, 'valid', $6) "
                "RETURNING *",
                label,
                forge,
                url,
                secret_slug,
                identity.username,
                datetime.now(UTC),
            )
    except asyncpg.UniqueViolationError as exc:
        raise ConnectionDuplicateError from exc
    log.info("connection_created", forge=forge, identity=identity.username)
    return Connection.from_row(row)


async def db_update_connection_status(
    conn: asyncpg.Connection,
    connection_id: uuid.UUID,
    *,
    status: str,
    identity: str | None,
    error_code: str | None,
) -> None:
    await conn.execute(
        "UPDATE connections SET last_status = $2, last_error_code = $3, "
        "identity = COALESCE($4, identity), last_checked_at = $5 WHERE id = $1",
        connection_id,
        status,
        error_code,
        identity,
        datetime.now(UTC),
    )


async def db_delete_connection_and_secret(
    conn: asyncpg.Connection, connection_id: uuid.UUID
) -> bool:
    """Supprime la connexion ET son secret. False si elle n'existe pas."""
    connection = await db_get_connection(conn, connection_id)
    if connection is None:
        return False
    async with conn.transaction():
        await conn.execute("DELETE FROM connections WHERE id = $1", connection_id)
        await conn.execute("DELETE FROM secrets WHERE slug = $1", connection.secret_slug)
    log.info("connection_deleted", forge=connection.forge)
    return True


async def resolve_connection_token(
    conn: asyncpg.Connection, crypto: CryptoService, connection: Connection
) -> SecretValue:
    row = await conn.fetchrow(
        "SELECT value_encrypted FROM secrets WHERE slug = $1", connection.secret_slug
    )
    if row is None:
        raise ConnectionNotFoundError
    return crypto.decrypt(row["value_encrypted"])
