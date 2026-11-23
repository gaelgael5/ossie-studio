from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import ProfilesRead, ProfilesWrite
from ossie_studio.audit.service import CREATED, DELETED, UPDATED, audited
from ossie_studio.authz.catalog import ALL_CAPABILITIES, catalog_payload, is_known_capability
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.models import Profile
from ossie_studio.oidc.mapping import db_count_mappings_for_profile
from ossie_studio.profiles.service import (
    ProfileNameTakenError,
    ProfileNotFoundError,
    ProfileSlugTakenError,
    db_count_users_with_profile,
    db_create_profile,
    db_delete_profile,
    db_free_profile_slug,
    db_get_profile,
    db_list_profiles,
    db_set_default_profile,
    db_update_profile,
)
from ossie_studio.slugs import free_slug, is_valid_slug, slugify

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/profiles")


class ProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    # À la création seulement (déduit du nom par l'écran, modifiable) ; ignoré à
    # la modification : un slug est figé.
    slug: str = ""
    description: str = ""
    capabilities: list[str] = []


def _clean_capabilities(capabilities: list[str]) -> list[str]:
    unknown = [c for c in capabilities if not is_known_capability(c)]
    if unknown:
        raise ApiError("invalid_request", status_code=400, fields=["capability_unknown"])
    # Dédoublonne en gardant l'ordre du catalogue n'est pas requis ; on garde un
    # ensemble stable.
    return sorted(set(capabilities))


def _payload(profile: Profile, user_count: int) -> dict[str, object]:
    # Le profil administrateur a toutes les capacités (calculées), cases cochées
    # et verrouillées côté écran.
    caps = sorted(ALL_CAPABILITIES) if profile.is_admin else sorted(profile.capabilities)
    return {
        "id": str(profile.id),
        "name": profile.name,
        "slug": profile.slug,
        "description": profile.description,
        "capabilities": caps,
        "is_admin": profile.is_admin,
        "is_default": profile.is_default,
        "user_count": user_count,
    }


@router.get("")
async def list_profiles(conn: ConnDep, _: ProfilesRead) -> dict[str, object]:
    profiles = await db_list_profiles(conn)
    out = []
    for profile in profiles:
        count = await db_count_users_with_profile(conn, profile.id)
        out.append(_payload(profile, count))
    return {"profiles": out, "catalog": catalog_payload()}


@router.post("", status_code=201)
async def create_profile(
    conn: ConnDep, user: ProfilesWrite, body: ProfileBody
) -> dict[str, object]:
    name = body.name.strip()
    if not name:
        raise ApiError("invalid_request", status_code=400, fields=["profile_name_required"])
    caps = _clean_capabilities(body.capabilities)
    slug = body.slug.strip()
    if slug and not is_valid_slug(slug):
        raise ApiError("invalid_request", status_code=400, fields=["profile_slug_invalid"])
    if not slug:
        slug = await free_slug(conn, "profiles", slugify(name, fallback="profil"))
    try:
        async with audited(
            conn, user, CREATED, "profile", detail={"capabilities": sorted(caps)}
        ) as entry:
            profile = await db_create_profile(conn, name, body.description.strip(), caps, slug)
            entry.target(profile.id, profile.name)
    except ProfileSlugTakenError:
        raise ApiError("profile_slug_taken", status_code=409, slug=slug) from None
    except ProfileNameTakenError:
        raise ApiError("profile_name_taken", status_code=409, name=name) from None
    log.info("profile_created", profile_id=str(profile.id), author=user.email)
    return _payload(profile, 0)


@router.post("/{profile_id}/duplicate", status_code=201)
async def duplicate_profile(
    conn: ConnDep, user: ProfilesWrite, profile_id: uuid.UUID
) -> dict[str, object]:
    source = await db_get_profile(conn, profile_id)
    if source is None:
        raise ApiError("profile_not_found", status_code=404)
    caps = sorted(ALL_CAPABILITIES) if source.is_admin else source.capabilities
    base = f"{source.name} (copie)"
    name, suffix = base, 2
    while True:
        try:
            async with audited(
                conn, user, CREATED, "profile", detail={"duplicated_from": source.name}
            ) as entry:
                copy = await db_create_profile(
                    conn,
                    name,
                    source.description,
                    caps,
                    await db_free_profile_slug(conn, f"{source.slug or 'profil'}-copie"),
                )
                entry.target(copy.id, copy.name)
            break
        except ProfileNameTakenError:
            name = f"{base} {suffix}"
            suffix += 1
    log.info(
        "profile_duplicated", profile_id=str(copy.id), source=str(profile_id), author=user.email
    )
    return _payload(copy, 0)


@router.put("/{profile_id}")
async def update_profile(
    conn: ConnDep, user: ProfilesWrite, profile_id: uuid.UUID, body: ProfileBody
) -> dict[str, object]:
    source = await db_get_profile(conn, profile_id)
    if source is None:
        raise ApiError("profile_not_found", status_code=404)
    if source.is_admin:
        # Le profil administrateur ne se modifie pas.
        raise ApiError("profile_admin_protected", status_code=409)
    name = body.name.strip()
    if not name:
        raise ApiError("invalid_request", status_code=400, fields=["profile_name_required"])
    caps = _clean_capabilities(body.capabilities)
    try:
        async with audited(
            conn, user, UPDATED, "profile", profile_id, name, {"capabilities": sorted(caps)}
        ):
            profile = await db_update_profile(
                conn, profile_id, name, body.description.strip(), caps
            )
    except ProfileNameTakenError:
        raise ApiError("profile_name_taken", status_code=409, name=name) from None
    except ProfileNotFoundError:
        raise ApiError("profile_not_found", status_code=404) from None
    count = await db_count_users_with_profile(conn, profile_id)
    log.info("profile_updated", profile_id=str(profile_id), author=user.email)
    return _payload(profile, count)


@router.delete("/{profile_id}", status_code=204)
async def delete_profile(conn: ConnDep, user: ProfilesWrite, profile_id: uuid.UUID) -> None:
    source = await db_get_profile(conn, profile_id)
    if source is None:
        raise ApiError("profile_not_found", status_code=404)
    if source.is_admin:
        raise ApiError("profile_admin_protected", status_code=409)
    if source.is_default:
        # Le profil par défaut ne se supprime pas tant qu'il porte la marque.
        raise ApiError("profile_default_protected", status_code=409)
    count = await db_count_users_with_profile(conn, profile_id)
    if count > 0:
        raise ApiError("profile_in_use", status_code=409, count=count)
    mappings = await db_count_mappings_for_profile(conn, profile_id)
    if mappings > 0:
        # Un profil visé par une correspondance OIDC n'est pas supprimable.
        raise ApiError("profile_in_mapping", status_code=409, count=mappings)
    async with audited(conn, user, DELETED, "profile", profile_id, source.name):
        await db_delete_profile(conn, profile_id)
    log.info("profile_deleted", profile_id=str(profile_id), author=user.email)


@router.post("/{profile_id}/default")
async def set_default(
    conn: ConnDep, user: ProfilesWrite, profile_id: uuid.UUID
) -> dict[str, object]:
    source = await db_get_profile(conn, profile_id)
    if source is None:
        raise ApiError("profile_not_found", status_code=404)
    if source.is_admin:
        # L'Administrateur ne peut pas être le profil par défaut (il ouvrirait
        # tous les droits à quiconque se connecte).
        raise ApiError("profile_admin_default", status_code=409)
    async with audited(
        conn, user, UPDATED, "profile", profile_id, source.name, {"change": "default"}
    ):
        profile = await db_set_default_profile(conn, profile_id)
    count = await db_count_users_with_profile(conn, profile_id)
    log.info("profile_default_set", profile_id=str(profile_id), author=user.email)
    return _payload(profile, count)
