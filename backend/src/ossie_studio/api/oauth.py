"""Écran de consentement d'une connexion OAuth : la personne, connectée à l'IHM,
voit le client qui demande l'accès et choisit le profil agent à lui accorder.

Droits réellement accordés = profil agent ∩ capacités de la personne : l'écran
ne propose que les profils dont au moins une primitive lui est utilisable, et
montre ce qui sera accordé et ce qui ne le sera pas.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter
from mcp.server.auth.provider import construct_redirect_uri
from pydantic import BaseModel, ConfigDict

from ossie_studio.agent_profiles.service import (
    AgentProfile,
    db_get_agent_profile,
    db_list_agent_profiles,
)
from ossie_studio.api.common import CurrentUser
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.mcp_surface.authz import capabilities_anywhere
from ossie_studio.mcp_surface.catalog import BY_ID
from ossie_studio.models import User
from ossie_studio.oauth.service import (
    AuthorizationRequest,
    db_claim_request,
    db_create_code,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/oauth/requests")


class ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_profile_id: str = ""


def granted_primitives(profile: AgentProfile, caps: set[str]) -> list[str]:
    """Primitives du profil réellement utilisables par la personne."""
    return sorted(p for p in profile.effective_primitives() if BY_ID[p].capability in caps)


async def usable_profiles(conn: ConnDep, user: User) -> list[dict[str, object]]:
    """Profils agents choisissables par la personne (au moins une primitive
    utilisable), avec ce qui sera accordé et ce qui ne le sera pas."""
    caps = await capabilities_anywhere(conn, user)
    out: list[dict[str, object]] = []
    for profile in await db_list_agent_profiles(conn):
        granted = granted_primitives(profile, caps)
        if not granted:
            continue
        out.append(
            {
                "id": str(profile.id),
                "name": profile.name,
                "description": profile.description,
                "granted": granted,
                "withheld": sorted(profile.effective_primitives() - set(granted)),
            }
        )
    return out


async def usable_profile(conn: ConnDep, user: User, raw_id: str) -> AgentProfile:
    """Profil agent désigné, à condition qu'au moins une primitive soit utilisable."""
    try:
        profile_id = uuid.UUID(raw_id.strip())
    except ValueError:
        raise ApiError(
            "invalid_request", status_code=400, fields=["agent_profile_required"]
        ) from None
    profile = await db_get_agent_profile(conn, profile_id)
    if profile is None:
        raise ApiError("agent_profile_not_found", status_code=400)
    if not granted_primitives(profile, await capabilities_anywhere(conn, user)):
        raise ApiError("agent_profile_not_usable", status_code=403)
    return profile


async def _request(conn: ConnDep, user: User, request_id: str) -> AuthorizationRequest:
    request = await db_claim_request(conn, request_id, user.id)
    if request is None:
        raise ApiError("oauth_request_not_found", status_code=404)
    return request


async def _consume(conn: ConnDep, request: AuthorizationRequest) -> None:
    """La demande ne sert qu'une fois (même sous deux clics concurrents)."""
    deleted = await conn.fetchval(
        "DELETE FROM oauth_authorization_requests WHERE id = $1 AND user_id = $2 RETURNING 1",
        request.id,
        request.user_id,
    )
    if deleted is None:
        raise ApiError("oauth_request_not_found", status_code=404)


@router.get("/{request_id}")
async def get_request(conn: ConnDep, user: CurrentUser, request_id: str) -> dict[str, object]:
    request = await _request(conn, user, request_id)
    current = await conn.fetchval(
        "SELECT agent_profile_id FROM oauth_connections "
        "WHERE user_id = $1 AND client_id = $2 AND revoked_at IS NULL",
        user.id,
        request.client_id,
    )
    return {
        "client": {
            "id": request.client_id,
            "name": request.client_name,
            "redirect_uri": request.redirect_uri,
        },
        "agent_profiles": await usable_profiles(conn, user),
        "current_agent_profile_id": str(current) if current else None,
    }


@router.post("/{request_id}/approve")
async def approve_request(
    conn: ConnDep, user: CurrentUser, request_id: str, body: ApproveBody
) -> dict[str, object]:
    request = await _request(conn, user, request_id)
    profile = await usable_profile(conn, user, body.agent_profile_id)
    async with conn.transaction():
        await _consume(conn, request)
        code = await db_create_code(conn, request, profile.id)
    log.info(
        "oauth_consent_granted",
        author=user.email,
        client_id=request.client_id,
        client_name=request.client_name,
        agent_profile_id=str(profile.id),
    )
    redirect = construct_redirect_uri(request.redirect_uri, code=code, state=request.state)
    return {"redirect_url": redirect}


@router.post("/{request_id}/deny")
async def deny_request(conn: ConnDep, user: CurrentUser, request_id: str) -> dict[str, object]:
    request = await _request(conn, user, request_id)
    await _consume(conn, request)
    log.info(
        "oauth_consent_denied",
        author=user.email,
        client_id=request.client_id,
        client_name=request.client_name,
    )
    return {
        "redirect_url": construct_redirect_uri(
            request.redirect_uri, error="access_denied", state=request.state
        )
    }
