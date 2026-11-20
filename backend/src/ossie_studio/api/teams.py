from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import ProfilesWrite, UsersRead, UsersWrite
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.profiles.service import db_get_profile
from ossie_studio.teams.service import (
    TeamNameTakenError,
    TeamNotFoundError,
    db_create_team_manual,
    db_delete_team,
    db_list_teams_detail,
    db_rename_team,
    db_set_team_profile,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/teams")


class TeamCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""


class TeamRenameBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""


class TeamProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str | None = None  # None = retirer le profil de l'équipe


@router.get("")
async def list_teams(conn: ConnDep, _: UsersRead) -> dict[str, object]:
    return {"teams": await db_list_teams_detail(conn)}


@router.post("", status_code=201)
async def create_team(conn: ConnDep, _: UsersWrite, body: TeamCreateBody) -> dict[str, object]:
    name = body.name.strip()
    if not name:
        raise ApiError("invalid_request", status_code=400, fields=["team_name_required"])
    team = await db_create_team_manual(conn, name)
    log.info("team_created_manually", team_id=str(team.id), name=team.name)
    return {"id": str(team.id), "name": team.name, "origin": team.origin}


@router.put("/{team_id}")
async def rename_team(
    conn: ConnDep, _: UsersWrite, team_id: uuid.UUID, body: TeamRenameBody
) -> dict[str, bool]:
    name = body.name.strip()
    if not name:
        raise ApiError("invalid_request", status_code=400, fields=["team_name_required"])
    try:
        await db_rename_team(conn, team_id, name)
    except TeamNotFoundError:
        raise ApiError("team_not_found", status_code=404) from None
    except TeamNameTakenError:
        raise ApiError("team_name_taken", status_code=409) from None
    return {"ok": True}


@router.delete("/{team_id}")
async def delete_team(conn: ConnDep, _: UsersWrite, team_id: uuid.UUID) -> dict[str, bool]:
    if not await db_delete_team(conn, team_id):
        raise ApiError("team_not_found", status_code=404)
    log.info("team_deleted", team_id=str(team_id))
    return {"ok": True}


@router.put("/{team_id}/profile")
async def set_team_profile(
    conn: ConnDep, user: ProfilesWrite, team_id: uuid.UUID, body: TeamProfileBody
) -> dict[str, bool]:
    # DROIT FORT (profiles:write) : associer un profil à une équipe donne ses
    # capacités à tous les membres — une équipe « Administrateur » les rendrait
    # admins. Volontairement distinct de la gestion des utilisateurs.
    profile_id: uuid.UUID | None = None
    if body.profile_id is not None:
        try:
            profile_id = uuid.UUID(body.profile_id)
        except ValueError:
            raise ApiError("profile_not_found", status_code=404) from None
        if await db_get_profile(conn, profile_id) is None:
            raise ApiError("profile_not_found", status_code=404)
    try:
        await db_set_team_profile(conn, team_id, profile_id)
    except TeamNotFoundError:
        raise ApiError("team_not_found", status_code=404) from None
    log.info(
        "team_profile_set", team_id=str(team_id),
        profile=str(profile_id) if profile_id else None, author=user.email,
    )
    return {"ok": True}
