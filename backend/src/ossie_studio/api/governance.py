"""Tags gouvernés et certifications d'un espace, dans son dépôt (par branche).
Lectures : la branche vue par la personne ; écritures : son brouillon."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import GovernanceRead, GovernanceWrite
from ossie_studio.api.governance_common import CryptoDep, DraftWorkdirDep, gov_ctx
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.governance.certifications import PRESETS, validate_certification
from ossie_studio.governance.repo import (
    Cert,
    Tag,
    delete_cert,
    delete_tag,
    new_id,
    read_certs,
    read_tags,
    write_cert,
    write_tag,
)
from ossie_studio.governance.tags import normalize_tag_id, validate_tag_fields
from ossie_studio.governance.yaml_io import TagImportError, export_tags_yaml, parse_tags_yaml
from ossie_studio.slugs import is_valid_slug, slugify

log = structlog.get_logger(__name__)

_BASE = "/api/spaces/{space_id}/branches/{branch_id}/governance"
router = APIRouter(prefix=f"{_BASE}/tags")
certifications_router = APIRouter(prefix=f"{_BASE}/certifications")


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


def _payload(tag: Tag) -> dict[str, object]:
    return {
        "id": tag.id,
        "owner": tag.owner,
        "description": tag.description,
        "links": tag.links,
        "properties": tag.properties,
    }


@router.get("")
async def list_tags(
    conn: ConnDep,
    crypto: CryptoDep,
    _: GovernanceRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    return {"tags": [_payload(t) for t in await ctx.read(read_tags)]}


@router.post("", status_code=201)
async def create_tag(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: TagCreate,
) -> dict[str, object]:
    tag_id = normalize_tag_id(body.id)
    errors = validate_tag_fields(tag_id, body.links, body.properties)
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    tag = Tag(
        tag_id, body.owner.strip(), body.description.strip(), body.links, dict(body.properties)
    )

    def write(root: Path) -> None:
        if any(t.id == tag_id for t in read_tags(root)):
            raise ApiError("tag_id_taken", status_code=409, tag_id=tag_id)
        write_tag(root, tag)

    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, f"tag {tag_id}", write)
    log.info("governed_tag_created", tag_id=tag_id)
    return _payload(tag)


@router.get("/export")
async def export_tags(
    conn: ConnDep,
    crypto: CryptoDep,
    _: GovernanceRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, str]:
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    return {"yaml": export_tags_yaml(await ctx.read(read_tags))}


@router.post("/import")
async def import_tags(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: ImportBody,
) -> dict[str, int]:
    try:
        parsed = parse_tags_yaml(body.yaml)
    except TagImportError as exc:
        raise ApiError("tag_import_invalid", status_code=400, detail=str(exc)) from None

    def write(root: Path) -> None:
        for p in parsed:
            write_tag(root, Tag(p.id, p.owner, p.description, p.links, p.properties))

    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, f"import de {len(parsed)} tag(s)", write)
    return {"imported": len(parsed)}


@router.put("/{tag_id:path}")
async def update_tag(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    tag_id: str,
    body: TagUpdate,
) -> dict[str, object]:
    normalized = normalize_tag_id(tag_id)
    errors = validate_tag_fields(normalized, body.links, body.properties)
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    tag = Tag(
        normalized, body.owner.strip(), body.description.strip(), body.links, dict(body.properties)
    )

    def write(root: Path) -> None:
        if not any(t.id == normalized for t in read_tags(root)):
            raise ApiError("tag_not_found", status_code=404, tag_id=normalized)
        write_tag(root, tag)

    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, f"tag {normalized}", write)
    return _payload(tag)


@router.delete("/{tag_id:path}", status_code=204)
async def remove_tag(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    tag_id: str,
) -> None:
    normalized = normalize_tag_id(tag_id)
    if validate_tag_fields(normalized, {}, {}):
        raise ApiError("tag_not_found", status_code=404, tag_id=normalized)

    def write(root: Path) -> None:
        if not delete_tag(root, normalized):
            raise ApiError("tag_not_found", status_code=404, tag_id=normalized)

    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, f"supprime le tag {normalized}", write)


# --- Certifications ---------------------------------------------------------------


class CertificationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    # À la création seulement (déduit du nom par l'écran, modifiable) ; figé ensuite.
    slug: str = ""
    rank: int = 1
    description: str = ""
    criteria: str = ""
    tag: str = ""
    icon: str = ""
    restricted: bool = False


def _cert_payload(cert: Cert) -> dict[str, object]:
    return {
        "id": cert.id,
        "slug": cert.slug,
        "name": cert.name,
        "rank": cert.rank,
        "description": cert.description,
        "criteria": cert.criteria,
        "tag": cert.tag,
        "icon": cert.icon,
        "restricted": cert.restricted,
    }


def _cert_from(cert_id: str, body: CertificationBody) -> Cert:
    tag = normalize_tag_id(body.tag)
    errors = validate_certification(body.name, body.rank, tag)
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    return Cert(
        cert_id,
        body.name.strip(),
        body.rank,
        body.description.strip(),
        body.criteria,
        tag,
        body.icon.strip(),
        body.restricted,
    )


def _check_slug_free(root: Path, cert: Cert) -> None:
    if any(c.slug == cert.slug and c.id != cert.id for c in read_certs(root)):
        raise ApiError("certification_slug_taken", status_code=409, slug=cert.slug)


def _check_tag_free(root: Path, cert: Cert) -> None:
    if any(c.tag == cert.tag and c.id != cert.id for c in read_certs(root)):
        raise ApiError("certification_tag_taken", status_code=409, tag=cert.tag)


@certifications_router.get("")
async def list_certifications(
    conn: ConnDep,
    crypto: CryptoDep,
    _: GovernanceRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    return {"certifications": [_cert_payload(c) for c in await ctx.read(read_certs)]}


@certifications_router.post("", status_code=201)
async def create_certification(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: CertificationBody,
) -> dict[str, object]:
    cert = _cert_from(new_id(), body)
    given = body.slug.strip()
    if given and not is_valid_slug(given):
        raise ApiError("invalid_request", status_code=400, fields=["certification_slug_invalid"])

    def write(root: Path) -> None:
        if given:
            cert.slug = given
            _check_slug_free(root, cert)
        else:
            # Déduit du nom : suffixé s'il est pris (jamais d'erreur sur ce que
            # l'utilisateur n'a pas saisi).
            taken = {c.slug for c in read_certs(root)}
            base = slugify(cert.name, fallback="certification")
            cert.slug, counter = base, 2
            while cert.slug in taken:
                cert.slug, counter = f"{base}-{counter}", counter + 1
        _check_tag_free(root, cert)
        write_cert(root, cert)

    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, f"certification {cert.name}", write)
    return _cert_payload(cert)


@certifications_router.post("/presets")
async def create_presets(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    def write(root: Path) -> None:
        existing = read_certs(root)
        taken = {c.tag for c in existing}
        slugs = {c.slug for c in existing}
        for preset in PRESETS:
            if str(preset["tag"]) in taken or slugify(str(preset["name"])) in slugs:
                continue
            write_cert(
                root,
                Cert(
                    new_id(),
                    str(preset["name"]),
                    int(str(preset["rank"])),
                    str(preset.get("description", "")),
                    str(preset.get("criteria", "")),
                    str(preset["tag"]),
                    str(preset.get("icon", "")),
                    bool(preset.get("restricted", False)),
                    slugify(str(preset["name"])),
                ),
            )

    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, "certifications prédéfinies", write)
    return {"certifications": [_cert_payload(c) for c in await ctx.read(read_certs)]}


@certifications_router.put("/{cert_id}")
async def update_certification(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    cert_id: str,
    body: CertificationBody,
) -> dict[str, object]:
    cert = _cert_from(cert_id, body)

    def write(root: Path) -> None:
        current = next((c for c in read_certs(root) if c.id == cert_id), None)
        if current is None:
            raise ApiError("certification_not_found", status_code=404)
        cert.slug = current.slug  # figé à la création
        _check_tag_free(root, cert)
        write_cert(root, cert)

    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, f"certification {cert.name}", write)
    return _cert_payload(cert)


@certifications_router.delete("/{cert_id}", status_code=204)
async def remove_certification(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    cert_id: str,
) -> None:
    def write(root: Path) -> None:
        if not delete_cert(root, cert_id):
            raise ApiError("certification_not_found", status_code=404)

    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, "supprime une certification", write)
