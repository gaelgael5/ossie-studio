from __future__ import annotations

import uuid

import asyncpg
import structlog
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import UsersRead, UsersWrite
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.profiles.service import db_get_profile
from ossie_studio.teams.service import (
    TeamNotFoundError,
    db_assign_team,
    db_remove_team_pin,
)
from ossie_studio.users.service import (
    AdminLockoutError,
    UserNotFoundError,
    db_assign_profile,
    db_search_users,
    db_set_pin,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/users")


class AssignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = ""


class TeamAssignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team_id: str = ""


def _parse_uuid(value: str) -> uuid.UUID | None:
    if not value.strip():
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _user_payload(row: asyncpg.Record) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "display_name": row["display_name"],
        "is_oidc": row["is_oidc"],
        "last_login_at": row["last_login_at"].isoformat() if row["last_login_at"] else None,
        "profile_id": str(row["profile_id"]) if row["profile_id"] else None,
        "profile_name": row["profile_name"],
        "profile_pinned": row["profile_pinned"],
        "team_id": str(row["team_id"]) if row["team_id"] else None,
        "team_name": row["team_name"],
        "team_origin": row["team_origin"],
        "team_pinned": row["team_pinned"],
    }


@router.get("")
async def list_users(
    conn: ConnDep,
    _: UsersRead,
    q: str = "",
    profile_id: str = "",
    team_id: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    rows, total = await db_search_users(
        conn,
        q,
        _parse_uuid(profile_id),
        max(1, min(limit, 200)),
        max(0, offset),
        _parse_uuid(team_id),
    )
    return {"users": [_user_payload(r) for r in rows], "total": total}


@router.put("/{user_id}/profile")
async def assign_profile(
    conn: ConnDep, user: UsersWrite, user_id: uuid.UUID, body: AssignBody
) -> dict[str, bool]:
    try:
        profile_id = uuid.UUID(body.profile_id)
    except ValueError:
        raise ApiError("profile_not_found", status_code=404) from None
    if await db_get_profile(conn, profile_id) is None:
        raise ApiError("profile_not_found", status_code=404)
    try:
        old_profile_id, _was_admin = await db_assign_profile(conn, user_id, profile_id)
    except UserNotFoundError:
        raise ApiError("user_not_found", status_code=404) from None
    except AdminLockoutError:
        raise ApiError("admin_lockout", status_code=409) from None
    log.info(
        "profile_assigned",
        user_id=str(user_id),
        old_profile=str(old_profile_id) if old_profile_id else None,
        new_profile=str(profile_id),
        author=user.email,
    )
    return {"ok": True}


@router.delete("/{user_id}/pin")
async def remove_pin(conn: ConnDep, user: UsersWrite, user_id: uuid.UUID) -> dict[str, bool]:
    # Retire la marque « fixé à la main » : les correspondances OIDC pourront de
    # nouveau choisir le profil à la connexion suivante.
    if not await db_set_pin(conn, user_id, pinned=False):
        raise ApiError("user_not_found", status_code=404)
    log.info("profile_pin_removed", user_id=str(user_id), author=user.email)
    return {"ok": True}


@router.put("/{user_id}/team")
async def assign_team(
    conn: ConnDep, user: UsersWrite, user_id: uuid.UUID, body: TeamAssignBody
) -> dict[str, bool]:
    # Assignation à la main : surcharge l'équipe du claim et la fixe. Affichage
    # seulement : ne change aucun droit.
    try:
        team_id = uuid.UUID(body.team_id)
    except ValueError:
        raise ApiError("team_not_found", status_code=404) from None
    try:
        old_team_id = await db_assign_team(conn, user_id, team_id)
    except UserNotFoundError:
        raise ApiError("user_not_found", status_code=404) from None
    except TeamNotFoundError:
        raise ApiError("team_not_found", status_code=404) from None
    log.info(
        "team_assigned",
        user_id=str(user_id),
        old_team=str(old_team_id) if old_team_id else None,
        new_team=str(team_id),
        author=user.email,
    )
    return {"ok": True}


@router.delete("/{user_id}/team-pin")
async def remove_team_pin(
    conn: ConnDep, user: UsersWrite, user_id: uuid.UUID
) -> dict[str, bool]:
    # Retire la marque « fixée à la main » : le claim reprend la main à la
    # connexion suivante.
    if not await db_remove_team_pin(conn, user_id):
        raise ApiError("user_not_found", status_code=404)
    log.info("team_pin_removed", user_id=str(user_id), author=user.email)
    return {"ok": True}
