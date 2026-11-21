"""Rattachements équipe → espace avec un profil (droits par espace).

Gérer un rattachement donne des droits : c'est un **droit fort**, réservé à
`profiles:write`, comme associer un profil à une équipe."""

from __future__ import annotations

import uuid

import asyncpg
import structlog
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import ProfilesWrite, SpacesRead
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/spaces/{space_id}/teams")


class AttachBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: uuid.UUID


async def db_list_space_teams(
    conn: asyncpg.Connection, space_id: uuid.UUID
) -> list[dict[str, str]]:
    rows = await conn.fetch(
        "SELECT t.id AS team_id, t.name AS team_name, p.id AS profile_id, p.name AS profile_name "
        "FROM space_team_profiles stp JOIN teams t ON t.id = stp.team_id "
        "JOIN profiles p ON p.id = stp.profile_id WHERE stp.space_id = $1 ORDER BY t.name",
        space_id,
    )
    return [
        {
            "team_id": str(r["team_id"]),
            "team_name": r["team_name"],
            "profile_id": str(r["profile_id"]),
            "profile_name": r["profile_name"],
        }
        for r in rows
    ]


@router.get("")
async def list_space_teams(conn: ConnDep, _: SpacesRead, space_id: uuid.UUID) -> dict[str, object]:
    return {"teams": await db_list_space_teams(conn, space_id)}


@router.put("/{team_id}")
async def attach_team(
    conn: ConnDep, _: ProfilesWrite, space_id: uuid.UUID, team_id: uuid.UUID, body: AttachBody
) -> dict[str, object]:
    if not await conn.fetchval("SELECT 1 FROM git_spaces WHERE id = $1", space_id):
        raise ApiError("space_not_found", status_code=404)
    if not await conn.fetchval("SELECT 1 FROM teams WHERE id = $1", team_id):
        raise ApiError("team_not_found", status_code=404)
    if not await conn.fetchval("SELECT 1 FROM profiles WHERE id = $1", body.profile_id):
        raise ApiError("profile_not_found", status_code=404)
    await conn.execute(
        "INSERT INTO space_team_profiles (space_id, team_id, profile_id) VALUES ($1, $2, $3) "
        "ON CONFLICT (space_id, team_id) DO UPDATE SET profile_id = EXCLUDED.profile_id",
        space_id,
        team_id,
        body.profile_id,
    )
    log.info("space_team_attached", space_id=str(space_id), team_id=str(team_id))
    return {"teams": await db_list_space_teams(conn, space_id)}


@router.delete("/{team_id}", status_code=204)
async def detach_team(
    conn: ConnDep, _: ProfilesWrite, space_id: uuid.UUID, team_id: uuid.UUID
) -> None:
    result: str = await conn.execute(
        "DELETE FROM space_team_profiles WHERE space_id = $1 AND team_id = $2", space_id, team_id
    )
    if result == "DELETE 0":
        raise ApiError("not_found", status_code=404)
    log.info("space_team_detached", space_id=str(space_id), team_id=str(team_id))
