from __future__ import annotations

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import UsersRead, UsersWrite
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.teams.service import db_create_team_manual, db_list_teams

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/teams")


class TeamCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""


@router.get("")
async def list_teams(conn: ConnDep, _: UsersRead) -> dict[str, object]:
    teams = await db_list_teams(conn)
    return {
        "teams": [
            {"id": str(t.id), "name": t.name, "slug": t.slug, "origin": t.origin} for t in teams
        ]
    }


@router.post("", status_code=201)
async def create_team(conn: ConnDep, _: UsersWrite, body: TeamCreateBody) -> dict[str, object]:
    name = body.name.strip()
    if not name:
        raise ApiError("invalid_request", status_code=400, fields=["team_name_required"])
    team = await db_create_team_manual(conn, name)
    log.info("team_created_manually", team_id=str(team.id), name=team.name)
    return {"id": str(team.id), "name": team.name, "origin": team.origin}
