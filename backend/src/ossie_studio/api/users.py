from __future__ import annotations

import uuid

import asyncpg
import structlog
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import UsersRead, UsersWrite
from ossie_studio.audit.service import UPDATED, audited
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.profiles.service import db_get_profile
from ossie_studio.teams.service import (
    TeamNotFoundError,
    db_add_user_team,
    db_remove_user_team,
    db_teams_for_users,
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


class TeamAddBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team_id: str = ""


def _parse_uuid(value: str) -> uuid.UUID | None:
    if not value.strip():
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


async def db_space_rights_for_users(
    conn: asyncpg.Connection, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[dict[str, str]]]:
    """Droits par espace de chaque personne : espace, profil et équipe qui le donne."""
    rows = await conn.fetch(
        "SELECT ut.user_id, s.id AS space_id, s.name AS space_name, p.name AS profile_name, "
        "t.name AS team_name FROM user_teams ut "
        "JOIN space_team_profiles stp ON stp.team_id = ut.team_id "
        "JOIN git_spaces s ON s.id = stp.space_id JOIN teams t ON t.id = ut.team_id "
        "JOIN profiles p ON p.id = stp.profile_id "
        "WHERE ut.user_id = ANY($1::uuid[]) ORDER BY s.name, t.name",
        user_ids,
    )
    out: dict[uuid.UUID, list[dict[str, str]]] = {}
    for r in rows:
        out.setdefault(r["user_id"], []).append(
            {
                "space_id": str(r["space_id"]),
                "space_name": r["space_name"],
                "profile_name": r["profile_name"],
                "team_name": r["team_name"],
            }
        )
    return out


def _user_payload(
    row: asyncpg.Record,
    teams: list[dict[str, str]],
    space_rights: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "display_name": row["display_name"],
        "is_oidc": row["is_oidc"],
        "last_login_at": row["last_login_at"].isoformat() if row["last_login_at"] else None,
        "profile_id": str(row["profile_id"]) if row["profile_id"] else None,
        "profile_name": row["profile_name"],
        "profile_pinned": row["profile_pinned"],
        # Appartenance multiple : liste d'équipes, chacune avec l'origine de son
        # rattachement (oidc | manual).
        "teams": teams,
        # Droits par espace gagnés par ses équipes (espace, profil, équipe).
        "space_rights": space_rights or [],
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
    ids = [r["id"] for r in rows]
    teams_by_user = await db_teams_for_users(conn, ids)
    rights_by_user = await db_space_rights_for_users(conn, ids)
    return {
        "users": [
            _user_payload(r, teams_by_user.get(r["id"], []), rights_by_user.get(r["id"]))
            for r in rows
        ],
        "total": total,
    }


async def _email(conn: ConnDep, user_id: uuid.UUID) -> str:
    """Empreinte lisible du compte visé, figée dans la ligne d'audit."""
    return str(await conn.fetchval("SELECT email FROM users WHERE id = $1", user_id) or "")


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
        async with audited(
            conn,
            user,
            UPDATED,
            "user",
            user_id,
            await _email(conn, user_id),
            {"change": "profile", "profile": str(profile_id)},
        ):
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
    async with audited(
        conn, user, UPDATED, "user", user_id, await _email(conn, user_id), {"change": "unpinned"}
    ) as entry:
        done = await db_set_pin(conn, user_id, pinned=False)
        entry.skip = not done
    if not done:
        raise ApiError("user_not_found", status_code=404)
    log.info("profile_pin_removed", user_id=str(user_id), author=user.email)
    return {"ok": True}


@router.post("/{user_id}/teams")
async def add_team(
    conn: ConnDep, user: UsersWrite, user_id: uuid.UUID, body: TeamAddBody
) -> dict[str, bool]:
    # Rattachement à la main (origine manuelle) : s'ajoute aux équipes existantes
    # et n'est jamais retiré par la synchro OIDC. Ne change aucun droit (ticket
    # suivant pour le profil d'équipe).
    try:
        team_id = uuid.UUID(body.team_id)
    except ValueError:
        raise ApiError("team_not_found", status_code=404) from None
    try:
        async with audited(
            conn,
            user,
            UPDATED,
            "user",
            user_id,
            await _email(conn, user_id),
            {"change": "team_added", "team": str(team_id)},
        ):
            await db_add_user_team(conn, user_id, team_id)
    except UserNotFoundError:
        raise ApiError("user_not_found", status_code=404) from None
    except TeamNotFoundError:
        raise ApiError("team_not_found", status_code=404) from None
    log.info("team_added", user_id=str(user_id), team=str(team_id), author=user.email)
    return {"ok": True}


@router.delete("/{user_id}/teams/{team_id}")
async def remove_team(
    conn: ConnDep, user: UsersWrite, user_id: uuid.UUID, team_id: uuid.UUID
) -> dict[str, bool]:
    # Retire un rattachement (décision humaine). Idempotent : retirer un
    # rattachement absent n'est pas une erreur.
    async with audited(
        conn,
        user,
        UPDATED,
        "user",
        user_id,
        await _email(conn, user_id),
        {"change": "team_removed", "team": str(team_id)},
    ) as entry:
        removed = await db_remove_user_team(conn, user_id, team_id)
        entry.skip = not removed
    log.info(
        "team_removed",
        user_id=str(user_id),
        team=str(team_id),
        removed=removed,
        author=user.email,
    )
    return {"ok": True, "removed": removed}
