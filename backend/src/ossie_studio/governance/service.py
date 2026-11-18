from __future__ import annotations

import uuid

import asyncpg
import structlog

from ossie_studio.models import Certification, GovernedTag

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


class CertificationTagTakenError(Exception):
    def __init__(self, tag: str) -> None:
        super().__init__(tag)
        self.tag = tag


class CertificationNotFoundError(Exception):
    def __init__(self, cert_id: uuid.UUID) -> None:
        super().__init__(str(cert_id))
        self.cert_id = cert_id


async def db_list_certifications(conn: asyncpg.Connection) -> list[Certification]:
    rows = await conn.fetch("SELECT * FROM certifications ORDER BY rank, name")
    return [Certification.from_row(row) for row in rows]


async def db_get_certification(
    conn: asyncpg.Connection, cert_id: uuid.UUID
) -> Certification | None:
    row = await conn.fetchrow("SELECT * FROM certifications WHERE id = $1", cert_id)
    return Certification.from_row(row) if row is not None else None


async def db_create_certification(
    conn: asyncpg.Connection,
    name: str,
    rank: int,
    description: str,
    criteria: str,
    tag: str,
    icon: str,
    restricted: bool,
) -> Certification:
    try:
        row = await conn.fetchrow(
            "INSERT INTO certifications (name, rank, description, criteria, tag, icon, restricted) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *",
            name,
            rank,
            description,
            criteria,
            tag,
            icon,
            restricted,
        )
    except asyncpg.UniqueViolationError as exc:
        raise CertificationTagTakenError(tag) from exc
    return Certification.from_row(row)


async def db_update_certification(
    conn: asyncpg.Connection,
    cert_id: uuid.UUID,
    name: str,
    rank: int,
    description: str,
    criteria: str,
    tag: str,
    icon: str,
    restricted: bool,
) -> Certification:
    try:
        row = await conn.fetchrow(
            "UPDATE certifications SET name = $2, rank = $3, description = $4, criteria = $5, "
            "tag = $6, icon = $7, restricted = $8, updated_at = now() WHERE id = $1 RETURNING *",
            cert_id,
            name,
            rank,
            description,
            criteria,
            tag,
            icon,
            restricted,
        )
    except asyncpg.UniqueViolationError as exc:
        raise CertificationTagTakenError(tag) from exc
    if row is None:
        raise CertificationNotFoundError(cert_id)
    return Certification.from_row(row)


async def db_delete_certification(conn: asyncpg.Connection, cert_id: uuid.UUID) -> bool:
    result: str = await conn.execute("DELETE FROM certifications WHERE id = $1", cert_id)
    return result != "DELETE 0"


async def db_create_certification_presets(
    conn: asyncpg.Connection, presets: list[dict[str, object]]
) -> None:
    # Idempotent : un niveau dont le tag existe déjà n'est pas recréé.
    for preset in presets:
        await conn.execute(
            "INSERT INTO certifications (name, rank, description, tag, icon) "
            "VALUES ($1, $2, $3, $4, $5) ON CONFLICT (tag) DO NOTHING",
            preset["name"],
            preset["rank"],
            preset["description"],
            preset["tag"],
            preset["icon"],
        )
