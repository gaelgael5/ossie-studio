from __future__ import annotations

import re

import asyncpg
import structlog

from ossie_studio.crypto import CryptoService, DecryptionFailedError, SecretValue
from ossie_studio.models import Secret

log = structlog.get_logger(__name__)

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class SecretSlugTakenError(Exception):
    """Un secret porte déjà ce slug."""


class SecretNotFoundError(Exception):
    def __init__(self, slug: str) -> None:
        super().__init__(f"Aucun secret ne porte le slug « {slug} »")
        self.slug = slug


async def db_list_secrets(conn: asyncpg.Connection) -> list[Secret]:
    rows = await conn.fetch("SELECT * FROM secrets ORDER BY slug")
    return [Secret.from_row(row) for row in rows]


async def db_list_secrets_by_category(conn: asyncpg.Connection, category: str) -> list[Secret]:
    rows = await conn.fetch(
        "SELECT * FROM secrets WHERE category = $1 ORDER BY slug", category
    )
    return [Secret.from_row(row) for row in rows]


async def db_get_secret(conn: asyncpg.Connection, slug: str) -> Secret | None:
    row = await conn.fetchrow("SELECT * FROM secrets WHERE slug = $1", slug)
    return Secret.from_row(row) if row is not None else None


async def db_create_secret(
    conn: asyncpg.Connection, slug: str, label: str, category: str, value_encrypted: bytes
) -> Secret:
    """Insère le secret ; l'unicité du slug est tranchée par la base."""
    try:
        row = await conn.fetchrow(
            "INSERT INTO secrets (slug, label, category, value_encrypted) "
            "VALUES ($1, $2, $3, $4) RETURNING *",
            slug,
            label,
            category,
            value_encrypted,
        )
    except asyncpg.UniqueViolationError as exc:
        raise SecretSlugTakenError from exc
    return Secret.from_row(row)


async def db_replace_secret_value(
    conn: asyncpg.Connection, slug: str, value_encrypted: bytes
) -> Secret:
    row = await conn.fetchrow(
        "UPDATE secrets SET value_encrypted = $2, updated_at = now() WHERE slug = $1 RETURNING *",
        slug,
        value_encrypted,
    )
    if row is None:
        raise SecretNotFoundError(slug)
    return Secret.from_row(row)


async def db_update_secret_label(conn: asyncpg.Connection, slug: str, label: str) -> Secret:
    row = await conn.fetchrow(
        "UPDATE secrets SET label = $2, updated_at = now() WHERE slug = $1 RETURNING *",
        slug,
        label,
    )
    if row is None:
        raise SecretNotFoundError(slug)
    return Secret.from_row(row)


async def db_delete_secret(conn: asyncpg.Connection, slug: str) -> bool:
    """Supprime le secret ; False si aucun secret ne porte ce slug."""
    result: str = await conn.execute("DELETE FROM secrets WHERE slug = $1", slug)
    return result != "DELETE 0"


async def resolve_secret_value(
    conn: asyncpg.Connection, crypto: CryptoService, slug: str
) -> SecretValue:
    """Rend la valeur d'un secret, sous le type opaque, pour usage serveur.

    Slug inconnu → erreur explicite, jamais une valeur vide. Le déchiffrement
    qui échoue est journalisé sans la moindre donnée chiffrée ni claire.
    """
    secret = await db_get_secret(conn, slug)
    if secret is None:
        raise SecretNotFoundError(slug)
    try:
        return crypto.decrypt(secret.value_encrypted)
    except DecryptionFailedError:
        log.warning("secret_decrypt_failed", slug=slug)
        raise
