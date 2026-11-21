from __future__ import annotations

import uuid

import asyncpg
import structlog

from ossie_studio.models import OidcRoleMapping
from ossie_studio.users.service import db_count_admin_users

log = structlog.get_logger(__name__)


class MappingDuplicateError(Exception):
    """Correspondance en doublon exact (connecteur, valeur, profil)."""


async def db_set_roles_claim(
    conn: asyncpg.Connection, connector_id: uuid.UUID, roles_claim: str
) -> None:
    await conn.execute(
        "UPDATE oidc_connectors SET roles_claim = $2 WHERE id = $1", connector_id, roles_claim
    )


async def db_list_mappings(
    conn: asyncpg.Connection, connector_id: uuid.UUID
) -> list[OidcRoleMapping]:
    rows = await conn.fetch(
        "SELECT * FROM oidc_role_mappings WHERE connector_id = $1 ORDER BY position", connector_id
    )
    return [OidcRoleMapping.from_row(row) for row in rows]


async def db_create_mapping(
    conn: asyncpg.Connection, connector_id: uuid.UUID, role_value: str, profile_id: uuid.UUID
) -> OidcRoleMapping:
    position = await conn.fetchval(
        "SELECT coalesce(max(position) + 1, 0) FROM oidc_role_mappings WHERE connector_id = $1",
        connector_id,
    )
    try:
        row = await conn.fetchrow(
            "INSERT INTO oidc_role_mappings (connector_id, position, role_value, profile_id) "
            "VALUES ($1, $2, $3, $4) RETURNING *",
            connector_id,
            position,
            role_value,
            profile_id,
        )
    except asyncpg.UniqueViolationError as exc:
        raise MappingDuplicateError from exc
    return OidcRoleMapping.from_row(row)


async def db_delete_mapping(conn: asyncpg.Connection, mapping_id: uuid.UUID) -> bool:
    result: str = await conn.execute("DELETE FROM oidc_role_mappings WHERE id = $1", mapping_id)
    return result != "DELETE 0"


async def db_reorder_mappings(
    conn: asyncpg.Connection, connector_id: uuid.UUID, ordered_ids: list[uuid.UUID]
) -> None:
    for position, mapping_id in enumerate(ordered_ids):
        await conn.execute(
            "UPDATE oidc_role_mappings SET position = $3 WHERE id = $1 AND connector_id = $2",
            mapping_id,
            connector_id,
            position,
        )


async def db_count_mappings_for_profile(conn: asyncpg.Connection, profile_id: uuid.UUID) -> int:
    value = await conn.fetchval(
        "SELECT count(*) FROM oidc_role_mappings WHERE profile_id = $1", profile_id
    )
    return int(value or 0)


async def resolve_mapped_profile(
    conn: asyncpg.Connection, connector_id: uuid.UUID, roles: list[str]
) -> uuid.UUID | None:
    """La première correspondance (dans l'ordre) dont la valeur figure dans les
    rôles (comparaison exacte) l'emporte."""
    if not roles:
        return None
    rows = await conn.fetch(
        "SELECT role_value, profile_id FROM oidc_role_mappings "
        "WHERE connector_id = $1 ORDER BY position",
        connector_id,
    )
    role_set = set(roles)
    for row in rows:
        if row["role_value"] in role_set:
            value: uuid.UUID = row["profile_id"]
            return value
    return None


async def apply_role_mapping(
    conn: asyncpg.Connection, connector_id: uuid.UUID, roles: list[str], user_id: uuid.UUID
) -> None:
    """Choisit le profil selon les correspondances, sauf si fixé à la main, sans
    jamais retirer l'Administrateur au dernier administrateur actif."""
    row = await conn.fetchrow(
        "SELECT u.profile_pinned, u.profile_id, p.is_admin "
        "FROM users u LEFT JOIN profiles p ON p.id = u.profile_id WHERE u.id = $1",
        user_id,
    )
    if row is None or row["profile_pinned"]:
        return
    target = await resolve_mapped_profile(conn, connector_id, roles)
    if target is None or target == row["profile_id"]:
        return
    target_is_admin = await conn.fetchval("SELECT is_admin FROM profiles WHERE id = $1", target)
    if row["is_admin"] and not target_is_admin and await db_count_admin_users(conn) <= 1:
        log.warning("oidc_mapping_admin_lockout_skipped", user_id=str(user_id))
        return
    await conn.execute("UPDATE users SET profile_id = $2 WHERE id = $1", user_id, target)
    log.info(
        "oidc_mapping_applied",
        user_id=str(user_id),
        old_profile=str(row["profile_id"]) if row["profile_id"] else None,
        new_profile=str(target),
    )
