from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import asyncpg
import structlog

log = structlog.get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_NAME_PATTERN = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class MigrationChangedError(Exception):
    """Une migration déjà appliquée a vu son contenu changer (empreinte)."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str
    fingerprint: str


def file_load_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Charge les migrations du répertoire, triées par numéro ; numéros uniques."""
    migrations: list[Migration] = []
    seen: set[int] = set()
    for path in sorted(directory.glob("*.sql")):
        match = _NAME_PATTERN.match(path.name)
        if match is None:
            msg = f"Nom de migration invalide : {path.name} (attendu NNNN_nom.sql)"
            raise ValueError(msg)
        version = int(match.group(1))
        if version in seen:
            msg = f"Numéro de migration en double : {version:04d}"
            raise ValueError(msg)
        seen.add(version)
        sql = path.read_text(encoding="utf-8")
        fingerprint = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        migrations.append(Migration(version, path.name, sql, fingerprint))
    return migrations


async def _db_ensure_tracking_table(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version      integer PRIMARY KEY,
            name         text NOT NULL,
            fingerprint  text NOT NULL,
            applied_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )


async def db_applied_migrations(conn: asyncpg.Connection) -> dict[int, str]:
    rows = await conn.fetch("SELECT version, fingerprint FROM schema_migrations")
    return {row["version"]: row["fingerprint"] for row in rows}


async def run_migrations(pool: asyncpg.Pool, directory: Path = MIGRATIONS_DIR) -> int:
    """Applique les migrations en attente ; rend la version de schéma courante.

    Idempotent : une migration déjà appliquée est ignorée. Chaque migration
    tourne dans sa propre transaction. Si le contenu d'une migration déjà
    appliquée a changé (empreinte différente), on refuse — une migration est
    immuable une fois jouée.
    """
    migrations = file_load_migrations(directory)
    async with pool.acquire() as conn:
        await _db_ensure_tracking_table(conn)
        applied = await db_applied_migrations(conn)

    for migration in migrations:
        known = applied.get(migration.version)
        if known is not None:
            if known != migration.fingerprint:
                msg = (
                    f"La migration {migration.name} a déjà été appliquée avec une "
                    f"empreinte différente : une migration jouée ne se modifie jamais."
                )
                raise MigrationChangedError(msg)
            continue
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(migration.sql)
            await conn.execute(
                "INSERT INTO schema_migrations (version, name, fingerprint) VALUES ($1, $2, $3)",
                migration.version,
                migration.name,
                migration.fingerprint,
            )
        log.info("migration_applied", version=migration.version, name=migration.name)

    return migrations[-1].version if migrations else 0


def latest_version(directory: Path = MIGRATIONS_DIR) -> int:
    migrations = file_load_migrations(directory)
    return migrations[-1].version if migrations else 0


async def db_current_version(conn: asyncpg.Connection) -> int:
    value = await conn.fetchval("SELECT max(version) FROM schema_migrations")
    return int(value) if value is not None else 0
