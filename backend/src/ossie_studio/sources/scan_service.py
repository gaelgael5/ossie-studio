from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import asyncpg

from ossie_studio.sources.collect import FileStatus


@dataclass(frozen=True)
class ScanFile:
    path: str
    crc: str
    cible: str
    statut: str
    message: str
    scanned_at: datetime

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> ScanFile:
        return cls(
            path=row["path"],
            crc=row["crc"],
            cible=row["cible"],
            statut=row["statut"],
            message=row["message"],
            scanned_at=row["scanned_at"],
        )


async def db_replace_scan_files(
    conn: asyncpg.Connection,
    *,
    space_id: uuid.UUID,
    branch_slug: str,
    source_slug: str,
    files: list[FileStatus],
) -> None:
    """Remet à plat l'index de travail d'une source : on efface l'inventaire
    précédent et on réinscrit les fichiers vus à cette collecte."""
    async with conn.transaction():
        await conn.execute(
            "DELETE FROM scan_files WHERE space_id = $1 AND branch_slug = $2 AND source_slug = $3",
            space_id,
            branch_slug,
            source_slug,
        )
        if not files:
            return
        await conn.executemany(
            "INSERT INTO scan_files (space_id, branch_slug, source_slug, path, crc, cible, "
            "statut, message) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            [
                (space_id, branch_slug, source_slug, f.path, f.crc, f.cible, f.statut, f.message)
                for f in files
            ],
        )


async def db_list_scan_files(
    conn: asyncpg.Connection,
    *,
    space_id: uuid.UUID,
    branch_slug: str,
    source_slug: str,
) -> list[ScanFile]:
    rows = await conn.fetch(
        "SELECT path, crc, cible, statut, message, scanned_at FROM scan_files "
        "WHERE space_id = $1 AND branch_slug = $2 AND source_slug = $3 ORDER BY path",
        space_id,
        branch_slug,
        source_slug,
    )
    return [ScanFile.from_row(r) for r in rows]
