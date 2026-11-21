from __future__ import annotations

import uuid

import asyncpg
import structlog

from ossie_studio.models import ClassificationLevel, ClassificationScheme

log = structlog.get_logger(__name__)


class SchemeIdTakenError(Exception):
    def __init__(self, scheme_id: str) -> None:
        super().__init__(scheme_id)
        self.scheme_id = scheme_id


class SchemeNotFoundError(Exception):
    def __init__(self, scheme_id: str) -> None:
        super().__init__(scheme_id)
        self.scheme_id = scheme_id


class LevelNotFoundError(Exception):
    pass


class LevelNameTakenError(Exception):
    """Nom ou nom court déjà pris dans le schéma ; `code` dit lequel."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _level_taken(exc: asyncpg.UniqueViolationError) -> LevelNameTakenError:
    if exc.constraint_name == "uq_level_short_name":
        return LevelNameTakenError("level_short_name_taken")
    return LevelNameTakenError("level_name_taken")


async def db_list_schemes_with_levels(
    conn: asyncpg.Connection,
) -> list[tuple[ClassificationScheme, list[ClassificationLevel]]]:
    scheme_rows = await conn.fetch(
        "SELECT * FROM classification_schemes ORDER BY display_order, name"
    )
    level_rows = await conn.fetch(
        "SELECT * FROM classification_levels ORDER BY scheme_id, position, rank"
    )
    by_scheme: dict[str, list[ClassificationLevel]] = {}
    for row in level_rows:
        level = ClassificationLevel.from_row(row)
        by_scheme.setdefault(level.scheme_id, []).append(level)
    schemes = [ClassificationScheme.from_row(row) for row in scheme_rows]
    return [(scheme, by_scheme.get(scheme.id, [])) for scheme in schemes]


async def db_get_scheme(conn: asyncpg.Connection, scheme_id: str) -> ClassificationScheme | None:
    row = await conn.fetchrow("SELECT * FROM classification_schemes WHERE id = $1", scheme_id)
    return ClassificationScheme.from_row(row) if row is not None else None


async def db_next_scheme_order(conn: asyncpg.Connection) -> int:
    value: int | None = await conn.fetchval(
        "SELECT max(display_order) + 1 FROM classification_schemes"
    )
    return value or 0


async def _unset_primary(conn: asyncpg.Connection) -> None:
    await conn.execute("UPDATE classification_schemes SET is_primary = false WHERE is_primary")


async def db_create_scheme(
    conn: asyncpg.Connection,
    scheme_id: str,
    name: str,
    description: str,
    badge_prefix: str,
    multi_value: bool,
    is_primary: bool,
    display_order: int,
) -> ClassificationScheme:
    if is_primary:
        await _unset_primary(conn)
    try:
        row = await conn.fetchrow(
            "INSERT INTO classification_schemes "
            "(id, name, description, badge_prefix, multi_value, is_primary, display_order) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *",
            scheme_id,
            name,
            description,
            badge_prefix,
            multi_value,
            is_primary,
            display_order,
        )
    except asyncpg.UniqueViolationError as exc:
        raise SchemeIdTakenError(scheme_id) from exc
    return ClassificationScheme.from_row(row)


async def db_update_scheme(
    conn: asyncpg.Connection,
    scheme_id: str,
    name: str,
    description: str,
    badge_prefix: str,
    multi_value: bool,
    is_primary: bool,
    display_order: int,
) -> ClassificationScheme:
    if is_primary:
        await conn.execute(
            "UPDATE classification_schemes SET is_primary = false WHERE is_primary AND id <> $1",
            scheme_id,
        )
    row = await conn.fetchrow(
        "UPDATE classification_schemes SET name = $2, description = $3, badge_prefix = $4, "
        "multi_value = $5, is_primary = $6, display_order = $7, updated_at = now() "
        "WHERE id = $1 RETURNING *",
        scheme_id,
        name,
        description,
        badge_prefix,
        multi_value,
        is_primary,
        display_order,
    )
    if row is None:
        raise SchemeNotFoundError(scheme_id)
    return ClassificationScheme.from_row(row)


async def db_delete_scheme(conn: asyncpg.Connection, scheme_id: str) -> bool:
    result: str = await conn.execute("DELETE FROM classification_schemes WHERE id = $1", scheme_id)
    return result != "DELETE 0"


async def db_add_level(
    conn: asyncpg.Connection,
    scheme_id: str,
    name: str,
    label: str,
    short_name: str,
    rank: int,
    description: str,
    criteria: str,
    color: str,
    icon: str,
    position: int,
) -> ClassificationLevel:
    try:
        row = await conn.fetchrow(
            "INSERT INTO classification_levels (scheme_id, name, label, short_name, rank, "
            "description, criteria, color, icon, position) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING *",
            scheme_id,
            name,
            label,
            short_name,
            rank,
            description,
            criteria,
            color,
            icon,
            position,
        )
    except asyncpg.UniqueViolationError as exc:
        raise _level_taken(exc) from exc
    return ClassificationLevel.from_row(row)


async def db_update_level(
    conn: asyncpg.Connection,
    level_id: uuid.UUID,
    name: str,
    label: str,
    short_name: str,
    rank: int,
    description: str,
    criteria: str,
    color: str,
    icon: str,
) -> ClassificationLevel:
    try:
        row = await conn.fetchrow(
            "UPDATE classification_levels SET name = $2, label = $3, short_name = $4, rank = $5, "
            "description = $6, criteria = $7, color = $8, icon = $9 WHERE id = $1 RETURNING *",
            level_id,
            name,
            label,
            short_name,
            rank,
            description,
            criteria,
            color,
            icon,
        )
    except asyncpg.UniqueViolationError as exc:
        raise _level_taken(exc) from exc
    if row is None:
        raise LevelNotFoundError
    return ClassificationLevel.from_row(row)


async def db_delete_level(conn: asyncpg.Connection, level_id: uuid.UUID) -> bool:
    result: str = await conn.execute("DELETE FROM classification_levels WHERE id = $1", level_id)
    return result != "DELETE 0"


async def db_reorder_levels(
    conn: asyncpg.Connection, scheme_id: str, ordered_ids: list[uuid.UUID]
) -> None:
    for position, level_id in enumerate(ordered_ids):
        await conn.execute(
            "UPDATE classification_levels SET position = $3 WHERE id = $1 AND scheme_id = $2",
            level_id,
            scheme_id,
            position,
        )
