from __future__ import annotations

import uuid
from datetime import UTC, datetime

import asyncpg
import structlog

from ossie_studio.models import GitSpace

log = structlog.get_logger(__name__)


class SpaceNameTakenError(Exception):
    pass


class SpaceRepoTakenError(Exception):
    def __init__(self, existing_name: str) -> None:
        super().__init__(existing_name)
        self.existing_name = existing_name


async def db_list_spaces(conn: asyncpg.Connection) -> list[GitSpace]:
    rows = await conn.fetch("SELECT * FROM git_spaces ORDER BY name")
    return [GitSpace.from_row(row) for row in rows]


async def db_get_space(conn: asyncpg.Connection, space_id: uuid.UUID) -> GitSpace | None:
    row = await conn.fetchrow("SELECT * FROM git_spaces WHERE id = $1", space_id)
    return GitSpace.from_row(row) if row is not None else None


async def db_spaces_using_connection(
    conn: asyncpg.Connection, connection_id: uuid.UUID
) -> list[str]:
    rows = await conn.fetch(
        "SELECT name FROM git_spaces WHERE connection_id = $1 ORDER BY name", connection_id
    )
    return [row["name"] for row in rows]


async def db_create_space(
    conn: asyncpg.Connection,
    *,
    name: str,
    connection_id: uuid.UUID,
    repo_url: str,
    probe_status: str,
) -> GitSpace:
    """Enregistre l'espace — appelé seulement APRÈS les cinq droits éprouvés.

    Nom déjà pris → refus ; même dépôt déjà rattaché → refus nommant l'espace.
    """
    existing = await conn.fetchval("SELECT name FROM git_spaces WHERE repo_url = $1", repo_url)
    if existing is not None:
        raise SpaceRepoTakenError(existing)
    try:
        row = await conn.fetchrow(
            "INSERT INTO git_spaces (name, connection_id, repo_url, last_probe_status, "
            "last_probe_at) VALUES ($1, $2, $3, $4, $5) RETURNING *",
            name,
            connection_id,
            repo_url,
            probe_status,
            datetime.now(UTC),
        )
    except asyncpg.UniqueViolationError as exc:
        # L'unicité du dépôt a été vérifiée ci-dessus : ici, c'est le nom.
        raise SpaceNameTakenError from exc
    log.info("git_space_created", name=name)
    return GitSpace.from_row(row)


async def db_update_space_probe(conn: asyncpg.Connection, space_id: uuid.UUID, status: str) -> None:
    await conn.execute(
        "UPDATE git_spaces SET last_probe_status = $2, last_probe_at = $3 WHERE id = $1",
        space_id,
        status,
        datetime.now(UTC),
    )


async def db_delete_space(conn: asyncpg.Connection, space_id: uuid.UUID) -> bool:
    """Supprime l'espace. Ne touche pas au dépôt."""
    result: str = await conn.execute("DELETE FROM git_spaces WHERE id = $1", space_id)
    return result != "DELETE 0"
