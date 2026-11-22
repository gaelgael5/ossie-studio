"""Clés d'API de service : émettre (avec un profil agent), lister, changer le
profil, révoquer. Une clé n'ouvre que la surface MCP, jamais ces routes."""

from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from ossie_studio.agent_profiles.service import (
    AgentProfile,
    beyond_capabilities,
    db_get_agent_profile,
    db_list_agent_profiles,
    primitives_beyond,
)
from ossie_studio.api.common import ApiKeysRead, ApiKeysWrite
from ossie_studio.api_keys.service import (
    ApiKey,
    db_create_api_key,
    db_get_api_key,
    db_list_api_keys,
    db_revoke_api_key,
    db_set_api_key_profile,
)
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.mcp_surface.guard import resource_url
from ossie_studio.models import User
from ossie_studio.profiles.service import load_capabilities

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/api-keys")

_LABEL_MAX = 200


class IssueBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = ""
    agent_profile_id: str = ""


class ProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_profile_id: str = ""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _payload(key: ApiKey) -> dict[str, object]:
    return {
        "id": str(key.id),
        "label": key.label,
        "prefix": key.prefix,
        "agent_profile": {"id": str(key.agent_profile_id), "name": key.agent_profile_name},
        "state": "revoked" if key.revoked_at is not None else "active",
        "created_at": _iso(key.created_at),
        "created_by": key.created_by_email,
        "revoked_at": _iso(key.revoked_at),
        "revoked_by": key.revoked_by_email,
        "last_used_at": _iso(key.last_used_at),
    }


async def _key_or_404(conn: ConnDep, key_id: uuid.UUID) -> ApiKey:
    key = await db_get_api_key(conn, key_id)
    if key is None:
        raise ApiError("api_key_not_found", status_code=404)
    return key


async def _grantable_profile(conn: ConnDep, user: User, raw_id: str) -> AgentProfile:
    """Profil agent désigné, que la personne a le droit de donner à une clé :
    chacune de ses primitives suppose une capacité qu'elle a elle-même."""
    try:
        profile_id = uuid.UUID(raw_id.strip())
    except ValueError:
        raise ApiError(
            "invalid_request", status_code=400, fields=["agent_profile_required"]
        ) from None
    profile = await db_get_agent_profile(conn, profile_id)
    if profile is None:
        raise ApiError("agent_profile_not_found", status_code=400)
    beyond = await primitives_beyond(conn, user, profile)
    if beyond:
        raise ApiError("agent_profile_beyond_rights", status_code=403, primitives=", ".join(beyond))
    return profile


@router.get("")
async def list_api_keys(conn: ConnDep, _: ApiKeysRead) -> dict[str, object]:
    return {"keys": [_payload(k) for k in await db_list_api_keys(conn)]}


@router.get("/options")
async def api_key_options(request: Request, conn: ConnDep, user: ApiKeysRead) -> dict[str, object]:
    """Ce qu'il faut à l'écran d'émission : l'adresse de la surface MCP à
    déclarer chez le client, et les profils agents avec ce que la personne
    peut en donner."""
    caps = await load_capabilities(conn, user)
    profiles = []
    for profile in await db_list_agent_profiles(conn):
        beyond = beyond_capabilities(profile, caps)
        profiles.append(
            {
                "id": str(profile.id),
                "name": profile.name,
                "description": profile.description,
                "primitives": sorted(profile.key_primitives()),
                "grantable": not beyond,
                "beyond": beyond,
            }
        )
    configured = bool((request.app.state.settings.public_base_url or "").strip())
    return {
        "endpoint": resource_url(request),
        "endpoint_configured": configured,
        "agent_profiles": profiles,
    }


@router.post("", status_code=201)
async def issue_api_key(conn: ConnDep, user: ApiKeysWrite, body: IssueBody) -> JSONResponse:
    label = body.label.strip()
    if not label or len(label) > _LABEL_MAX:
        raise ApiError("invalid_request", status_code=400, fields=["label_required"])
    profile = await _grantable_profile(conn, user, body.agent_profile_id)
    key, secret = await db_create_api_key(conn, label, profile.id, user.id)
    log.info(
        "api_key_issued", key_id=str(key.id), agent_profile_id=str(profile.id), author=user.email
    )
    # Le secret n'est rendu qu'ici, et ne doit être gardé par aucun cache.
    return JSONResponse(
        {"key": _payload(key), "secret": secret},
        status_code=201,
        headers={"Cache-Control": "no-store"},
    )


@router.put("/{key_id}/profile")
async def change_api_key_profile(
    conn: ConnDep, user: ApiKeysWrite, key_id: uuid.UUID, body: ProfileBody
) -> dict[str, object]:
    key = await _key_or_404(conn, key_id)
    if key.revoked_at is not None:
        raise ApiError("api_key_revoked", status_code=409)
    profile = await _grantable_profile(conn, user, body.agent_profile_id)
    await db_set_api_key_profile(conn, key_id, profile.id)
    log.info(
        "api_key_profile_changed",
        key_id=str(key_id),
        agent_profile_id=str(profile.id),
        author=user.email,
    )
    return _payload(await _key_or_404(conn, key_id))


@router.post("/{key_id}/revoke")
async def revoke_api_key(conn: ConnDep, user: ApiKeysWrite, key_id: uuid.UUID) -> dict[str, object]:
    await _key_or_404(conn, key_id)
    if await db_revoke_api_key(conn, key_id, user.id):
        log.info("api_key_revoked", key_id=str(key_id), author=user.email)
    return _payload(await _key_or_404(conn, key_id))
