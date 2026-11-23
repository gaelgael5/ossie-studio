"""Profils agents : les primitives MCP qu'un agent peut appeler. Calqué sur les
profils utilisateurs, avec les primitives du catalogue à la place des
capacités. Un profil agent se choisit à l'émission d'une clé d'API ou à la
connexion OAuth d'un client ; il ne s'assigne jamais à une personne."""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ossie_studio.agent_profiles.service import (
    AgentProfile,
    AgentProfileNameTakenError,
    AgentProfileSlugTakenError,
    added_beyond,
    db_agent_profile_usage,
    db_create_agent_profile,
    db_delete_agent_profile,
    db_free_agent_profile_slug,
    db_get_agent_profile,
    db_list_agent_profiles,
    db_update_agent_profile,
)
from ossie_studio.api.common import AgentProfilesRead, AgentProfilesWrite
from ossie_studio.audit.service import CREATED, DELETED, UPDATED, audited
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.mcp_surface import catalog
from ossie_studio.models import User
from ossie_studio.slugs import is_valid_slug, slugify

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/agent-profiles")


class AgentProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    # À la création seulement (déduit du nom par l'écran, modifiable) ; ignoré à
    # la modification : un slug est figé.
    slug: str = ""
    description: str = ""
    primitives: list[str] = []
    # Reçoit toutes les primitives de lecture, y compris celles ajoutées plus tard.
    all_read: bool = False


async def _payload(conn: ConnDep, profile: AgentProfile) -> dict[str, object]:
    usage = await db_agent_profile_usage(conn, profile.id)
    return {
        "id": str(profile.id),
        "slug": profile.slug,
        "name": profile.name,
        "description": profile.description,
        "primitives": sorted(set(profile.primitives) & catalog.ALL_PRIMITIVES),
        "all_read": profile.all_read,
        "is_builtin": profile.is_builtin,
        "effective": sorted(profile.effective_primitives()),
        "used_by": {"keys": usage.keys, "connections": usage.connections},
    }


def _clean_primitives(primitives: list[str]) -> list[str]:
    if any(not catalog.is_known_primitive(p) for p in primitives):
        raise ApiError("invalid_request", status_code=400, fields=["primitive_unknown"])
    return sorted(set(primitives))


def _name(body: AgentProfileBody) -> str:
    name = body.name.strip()
    if not name:
        raise ApiError("invalid_request", status_code=400, fields=["agent_profile_name_required"])
    return name


async def _editable(conn: ConnDep, profile_id: uuid.UUID) -> AgentProfile:
    profile = await db_get_agent_profile(conn, profile_id)
    if profile is None:
        raise ApiError("agent_profile_not_found", status_code=404)
    if profile.is_builtin:
        # Le profil livré garde sa promesse (toutes les lectures) : on le duplique.
        raise ApiError("agent_profile_builtin_protected", status_code=409)
    return profile


async def _guard(conn: ConnDep, user: User, before: set[str], after: set[str]) -> None:
    beyond = await added_beyond(conn, user, before, after)
    if beyond:
        raise ApiError("agent_profile_beyond_rights", status_code=403, primitives=", ".join(beyond))


async def _create(
    conn: ConnDep,
    *,
    name: str,
    slug: str,
    description: str,
    primitives: list[str],
    all_read: bool,
) -> AgentProfile:
    try:
        return await db_create_agent_profile(
            conn,
            name=name,
            slug=slug,
            description=description,
            primitives=primitives,
            all_read=all_read,
        )
    except AgentProfileSlugTakenError:
        raise ApiError("agent_profile_slug_taken", status_code=409, slug=slug) from None
    except AgentProfileNameTakenError:
        raise ApiError("agent_profile_name_taken", status_code=409, name=name) from None


@router.get("")
async def list_agent_profiles(conn: ConnDep, _: AgentProfilesRead) -> dict[str, object]:
    profiles = [await _payload(conn, p) for p in await db_list_agent_profiles(conn)]
    return {"profiles": profiles, "catalog": catalog.catalog_payload()}


@router.post("", status_code=201)
async def create_agent_profile(
    conn: ConnDep, user: AgentProfilesWrite, body: AgentProfileBody
) -> dict[str, object]:
    name = _name(body)
    primitives = _clean_primitives(body.primitives)
    slug = body.slug.strip()
    if slug and not is_valid_slug(slug):
        raise ApiError("invalid_request", status_code=400, fields=["agent_profile_slug_invalid"])
    if not slug:
        slug = await db_free_agent_profile_slug(conn, slugify(name, fallback="profil-agent"))
    draft = AgentProfile(uuid.uuid4(), slug, name, "", primitives, body.all_read, False)
    await _guard(conn, user, set(), draft.effective_primitives())
    async with audited(
        conn, user, CREATED, "agent_profile", detail={"primitives": sorted(primitives)}
    ) as entry:
        profile = await _create(
            conn,
            name=name,
            slug=slug,
            description=body.description.strip(),
            primitives=primitives,
            all_read=body.all_read,
        )
        entry.target(profile.id, profile.name)
    log.info("agent_profile_created", agent_profile_id=str(profile.id), author=user.email)
    return await _payload(conn, profile)


@router.post("/{profile_id}/duplicate", status_code=201)
async def duplicate_agent_profile(
    conn: ConnDep, user: AgentProfilesWrite, profile_id: uuid.UUID
) -> dict[str, object]:
    source = await db_get_agent_profile(conn, profile_id)
    if source is None:
        raise ApiError("agent_profile_not_found", status_code=404)
    await _guard(conn, user, set(), source.effective_primitives())
    slug = await db_free_agent_profile_slug(conn, f"{source.slug}-copie")
    base = f"{source.name} (copie)"
    name, suffix = base, 2
    while True:
        try:
            async with audited(
                conn, user, CREATED, "agent_profile", detail={"duplicated_from": source.name}
            ) as entry:
                copy = await db_create_agent_profile(
                    conn,
                    name=name,
                    slug=slug,
                    description=source.description,
                    primitives=source.primitives,
                    all_read=source.all_read,
                )
                entry.target(copy.id, copy.name)
            break
        except AgentProfileNameTakenError:
            name, suffix = f"{base} {suffix}", suffix + 1
    log.info(
        "agent_profile_duplicated",
        agent_profile_id=str(copy.id),
        source=str(profile_id),
        author=user.email,
    )
    return await _payload(conn, copy)


@router.put("/{profile_id}")
async def update_agent_profile(
    conn: ConnDep, user: AgentProfilesWrite, profile_id: uuid.UUID, body: AgentProfileBody
) -> dict[str, object]:
    source = await _editable(conn, profile_id)
    name = _name(body)
    primitives = _clean_primitives(body.primitives)
    after = AgentProfile(
        source.id, source.slug, name, "", primitives, body.all_read, False
    ).effective_primitives()
    await _guard(conn, user, source.effective_primitives(), after)
    try:
        async with audited(
            conn,
            user,
            UPDATED,
            "agent_profile",
            profile_id,
            name,
            {
                "added": sorted(after - source.effective_primitives()),
                "removed": sorted(source.effective_primitives() - after),
            },
        ):
            profile = await db_update_agent_profile(
                conn,
                profile_id,
                name=name,
                description=body.description.strip(),
                primitives=primitives,
                all_read=body.all_read,
            )
    except AgentProfileNameTakenError:
        raise ApiError("agent_profile_name_taken", status_code=409, name=name) from None
    log.info(
        "agent_profile_updated",
        agent_profile_id=str(profile_id),
        added=sorted(after - source.effective_primitives()),
        removed=sorted(source.effective_primitives() - after),
        author=user.email,
    )
    return await _payload(conn, profile)


@router.delete("/{profile_id}", status_code=204)
async def delete_agent_profile(
    conn: ConnDep, user: AgentProfilesWrite, profile_id: uuid.UUID
) -> None:
    source = await _editable(conn, profile_id)
    usage = await db_agent_profile_usage(conn, profile_id)
    if usage.in_use:
        raise ApiError(
            "agent_profile_in_use",
            status_code=409,
            keys=", ".join(usage.keys) or "—",
            connections=", ".join(usage.connections) or "—",
        )
    async with audited(conn, user, DELETED, "agent_profile", profile_id, source.name):
        await db_delete_agent_profile(conn, profile_id)
    log.info("agent_profile_deleted", agent_profile_id=str(profile_id), author=user.email)
