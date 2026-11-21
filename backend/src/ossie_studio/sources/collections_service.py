from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import asyncpg

from ossie_studio.sources.collect import CollectReport


@dataclass(frozen=True)
class CollectionRun:
    id: uuid.UUID
    author_email: str
    source_commit: str
    local_commit: str | None
    added: int
    modified: int
    deleted: int
    unchanged: int
    error_count: int
    duration_ms: int
    created_at: datetime

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> CollectionRun:
        return cls(
            id=row["id"],
            author_email=row["author_email"],
            source_commit=row["source_commit"],
            local_commit=row["local_commit"],
            added=row["added"],
            modified=row["modified"],
            deleted=row["deleted"],
            unchanged=row["unchanged"],
            error_count=row["error_count"],
            duration_ms=row["duration_ms"],
            created_at=row["created_at"],
        )


async def db_record_collection(
    conn: asyncpg.Connection,
    *,
    space_id: uuid.UUID,
    branch_slug: str,
    source_slug: str,
    author_email: str,
    report: CollectReport,
) -> None:
    await conn.execute(
        "INSERT INTO source_collections (space_id, branch_slug, source_slug, author_email, "
        "source_commit, local_commit, added, modified, deleted, unchanged, error_count, "
        "duration_ms) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)",
        space_id,
        branch_slug,
        source_slug,
        author_email,
        report.source_commit,
        report.local_commit,
        report.added,
        report.modified,
        report.deleted,
        report.unchanged,
        len(report.errors),
        report.duration_ms,
    )


async def db_list_collections(
    conn: asyncpg.Connection,
    *,
    space_id: uuid.UUID,
    branch_slug: str,
    source_slug: str,
    limit: int = 20,
) -> list[CollectionRun]:
    rows = await conn.fetch(
        "SELECT * FROM source_collections WHERE space_id = $1 AND branch_slug = $2 "
        "AND source_slug = $3 ORDER BY created_at DESC LIMIT $4",
        space_id,
        branch_slug,
        source_slug,
        limit,
    )
    return [CollectionRun.from_row(row) for row in rows]
