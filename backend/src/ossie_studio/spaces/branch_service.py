from __future__ import annotations

import re
import uuid

import asyncpg
import structlog

from ossie_studio.models import SpaceBranch

log = structlog.get_logger(__name__)

KIND_BASE = "base"
KIND_CHILD = "child"
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class BranchSlugTakenError(Exception):
    def __init__(self, slug: str) -> None:
        super().__init__(slug)
        self.slug = slug


def slugify(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")
    return slug[:64]


def is_valid_slug(slug: str) -> bool:
    return bool(_SLUG_PATTERN.fullmatch(slug))


async def db_list_branches(conn: asyncpg.Connection, space_id: uuid.UUID) -> list[SpaceBranch]:
    rows = await conn.fetch(
        "SELECT * FROM space_branches WHERE space_id = $1 ORDER BY kind DESC, label", space_id
    )
    return [SpaceBranch.from_row(row) for row in rows]


async def db_get_branch(conn: asyncpg.Connection, branch_id: uuid.UUID) -> SpaceBranch | None:
    row = await conn.fetchrow("SELECT * FROM space_branches WHERE id = $1", branch_id)
    return SpaceBranch.from_row(row) if row is not None else None


async def db_get_branch_by_slug(
    conn: asyncpg.Connection, space_id: uuid.UUID, slug: str
) -> SpaceBranch | None:
    row = await conn.fetchrow(
        "SELECT * FROM space_branches WHERE space_id = $1 AND slug = $2", space_id, slug
    )
    return SpaceBranch.from_row(row) if row is not None else None


async def db_get_base(conn: asyncpg.Connection, space_id: uuid.UUID) -> SpaceBranch | None:
    row = await conn.fetchrow(
        "SELECT * FROM space_branches WHERE space_id = $1 AND kind = 'base'", space_id
    )
    return SpaceBranch.from_row(row) if row is not None else None


async def db_create_branch(
    conn: asyncpg.Connection, space_id: uuid.UUID, label: str, slug: str, kind: str
) -> SpaceBranch:
    try:
        row = await conn.fetchrow(
            "INSERT INTO space_branches (space_id, label, slug, kind, git_ref) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING *",
            space_id,
            label,
            slug,
            kind,
            f"work/{slug}",
        )
    except asyncpg.UniqueViolationError as exc:
        raise BranchSlugTakenError(slug) from exc
    log.info("branch_recorded", space_id=str(space_id), slug=slug, kind=kind)
    return SpaceBranch.from_row(row)


async def db_set_viewed_branch(
    conn: asyncpg.Connection, user_id: uuid.UUID, space_id: uuid.UUID, branch_id: uuid.UUID
) -> None:
    await conn.execute(
        "INSERT INTO user_viewed_branch (user_id, space_id, branch_id) VALUES ($1, $2, $3) "
        "ON CONFLICT (user_id, space_id) DO UPDATE SET branch_id = EXCLUDED.branch_id",
        user_id,
        space_id,
        branch_id,
    )


async def db_get_viewed_branch(
    conn: asyncpg.Connection, user_id: uuid.UUID, space_id: uuid.UUID
) -> uuid.UUID | None:
    value: uuid.UUID | None = await conn.fetchval(
        "SELECT branch_id FROM user_viewed_branch WHERE user_id = $1 AND space_id = $2",
        user_id,
        space_id,
    )
    return value
