from __future__ import annotations

import uuid
from datetime import UTC, datetime

import asyncpg
import structlog

from ossie_studio.crypto import CryptoService, SecretValue
from ossie_studio.forges.base import ForgeIdentity
from ossie_studio.models import Connection
from ossie_studio.slugs import slugify

log = structlog.get_logger(__name__)


class ConnectionDuplicateError(Exception):
    """Une connexion existe déjà sur cette forge, cette URL et cette identité."""


class ConnectionSlugTakenError(Exception):
    """Le slug saisi désigne déjà une autre connexion."""


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


async def db_get_connection_by_slug(conn: asyncpg.Connection, slug: str) -> Connection | None:
    row = await conn.fetchrow("SELECT * FROM connections WHERE slug = $1", slug)
    return Connection.from_row(row) if row is not None else None


async def db_connection_slug_exists(conn: asyncpg.Connection, slug: str) -> bool:
    return bool(await conn.fetchval("SELECT 1 FROM connections WHERE slug = $1", slug))


async def _free_connection_slug(conn: asyncpg.Connection, base: str) -> str:
    """Rend un slug libre, en suffixant d'un compteur au besoin."""
    slug = base
    counter = 2
    while await conn.fetchval("SELECT 1 FROM connections WHERE slug = $1", slug):
        slug = f"{base}-{counter}"
        counter += 1
    return slug


async def db_create_connection(
    conn: asyncpg.Connection,
    *,
    label: str,
    forge: str,
    url: str,
    secret_slug: str,
    identity: ForgeIdentity,
    slug: str = "",
) -> Connection:
    """Enregistre la connexion, qui désigne un secret existant par son slug (de
    la catégorie de la forge). La valeur du jeton n'est jamais touchée ici : elle
    vit dans la table des secrets. Doublon (forge, url, identité) → rien n'est
    écrit. La clé étrangère garantit que le secret existe."""
    # Slug saisi : pris tel quel (déjà validé et vérifié libre par l'appelant ;
    # une course est rattrapée par l'index unique). Absent : déduit du libellé
    # et rendu libre par suffixe — compatibilité des appels sans slug.
    if not slug:
        slug = await _free_connection_slug(conn, slugify(label, fallback="connexion"))
    try:
        row = await conn.fetchrow(
            "INSERT INTO connections (label, slug, forge, url, secret_slug, identity, "
            "last_status, last_checked_at) VALUES ($1, $2, $3, $4, $5, $6, 'valid', $7) "
            "RETURNING *",
            label,
            slug,
            forge,
            url,
            secret_slug,
            identity.username,
            datetime.now(UTC),
        )
    except asyncpg.UniqueViolationError as exc:
        if exc.constraint_name == "uq_connection_slug":
            raise ConnectionSlugTakenError from exc
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


async def db_delete_connection(
    conn: asyncpg.Connection, connection_id: uuid.UUID
) -> str | None:
    """Supprime la seule connexion ; rend le slug de son secret (pour que
    l'appelant décide s'il faut aussi le supprimer). None si elle n'existe pas.
    Le secret n'est jamais supprimé ici."""
    connection = await db_get_connection(conn, connection_id)
    if connection is None:
        return None
    await conn.execute("DELETE FROM connections WHERE id = $1", connection_id)
    log.info("connection_deleted", forge=connection.forge)
    return connection.secret_slug


async def resolve_connection_token(
    conn: asyncpg.Connection, crypto: CryptoService, connection: Connection
) -> SecretValue:
    row = await conn.fetchrow(
        "SELECT value_encrypted FROM secrets WHERE slug = $1", connection.secret_slug
    )
    if row is None:
        raise ConnectionNotFoundError
    return crypto.decrypt(row["value_encrypted"])
