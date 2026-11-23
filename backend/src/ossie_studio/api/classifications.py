"""Schémas de classification d'un espace (et leurs niveaux), dans son dépôt."""

from __future__ import annotations

import uuid
from dataclasses import asdict

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import GovernanceRead, GovernanceWrite
from ossie_studio.api.governance_common import CryptoDep, DraftWorkdirDep, gov_ctx
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.governance import actions
from ossie_studio.governance.repo import Level, Scheme, new_id, read_schemes
from ossie_studio.governance.yaml_io import (
    SchemeImportError,
    export_scheme_yaml,
    parse_scheme_yaml,
)
from ossie_studio.sources.curation_assets import invalid_classifications

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/governance/classifications")


class LevelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    label: str = ""
    short_name: str = ""
    rank: int = 0
    description: str = ""
    criteria: str = ""
    color: str = ""
    icon: str = ""


class SchemeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = ""
    name: str = ""
    description: str = ""
    badge_prefix: str = ""
    multi_value: bool = False
    is_primary: bool = False
    levels: list[LevelBody] = []


class SchemeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    description: str = ""
    badge_prefix: str = ""
    multi_value: bool = False
    is_primary: bool = False
    display_order: int = 0


class ReorderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: list[str] = []


class SchemeImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    yaml: str = ""


def _level_payload(level: Level) -> dict[str, object]:
    return {
        "id": level.id,
        "name": level.name,
        "label": level.label,
        "short_name": level.short_name,
        "rank": level.rank,
        "description": level.description,
        "criteria": level.criteria,
        "color": level.color,
        "icon": level.icon,
        "position": level.position,
    }


def _scheme_payload(scheme: Scheme) -> dict[str, object]:
    return {
        "id": scheme.id,
        "name": scheme.name,
        "description": scheme.description,
        "badge_prefix": scheme.badge_prefix,
        "multi_value": scheme.multi_value,
        "is_primary": scheme.is_primary,
        "display_order": scheme.display_order,
        "levels": [_level_payload(lv) for lv in scheme.levels],
    }


@router.get("")
async def list_schemes(
    conn: ConnDep,
    crypto: CryptoDep,
    _: GovernanceRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    return {"schemes": [_scheme_payload(s) for s in await ctx.read(read_schemes)]}


@router.get("/invalid-values")
async def list_invalid_values(
    conn: ConnDep,
    crypto: CryptoDep,
    _: GovernanceRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    """Valeurs de classification portées par des assets et qui ne désignent
    aucun niveau existant (niveau supprimé ou renommé, schéma supprimé, saisie à
    la main) — ticket 5e8680d7, critère 6. Elles n'affichent aucun badge."""
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    found = await ctx.read(invalid_classifications)
    return {"values": [asdict(v) for v in found]}


@router.post("", status_code=201)
async def create_scheme(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: SchemeCreate,
) -> dict[str, object]:
    values = body.model_dump(exclude={"levels"})
    levels = [level.model_dump() for level in body.levels]
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    scheme = await ctx.write(
        user.email,
        f"schéma {body.id.strip() or body.name.strip()}",
        lambda root: actions.create_scheme(root, values, levels),
    )
    return _scheme_payload(scheme)


@router.post("/import", status_code=201)
async def import_scheme(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: SchemeImportBody,
) -> dict[str, object]:
    try:
        parsed = parse_scheme_yaml(body.yaml)
    except SchemeImportError as exc:
        raise ApiError("scheme_import_invalid", status_code=400, detail=str(exc)) from None
    scheme = Scheme(
        parsed.id,
        parsed.name,
        parsed.description,
        parsed.badge_prefix,
        parsed.multi_value,
        parsed.is_primary,
    )
    scheme.levels = [
        Level(
            new_id(),
            parsed.id,
            lv.name,
            lv.label,
            lv.short_name,
            lv.rank,
            lv.description,
            lv.criteria,
            lv.color,
            lv.icon,
            i,
        )
        for i, lv in enumerate(parsed.levels)
    ]
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(
        user.email,
        f"import du schéma {scheme.id}",
        lambda root: actions.insert_scheme(root, scheme),
    )
    return _scheme_payload(scheme)


@router.get("/{scheme_id}/export")
async def export_scheme(
    conn: ConnDep,
    crypto: CryptoDep,
    _: GovernanceRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    scheme_id: str,
) -> dict[str, str]:
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    scheme = await ctx.read(lambda root: actions.find_scheme(root, scheme_id))
    return {"yaml": export_scheme_yaml(scheme, scheme.levels)}


@router.put("/{scheme_id}")
async def update_scheme(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    scheme_id: str,
    body: SchemeUpdate,
) -> dict[str, object]:
    # L'écran remplace le schéma : il donne tous les champs.
    changes = body.model_dump()
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    scheme = await ctx.write(
        user.email,
        f"schéma {scheme_id}",
        lambda root: actions.update_scheme(root, scheme_id, changes),
    )
    return _scheme_payload(scheme)


@router.delete("/{scheme_id}", status_code=204)
async def remove_scheme(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    scheme_id: str,
) -> None:
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(
        user.email,
        f"supprime le schéma {scheme_id}",
        lambda root: actions.delete_scheme(root, scheme_id),
    )


@router.post("/{scheme_id}/levels", status_code=201)
async def add_level(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    scheme_id: str,
    body: LevelBody,
) -> dict[str, object]:
    values = body.model_dump()
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    level = await ctx.write(
        user.email,
        f"niveau du schéma {scheme_id}",
        lambda root: actions.add_level(root, scheme_id, values),
    )
    return _level_payload(level)


@router.put("/{scheme_id}/levels/{level_id}")
async def update_level(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    scheme_id: str,
    level_id: str,
    body: LevelBody,
) -> dict[str, object]:
    # L'écran remplace le niveau : il donne tous les champs.
    changes = body.model_dump()
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    level = await ctx.write(
        user.email,
        f"niveau du schéma {scheme_id}",
        lambda root: actions.update_level(root, scheme_id, level_id, changes),
    )
    return _level_payload(level)


@router.delete("/{scheme_id}/levels/{level_id}", status_code=204)
async def delete_level(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    scheme_id: str,
    level_id: str,
) -> None:
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(
        user.email,
        f"supprime un niveau du schéma {scheme_id}",
        lambda root: actions.delete_level(root, scheme_id, level_id),
    )


@router.put("/{scheme_id}/levels")
async def reorder_levels(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    scheme_id: str,
    body: ReorderBody,
) -> dict[str, bool]:
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(
        user.email,
        f"ordre des niveaux du schéma {scheme_id}",
        lambda root: actions.reorder_levels(root, scheme_id, body.order),
    )
    return {"ok": True}
