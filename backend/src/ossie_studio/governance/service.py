from __future__ import annotations

import asyncpg
import structlog

from ossie_studio.models import GovernedTag

log = structlog.get_logger(__name__)


class TagIdTakenError(Exception):
    def __init__(self, tag_id: str) -> None:
        super().__init__(tag_id)
        self.tag_id = tag_id


class TagNotFoundError(Exception):
    def __init__(self, tag_id: str) -> None:
        super().__init__(tag_id)
        self.tag_id = tag_id


async def db_list_tags(conn: asyncpg.Connection) -> list[GovernedTag]:
    rows = await conn.fetch("SELECT * FROM governed_tags ORDER BY id")
    return [GovernedTag.from_row(row) for row in rows]


async def db_get_tag(conn: asyncpg.Connection, tag_id: str) -> GovernedTag | None:
    row = await conn.fetchrow("SELECT * FROM governed_tags WHERE id = $1", tag_id)
    return GovernedTag.from_row(row) if row is not None else None


async def db_create_tag(
    conn: asyncpg.Connection,
    tag_id: str,
    owner: str,
    description: str,
    links: dict[str, str],
    properties: dict[str, object],
) -> GovernedTag:
    try:
        row = await conn.fetchrow(
            "INSERT INTO governed_tags (id, owner, description, links, properties) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING *",
            tag_id,
            owner,
            description,
            links,
            properties,
        )
    except asyncpg.UniqueViolationError as exc:
        raise TagIdTakenError(tag_id) from exc
    return GovernedTag.from_row(row)


async def db_update_tag(
    conn: asyncpg.Connection,
    tag_id: str,
    owner: str,
    description: str,
    links: dict[str, str],
    properties: dict[str, object],
) -> GovernedTag:
    row = await conn.fetchrow(
        "UPDATE governed_tags SET owner = $2, description = $3, links = $4, "
        "properties = $5, updated_at = now() WHERE id = $1 RETURNING *",
        tag_id,
        owner,
        description,
        links,
        properties,
    )
    if row is None:
        raise TagNotFoundError(tag_id)
    return GovernedTag.from_row(row)


async def db_upsert_tag(
    conn: asyncpg.Connection,
    tag_id: str,
    owner: str,
    description: str,
    links: dict[str, str],
    properties: dict[str, object],
) -> None:
    await conn.execute(
        "INSERT INTO governed_tags (id, owner, description, links, properties) "
        "VALUES ($1, $2, $3, $4, $5) "
        "ON CONFLICT (id) DO UPDATE SET owner = EXCLUDED.owner, "
        "description = EXCLUDED.description, links = EXCLUDED.links, "
        "properties = EXCLUDED.properties, updated_at = now()",
        tag_id,
        owner,
        description,
        links,
        properties,
    )


async def db_delete_tag(conn: asyncpg.Connection, tag_id: str) -> bool:
    result: str = await conn.execute("DELETE FROM governed_tags WHERE id = $1", tag_id)
    return result != "DELETE 0"
