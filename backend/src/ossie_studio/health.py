from __future__ import annotations

from dataclasses import asdict, dataclass

import asyncpg
import structlog

from ossie_studio.config import Settings
from ossie_studio.db.migrate import db_current_version, latest_version
from ossie_studio.volumes import VolumeStatus, check_volume_writable

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class DbStatus:
    reachable: bool
    schema_version: int | None
    expected_version: int
    up_to_date: bool
    detail: str | None = None


@dataclass(frozen=True)
class HealthReport:
    ok: bool
    database: DbStatus
    volumes: list[VolumeStatus]

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "database": asdict(self.database),
            "volumes": [asdict(v) for v in self.volumes],
        }


async def _db_check(pool: asyncpg.Pool) -> DbStatus:
    expected = latest_version()
    try:
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")
            current = await db_current_version(conn)
    except (asyncpg.PostgresError, OSError) as exc:
        return DbStatus(False, None, expected, up_to_date=False, detail=f"base injoignable : {exc}")
    up_to_date = current == expected
    detail = None if up_to_date else f"schéma en version {current}, attendu {expected}"
    return DbStatus(True, current, expected, up_to_date, detail)


async def resolve_health(pool: asyncpg.Pool, settings: Settings) -> HealthReport:
    """Interroge réellement la base et les deux volumes. Jamais de 200 de
    complaisance : si quelque chose manque, le rapport le dit et n'est pas ok."""
    database = await _db_check(pool)
    volumes = [
        check_volume_writable("work", settings.work_volume_path),
        check_volume_writable("sources", settings.sources_volume_path),
    ]
    ok = database.reachable and database.up_to_date and all(v.writable for v in volumes)
    return HealthReport(ok=ok, database=database, volumes=volumes)
