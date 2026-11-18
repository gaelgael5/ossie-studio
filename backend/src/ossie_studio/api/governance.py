from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import AdminApiDep, require_admin_api
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.governance.certifications import PRESETS, validate_certification
from ossie_studio.governance.service import (
    CertificationNotFoundError,
    CertificationTagTakenError,
    TagIdTakenError,
    TagNotFoundError,
    db_create_certification,
    db_create_certification_presets,
    db_create_tag,
    db_delete_certification,
    db_delete_tag,
    db_list_certifications,
    db_list_tags,
    db_update_certification,
    db_update_tag,
    db_upsert_tag,
)
from ossie_studio.governance.tags import normalize_tag_id, validate_tag_fields
from ossie_studio.governance.yaml_io import TagImportError, export_tags_yaml, parse_tags_yaml
from ossie_studio.models import Certification, GovernedTag

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/governance/tags")
certifications_router = APIRouter(prefix="/api/governance/certifications")


class TagCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = ""
    owner: str = ""
    description: str = ""
    links: dict[str, str] = {}
    properties: dict[str, Any] = {}


class TagUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner: str = ""
    description: str = ""
    links: dict[str, str] = {}
    properties: dict[str, Any] = {}


class ImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    yaml: str = ""


def _payload(tag: GovernedTag) -> dict[str, object]:
    return {
        "id": tag.id,
        "owner": tag.owner,
        "description": tag.description,
        "links": tag.links,
        "properties": tag.properties,
        "created_at": tag.created_at.isoformat(),
        "updated_at": tag.updated_at.isoformat(),
    }


@router.get("")
async def list_tags(conn: ConnDep, _: AdminApiDep) -> dict[str, object]:
    tags = await db_list_tags(conn)
    return {"tags": [_payload(t) for t in tags]}


@router.post("", status_code=201)
async def create_tag(request: Request, conn: ConnDep, body: TagCreate) -> dict[str, object]:
    user = await require_admin_api(request, conn)
    tag_id = normalize_tag_id(body.id)
    errors = validate_tag_fields(tag_id, body.links, body.properties)
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    try:
        tag = await db_create_tag(
            conn, tag_id, body.owner.strip(), body.description.strip(),
            body.links, dict(body.properties),
        )
    except TagIdTakenError:
        raise ApiError("tag_id_taken", status_code=409, tag_id=tag_id) from None
    log.info("governed_tag_created", tag_id=tag_id, author=user.email)
    return _payload(tag)


@router.get("/export")
async def export_tags(conn: ConnDep, _: AdminApiDep) -> dict[str, str]:
    tags = await db_list_tags(conn)
    return {"yaml": export_tags_yaml(tags)}


@router.post("/import")
async def import_tags(request: Request, conn: ConnDep, body: ImportBody) -> dict[str, int]:
    user = await require_admin_api(request, conn)
    try:
        parsed = parse_tags_yaml(body.yaml)
    except TagImportError as exc:
        raise ApiError("tag_import_invalid", status_code=400, detail=str(exc)) from None
    async with conn.transaction():
        for tag in parsed:
            await db_upsert_tag(conn, tag.id, tag.owner, tag.description, tag.links, tag.properties)
    log.info("governed_tags_imported", count=len(parsed), author=user.email)
    return {"imported": len(parsed)}


@router.put("/{tag_id:path}")
async def update_tag(
    request: Request, conn: ConnDep, tag_id: str, body: TagUpdate
) -> dict[str, object]:
    user = await require_admin_api(request, conn)
    normalized = normalize_tag_id(tag_id)
    errors = validate_tag_fields(normalized, body.links, body.properties)
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    try:
        tag = await db_update_tag(
            conn, normalized, body.owner.strip(), body.description.strip(),
            body.links, dict(body.properties),
        )
    except TagNotFoundError:
        raise ApiError("tag_not_found", status_code=404, tag_id=normalized) from None
    log.info("governed_tag_updated", tag_id=normalized, author=user.email)
    return _payload(tag)


@router.delete("/{tag_id:path}", status_code=204)
async def delete_tag(request: Request, conn: ConnDep, tag_id: str) -> None:
    user = await require_admin_api(request, conn)
    normalized = normalize_tag_id(tag_id)
    # Le refus « tag utilisé par des assets » (critère 6) arrivera avec les
    # assets (epic Curation) ; sans assets, aucun usage ne bloque encore.
    deleted = await db_delete_tag(conn, normalized)
    if not deleted:
        raise ApiError("tag_not_found", status_code=404, tag_id=normalized)
    log.info("governed_tag_deleted", tag_id=normalized, author=user.email)


class CertificationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    rank: int = 1
    description: str = ""
    criteria: str = ""
    tag: str = ""
    icon: str = ""
    restricted: bool = False


def _cert_payload(cert: Certification) -> dict[str, object]:
    return {
        "id": str(cert.id),
        "name": cert.name,
        "rank": cert.rank,
        "description": cert.description,
        "criteria": cert.criteria,
        "tag": cert.tag,
        "icon": cert.icon,
        "restricted": cert.restricted,
        "created_at": cert.created_at.isoformat(),
        "updated_at": cert.updated_at.isoformat(),
    }


@certifications_router.get("")
async def list_certifications(conn: ConnDep, _: AdminApiDep) -> dict[str, object]:
    certs = await db_list_certifications(conn)
    return {"certifications": [_cert_payload(c) for c in certs]}


@certifications_router.post("", status_code=201)
async def create_certification(
    request: Request, conn: ConnDep, body: CertificationBody
) -> dict[str, object]:
    user = await require_admin_api(request, conn)
    tag = normalize_tag_id(body.tag)
    errors = validate_certification(body.name, body.rank, tag)
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    try:
        cert = await db_create_certification(
            conn, body.name.strip(), body.rank, body.description.strip(),
            body.criteria, tag, body.icon.strip(), body.restricted,
        )
    except CertificationTagTakenError:
        raise ApiError("certification_tag_taken", status_code=409, tag=tag) from None
    log.info("certification_created", cert_id=str(cert.id), tag=tag, author=user.email)
    return _cert_payload(cert)


@certifications_router.post("/presets")
async def create_presets(request: Request, conn: ConnDep) -> dict[str, object]:
    user = await require_admin_api(request, conn)
    await db_create_certification_presets(conn, PRESETS)
    log.info("certification_presets_created", author=user.email)
    certs = await db_list_certifications(conn)
    return {"certifications": [_cert_payload(c) for c in certs]}


@certifications_router.put("/{cert_id}")
async def update_certification(
    request: Request, conn: ConnDep, cert_id: uuid.UUID, body: CertificationBody
) -> dict[str, object]:
    user = await require_admin_api(request, conn)
    tag = normalize_tag_id(body.tag)
    errors = validate_certification(body.name, body.rank, tag)
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    try:
        cert = await db_update_certification(
            conn, cert_id, body.name.strip(), body.rank, body.description.strip(),
            body.criteria, tag, body.icon.strip(), body.restricted,
        )
    except CertificationTagTakenError:
        raise ApiError("certification_tag_taken", status_code=409, tag=tag) from None
    except CertificationNotFoundError:
        raise ApiError("certification_not_found", status_code=404) from None
    log.info("certification_updated", cert_id=str(cert_id), author=user.email)
    return _cert_payload(cert)


@certifications_router.delete("/{cert_id}", status_code=204)
async def delete_certification(request: Request, conn: ConnDep, cert_id: uuid.UUID) -> None:
    user = await require_admin_api(request, conn)
    # Le refus « certification assignée à des assets » (critère 5) arrivera avec
    # les assets (epic Curation) ; sans assets, aucune assignation ne bloque.
    deleted = await db_delete_certification(conn, cert_id)
    if not deleted:
        raise ApiError("certification_not_found", status_code=404)
    log.info("certification_deleted", cert_id=str(cert_id), author=user.email)
