"""« Mes connexions agents » : chaque personne voit les clients qu'elle a
connectés par OAuth, peut changer leur profil agent ou les révoquer.
Qui a la capacité « connexions d'agents » voit toutes les connexions et peut
les révoquer (pas en changer le profil : il appartient à la personne)."""

from __future__ import annotations

import uuid
from datetime import datetime

import asyncpg
import structlog
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import AgentConnectionsRead, CurrentUser
from ossie_studio.api.oauth import usable_profile, usable_profiles
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.oauth.service import db_revoke_connection
from ossie_studio.profiles.service import load_capabilities

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/agent-connections")

_SELECT = (
    "SELECT c.*, cl.client_name, p.name AS agent_profile_name, u.email AS user_email "
    "FROM oauth_connections c "
    "JOIN oauth_clients cl ON cl.client_id = c.client_id "
    "JOIN agent_profiles p ON p.id = c.agent_profile_id "
    "JOIN users u ON u.id = c.user_id "
)
_ORDER = " ORDER BY c.revoked_at IS NOT NULL, c.created_at DESC"


class ProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_profile_id: str = ""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _payload(row: asyncpg.Record) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "client": {"id": row["client_id"], "name": row["client_name"]},
        "agent_profile": {"id": str(row["agent_profile_id"]), "name": row["agent_profile_name"]},
        "user": row["user_email"],
        "state": "revoked" if row["revoked_at"] is not None else "active",
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
        "last_used_at": _iso(row["last_used_at"]),
        "revoked_at": _iso(row["revoked_at"]),
    }


async def _get(conn: ConnDep, connection_id: uuid.UUID) -> asyncpg.Record | None:
    row: asyncpg.Record | None = await conn.fetchrow(_SELECT + "WHERE c.id = $1", connection_id)
    return row


@router.get("")
async def my_connections(conn: ConnDep, user: CurrentUser) -> dict[str, object]:
    rows = await conn.fetch(_SELECT + "WHERE c.user_id = $1" + _ORDER, user.id)
    return {
        "connections": [_payload(r) for r in rows],
        "agent_profiles": await usable_profiles(conn, user),
    }


@router.get("/all")
async def all_connections(conn: ConnDep, _: AgentConnectionsRead) -> dict[str, object]:
    rows = await conn.fetch(_SELECT + "WHERE TRUE" + _ORDER)
    return {"connections": [_payload(r) for r in rows]}


@router.put("/{connection_id}/profile")
async def change_profile(
    conn: ConnDep, user: CurrentUser, connection_id: uuid.UUID, body: ProfileBody
) -> dict[str, object]:
    row = await _get(conn, connection_id)
    # Le profil d'une connexion appartient à sa personne : pour les autres, elle n'existe pas.
    if row is None or row["user_id"] != user.id:
        raise ApiError("agent_connection_not_found", status_code=404)
    if row["revoked_at"] is not None:
        raise ApiError("agent_connection_revoked", status_code=409)
    profile = await usable_profile(conn, user, body.agent_profile_id)
    await conn.execute(
        "UPDATE oauth_connections SET agent_profile_id = $2, updated_at = now() WHERE id = $1",
        connection_id,
        profile.id,
    )
    log.info(
        "oauth_connection_profile_changed",
        connection_id=str(connection_id),
        client_id=row["client_id"],
        agent_profile_id=str(profile.id),
        author=user.email,
    )
    updated = await _get(conn, connection_id)
    assert updated is not None
    return _payload(updated)


@router.post("/{connection_id}/revoke")
async def revoke(conn: ConnDep, user: CurrentUser, connection_id: uuid.UUID) -> dict[str, object]:
    row = await _get(conn, connection_id)
    allowed = row is not None and (
        row["user_id"] == user.id
        or "agent_connections:write" in await load_capabilities(conn, user)
    )
    if row is None or not allowed:
        raise ApiError("agent_connection_not_found", status_code=404)
    if await db_revoke_connection(conn, connection_id, user.id):
        log.info(
            "oauth_connection_revoked",
            connection_id=str(connection_id),
            client_id=row["client_id"],
            owner=row["user_email"],
            author=user.email,
        )
    updated = await _get(conn, connection_id)
    assert updated is not None
    return _payload(updated)
