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
from ossie_studio.governance import actions
from ossie_studio.governance.certifications import PRESETS
from ossie_studio.governance.repo import (
    Cert,
    Tag,
    new_id,
    read_certs,
    read_tags,
    write_cert,
    write_tag,
)
from ossie_studio.governance.tags import normalize_tag_id
from ossie_studio.governance.yaml_io import TagImportError, export_tags_yaml, parse_tags_yaml
from ossie_studio.slugs import slugify

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
    values = body.model_dump(exclude={"id"})
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    tag = await ctx.write(
        user.email,
        f"tag {normalize_tag_id(body.id)}",
        lambda root: actions.create_tag(root, body.id, values),
    )
    log.info("governed_tag_created", tag_id=tag.id)
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
    # L'écran remplace le tag : il donne tous les champs.
    changes = body.model_dump()
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    tag = await ctx.write(
        user.email,
        f"tag {normalize_tag_id(tag_id)}",
        lambda root: actions.update_tag(root, tag_id, changes),
    )
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
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(
        user.email,
        f"supprime le tag {normalize_tag_id(tag_id)}",
        lambda root: actions.delete_tag(root, tag_id),
    )


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
    values = body.model_dump(exclude={"slug"})
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    cert = await ctx.write(
        user.email,
        f"certification {body.name.strip()}",
        lambda root: actions.create_certification(root, values, body.slug),
    )
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
    # L'écran remplace la certification (tous les champs) ; le slug reste figé.
    changes = body.model_dump(exclude={"slug"})
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    cert = await ctx.write(
        user.email,
        f"certification {body.name.strip()}",
        lambda root: actions.update_certification(root, cert_id, changes),
    )
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
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(
        user.email,
        "supprime une certification",
        lambda root: actions.delete_certification(root, cert_id),
    )
