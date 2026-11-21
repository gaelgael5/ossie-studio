from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

import httpx
import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import OidcRead, OidcWrite
from ossie_studio.deps import ConnDep, get_http_client_factory
from ossie_studio.errors import ApiError
from ossie_studio.oidc.discovery_cache import connector_discovery_status
from ossie_studio.oidc.forms import DEFAULT_SCOPES, parse_connector
from ossie_studio.oidc.mapping import (
    MappingDuplicateError,
    db_create_mapping,
    db_delete_mapping,
    db_list_mappings,
    db_reorder_mappings,
    db_set_roles_claim,
)
from ossie_studio.oidc.provider import DiscoveryError, discover
from ossie_studio.oidc.redirect import public_callback_url
from ossie_studio.oidc.service import (
    IssuerAlreadyDeclaredError,
    db_count_identities_for_issuer,
    db_create_connector,
    db_delete_connector,
    db_get_connector,
    db_list_connectors,
)
from ossie_studio.profiles.service import db_get_profile, db_list_profiles
from ossie_studio.secretstore.service import db_get_secret
from ossie_studio.secretstore.usages import db_delete_secret_if_unused
from ossie_studio.teams.service import db_list_teams_for_connector, db_set_team_claim
from ossie_studio.users.service import db_count_pinned_users

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/oidc/connectors")

HttpFactoryDep = Annotated[Callable[[], httpx.AsyncClient], Depends(get_http_client_factory)]

OIDC_SECRET_CATEGORY = "oidc"  # noqa: S105  (nom de catégorie, pas un secret)


class ConnectorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = ""
    issuer: str = ""
    client_id: str = ""
    client_secret_slug: str = ""
    scopes: str = DEFAULT_SCOPES


class RolesClaimBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    roles_claim: str = ""


class TeamClaimBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team_claim: str = ""


class MappingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role_value: str = ""
    profile_id: str = ""


class MappingReorderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: list[str] = []


@router.get("")
async def list_connectors(request: Request, conn: ConnDep, _: OidcRead) -> dict[str, object]:
    connectors = await db_list_connectors(conn)
    profile_names = {p.id: p.name for p in await db_list_profiles(conn)}
    rows = []
    for c in connectors:
        mappings = await db_list_mappings(conn, c.id)
        teams = await db_list_teams_for_connector(conn, c.id)
        rows.append(
            {
                "id": str(c.id),
                "label": c.label,
                "issuer": c.issuer,
                "accounts": await db_count_identities_for_issuer(conn, c.issuer),
                "client_secret_slug": c.client_secret_slug,
                "roles_claim": c.roles_claim,
                "team_claim": c.team_claim,
                "mappings": [
                    {
                        "id": str(m.id),
                        "role_value": m.role_value,
                        "profile_id": str(m.profile_id),
                        "profile_name": profile_names.get(m.profile_id),
                    }
                    for m in mappings
                ],
                # Équipes venues de ce connecteur (affichage seulement).
                "teams": [{"id": str(t.id), "name": t.name, "slug": t.slug} for t in teams],
            }
        )
    return {
        "connectors": rows,
        "default_scopes": DEFAULT_SCOPES,
        "pinned_count": await db_count_pinned_users(conn),
        # URL de retour UNIQUE à déclarer chez le fournisseur (tous connecteurs).
        "callback_url": public_callback_url(request),
    }


class IssuerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issuer: str = ""


@router.post("/check-discovery")
async def check_discovery(
    _: OidcWrite, http_client_factory: HttpFactoryDep, body: IssuerBody
) -> dict[str, bool]:
    # Vérification à la demande, sans rien enregistrer : alimente le bouton
    # « Vérifier la découverte » du dialogue d'ajout.
    issuer = body.issuer.strip().rstrip("/")
    if not issuer:
        return {"ok": False}
    async with http_client_factory() as client:
        try:
            await discover(client, issuer)
        except DiscoveryError:
            return {"ok": False}
    return {"ok": True}


@router.get("/discovery")
async def connectors_discovery(
    conn: ConnDep, _: OidcRead, http_client_factory: HttpFactoryDep
) -> dict[str, str]:
    # Sondé à part de la liste (réseau) : « ok » ou « unreachable » par
    # connecteur, mis en cache quelques minutes. Jamais bloquant.
    connectors = await db_list_connectors(conn)
    return {
        str(c.id): await connector_discovery_status(http_client_factory, c.issuer)
        for c in connectors
    }


@router.post("", status_code=201)
async def create_connector(
    conn: ConnDep,
    _: OidcWrite,
    http_client_factory: HttpFactoryDep,
    body: ConnectorCreate,
) -> dict[str, str]:
    form = parse_connector(body.label, body.issuer, body.client_id, body.scopes)
    if form.errors:
        raise ApiError("invalid_request", status_code=400, fields=form.errors)

    # Le client secret est un secret de catégorie « oidc », désigné par son slug
    # et jamais saisi ici. On vérifie qu'il existe et qu'il est bien de cette
    # catégorie avant tout (local, pas de réseau).
    slug = body.client_secret_slug.strip()
    if not slug:
        raise ApiError("invalid_request", status_code=400, fields=["client_secret_required"])
    secret = await db_get_secret(conn, slug)
    if secret is None or secret.category != OIDC_SECRET_CATEGORY:
        raise ApiError("oidc_secret_invalid", status_code=400, slug=slug)

    # On interroge la découverte AVANT d'enregistrer.
    async with http_client_factory() as client:
        try:
            await discover(client, form.issuer)
        except DiscoveryError:
            raise ApiError("oidc_issuer_unreachable", status_code=400, issuer=form.issuer) from None

    try:
        connector = await db_create_connector(
            conn, form.label, form.issuer, form.client_id, slug, form.scopes
        )
    except IssuerAlreadyDeclaredError:
        raise ApiError(
            "oidc_issuer_already_declared", status_code=409, issuer=form.issuer
        ) from None
    log.info("oidc_connector_created", issuer=form.issuer)
    return {"id": str(connector.id)}


@router.delete("/{connector_id}", status_code=204)
async def delete_connector(
    conn: ConnDep, _: OidcWrite, connector_id: uuid.UUID, delete_secret: bool = False
) -> None:
    connector = await db_get_connector(conn, connector_id)
    if connector is None:
        raise ApiError("not_found", status_code=404)
    # Les comptes rattachés et leur couple (issuer, sub) sont conservés : seul le
    # connecteur disparaît. Le secret ne part que si l'administrateur l'a demandé
    # ET qu'aucun autre objet ne l'utilise (vérifié côté serveur, à l'instant).
    await db_delete_connector(conn, connector_id)
    secret_outcome = "kept"  # noqa: S105  (état journalisé, pas un secret)
    if delete_secret:
        secret_outcome = await db_delete_secret_if_unused(conn, connector.client_secret_slug)
    log.info("oidc_connector_deleted", issuer=connector.issuer, secret=secret_outcome)


async def _require_connector(conn: ConnDep, connector_id: uuid.UUID) -> None:
    if await db_get_connector(conn, connector_id) is None:
        raise ApiError("not_found", status_code=404)


@router.put("/{connector_id}/roles-claim")
async def set_roles_claim(
    conn: ConnDep, _: OidcWrite, connector_id: uuid.UUID, body: RolesClaimBody
) -> dict[str, bool]:
    await _require_connector(conn, connector_id)
    await db_set_roles_claim(conn, connector_id, body.roles_claim.strip())
    log.info("oidc_roles_claim_set", connector_id=str(connector_id))
    return {"ok": True}


@router.put("/{connector_id}/team-claim")
async def set_team_claim(
    conn: ConnDep, _: OidcWrite, connector_id: uuid.UUID, body: TeamClaimBody
) -> dict[str, bool]:
    await _require_connector(conn, connector_id)
    await db_set_team_claim(conn, connector_id, body.team_claim.strip())
    log.info("oidc_team_claim_set", connector_id=str(connector_id))
    return {"ok": True}


@router.post("/{connector_id}/mappings", status_code=201)
async def add_mapping(
    conn: ConnDep, _: OidcWrite, connector_id: uuid.UUID, body: MappingBody
) -> dict[str, object]:
    await _require_connector(conn, connector_id)
    role_value = body.role_value.strip()
    if not role_value:
        raise ApiError("invalid_request", status_code=400, fields=["role_value_required"])
    try:
        profile_id = uuid.UUID(body.profile_id)
    except ValueError:
        raise ApiError("profile_not_found", status_code=404) from None
    if await db_get_profile(conn, profile_id) is None:
        raise ApiError("profile_not_found", status_code=404)
    try:
        mapping = await db_create_mapping(conn, connector_id, role_value, profile_id)
    except MappingDuplicateError:
        raise ApiError("mapping_duplicate", status_code=409) from None
    log.info("oidc_mapping_added", connector_id=str(connector_id), role=role_value)
    return {"id": str(mapping.id), "role_value": mapping.role_value}


@router.delete("/{connector_id}/mappings/{mapping_id}", status_code=204)
async def delete_mapping(
    conn: ConnDep, _: OidcWrite, connector_id: uuid.UUID, mapping_id: uuid.UUID
) -> None:
    await _require_connector(conn, connector_id)
    if not await db_delete_mapping(conn, mapping_id):
        raise ApiError("mapping_not_found", status_code=404)
    log.info("oidc_mapping_deleted", connector_id=str(connector_id))


@router.put("/{connector_id}/mappings")
async def reorder_mappings(
    conn: ConnDep, _: OidcWrite, connector_id: uuid.UUID, body: MappingReorderBody
) -> dict[str, bool]:
    await _require_connector(conn, connector_id)
    try:
        ordered = [uuid.UUID(value) for value in body.order]
    except ValueError:
        raise ApiError("invalid_request", status_code=400) from None
    await db_reorder_mappings(conn, connector_id, ordered)
    log.info("oidc_mappings_reordered", connector_id=str(connector_id))
    return {"ok": True}
