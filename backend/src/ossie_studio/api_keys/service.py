"""Clés d'API de service : émission, révocation, et reconnaissance à l'appel MCP.

Une clé est `osk_` suivi de 43 caractères base64 url (256 bits tirés du
hasard). Elle n'est rendue qu'une fois ; Ossie-studio n'en garde que
l'empreinte SHA-256 (une clé à haute entropie n'a pas besoin d'un hachage lent)
et le préfixe de 12 caractères qui sert à la reconnaître à l'écran.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime

import asyncpg

from ossie_studio.mcp_surface.caller import McpCaller

KEY_PREFIX = "osk_"
PREFIX_LENGTH = 12

_SELECT = (
    "SELECT k.*, p.name AS agent_profile_name, "
    "c.email AS created_by_email, r.email AS revoked_by_email "
    "FROM api_keys k "
    "JOIN agent_profiles p ON p.id = k.agent_profile_id "
    "LEFT JOIN users c ON c.id = k.created_by "
    "LEFT JOIN users r ON r.id = k.revoked_by "
)


@dataclass(frozen=True)
class ApiKey:
    id: uuid.UUID
    label: str
    prefix: str
    agent_profile_id: uuid.UUID
    agent_profile_name: str
    created_at: datetime
    created_by_email: str | None
    revoked_at: datetime | None
    revoked_by_email: str | None
    last_used_at: datetime | None

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> ApiKey:
        return cls(
            id=row["id"],
            label=row["label"],
            prefix=row["prefix"],
            agent_profile_id=row["agent_profile_id"],
            agent_profile_name=row["agent_profile_name"],
            created_at=row["created_at"],
            created_by_email=row["created_by_email"],
            revoked_at=row["revoked_at"],
            revoked_by_email=row["revoked_by_email"],
            last_used_at=row["last_used_at"],
        )


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


async def db_list_api_keys(conn: asyncpg.Connection) -> list[ApiKey]:
    rows = await conn.fetch(_SELECT + "ORDER BY k.revoked_at IS NOT NULL, k.created_at DESC")
    return [ApiKey.from_row(row) for row in rows]


async def db_get_api_key(conn: asyncpg.Connection, key_id: uuid.UUID) -> ApiKey | None:
    row = await conn.fetchrow(_SELECT + "WHERE k.id = $1", key_id)
    return ApiKey.from_row(row) if row is not None else None


async def db_create_api_key(
    conn: asyncpg.Connection, label: str, agent_profile_id: uuid.UUID, created_by: uuid.UUID
) -> tuple[ApiKey, str]:
    """Émet une clé ; rend la clé enregistrée et son secret (à montrer une fois)."""
    secret = generate_key()
    key_id = await conn.fetchval(
        "INSERT INTO api_keys (label, prefix, key_hash, agent_profile_id, created_by) "
        "VALUES ($1, $2, $3, $4, $5) RETURNING id",
        label,
        secret[:PREFIX_LENGTH],
        key_hash(secret),
        agent_profile_id,
        created_by,
    )
    key = await db_get_api_key(conn, key_id)
    assert key is not None
    return key, secret


async def db_set_api_key_profile(
    conn: asyncpg.Connection, key_id: uuid.UUID, agent_profile_id: uuid.UUID
) -> None:
    await conn.execute(
        "UPDATE api_keys SET agent_profile_id = $2 WHERE id = $1 AND revoked_at IS NULL",
        key_id,
        agent_profile_id,
    )


async def db_revoke_api_key(
    conn: asyncpg.Connection, key_id: uuid.UUID, revoked_by: uuid.UUID
) -> bool:
    """Révoque ; rend False si elle l'était déjà (rejouable sans effet)."""
    result: str = await conn.execute(
        "UPDATE api_keys SET revoked_at = now(), revoked_by = $2 "
        "WHERE id = $1 AND revoked_at IS NULL",
        key_id,
        revoked_by,
    )
    return result != "UPDATE 0"


async def resolve_api_key(
    conn: asyncpg.Connection, token: str, _resource: str
) -> McpCaller | None:
    """Résolveur de la surface MCP : une clé active → son compte de service.

    Inconnue ou révoquée : None, sans rien qui les distingue. Le dernier usage
    est tenu à jour au plus une fois par minute."""
    if not token.startswith(KEY_PREFIX):
        return None
    presented = key_hash(token)
    row = await conn.fetchrow(
        "SELECT id, key_hash, agent_profile_id FROM api_keys "
        "WHERE key_hash = $1 AND revoked_at IS NULL",
        presented,
    )
    if row is None or not hmac.compare_digest(row["key_hash"], presented):
        return None
    await conn.execute(
        "UPDATE api_keys SET last_used_at = now() WHERE id = $1 "
        "AND (last_used_at IS NULL OR last_used_at < now() - interval '1 minute')",
        row["id"],
    )
    return McpCaller(kind="key", ref=str(row["id"]), agent_profile_id=row["agent_profile_id"])
