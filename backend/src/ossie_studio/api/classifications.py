"""Schémas de classification d'un espace (et leurs niveaux), dans son dépôt."""

from __future__ import annotations

import uuid
from pathlib import Path

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import GovernanceRead, GovernanceWrite
from ossie_studio.api.governance_common import CryptoDep, DraftWorkdirDep, gov_ctx
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.governance.classifications import (
    derive_scheme_id,
    validate_level,
    validate_scheme,
)
from ossie_studio.governance.repo import (
    Level,
    Scheme,
    delete_scheme,
    new_id,
    read_schemes,
    write_scheme,
)
from ossie_studio.governance.yaml_io import (
    SchemeImportError,
    export_scheme_yaml,
    parse_scheme_yaml,
)

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


def _level(scheme_id: str, body: LevelBody, level_id: str, position: int) -> Level:
    return Level(
        level_id,
        scheme_id,
        body.name.strip(),
        body.label.strip(),
        body.short_name.strip(),
        body.rank,
        body.description,
        body.criteria,
        body.color,
        body.icon,
        position,
    )


def _check_level_free(scheme: Scheme, level: Level) -> None:
    """Unicité du nom et du nom court dans le schéma (autrefois contrainte de base)."""
    for other in scheme.levels:
        if other.id == level.id:
            continue
        if other.name == level.name:
            raise ApiError("level_name_taken", status_code=409)
        if level.short_name and other.short_name == level.short_name:
            raise ApiError("level_short_name_taken", status_code=409)


def _find(root: Path, scheme_id: str) -> Scheme:
    scheme = next((s for s in read_schemes(root) if s.id == scheme_id), None)
    if scheme is None:
        raise ApiError("scheme_not_found", status_code=404)
    return scheme


def _new_scheme(root: Path, scheme: Scheme) -> None:
    existing = read_schemes(root)
    if any(s.id == scheme.id for s in existing):
        raise ApiError("scheme_id_taken", status_code=409, scheme_id=scheme.id)
    scheme.display_order = max((s.display_order for s in existing), default=-1) + 1
    for level in scheme.levels:
        _check_level_free(scheme, level)
    write_scheme(root, scheme)


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
    errors = validate_scheme(body.name, body.multi_value, body.is_primary)
    for level in body.levels:
        errors.extend(validate_level(level.name, level.short_name))
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    scheme_id = derive_scheme_id(body.id) if body.id.strip() else derive_scheme_id(body.name)
    scheme = Scheme(
        scheme_id,
        body.name.strip(),
        body.description.strip(),
        body.badge_prefix.strip(),
        body.multi_value,
        body.is_primary,
    )
    scheme.levels = [_level(scheme_id, lv, new_id(), i) for i, lv in enumerate(body.levels)]
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, f"schéma {scheme_id}", lambda root: _new_scheme(root, scheme))
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
        user.email, f"import du schéma {scheme.id}", lambda root: _new_scheme(root, scheme)
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
    scheme = await ctx.read(lambda root: _find(root, scheme_id))
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
    errors = validate_scheme(body.name, body.multi_value, body.is_primary)
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    result: dict[str, Scheme] = {}

    def write(root: Path) -> None:
        scheme = _find(root, scheme_id)
        scheme.name, scheme.description = body.name.strip(), body.description.strip()
        scheme.badge_prefix, scheme.multi_value = body.badge_prefix.strip(), body.multi_value
        scheme.is_primary, scheme.display_order = body.is_primary, body.display_order
        write_scheme(root, scheme)
        result["scheme"] = scheme

    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, f"schéma {scheme_id}", write)
    return _scheme_payload(result["scheme"])


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
    def write(root: Path) -> None:
        if not delete_scheme(root, scheme_id):
            raise ApiError("scheme_not_found", status_code=404)

    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, f"supprime le schéma {scheme_id}", write)


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
    errors = validate_level(body.name, body.short_name)
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    result: dict[str, Level] = {}

    def write(root: Path) -> None:
        scheme = _find(root, scheme_id)
        level = _level(scheme_id, body, new_id(), len(scheme.levels))
        _check_level_free(scheme, level)
        scheme.levels.append(level)
        write_scheme(root, scheme)
        result["level"] = level

    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, f"niveau du schéma {scheme_id}", write)
    return _level_payload(result["level"])


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
    errors = validate_level(body.name, body.short_name)
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    result: dict[str, Level] = {}

    def write(root: Path) -> None:
        scheme = _find(root, scheme_id)
        index = next((i for i, lv in enumerate(scheme.levels) if lv.id == level_id), None)
        if index is None:
            raise ApiError("level_not_found", status_code=404)
        level = _level(scheme_id, body, level_id, index)
        _check_level_free(scheme, level)
        scheme.levels[index] = level
        write_scheme(root, scheme)
        result["level"] = level

    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, f"niveau du schéma {scheme_id}", write)
    return _level_payload(result["level"])


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
    def write(root: Path) -> None:
        scheme = _find(root, scheme_id)
        kept = [lv for lv in scheme.levels if lv.id != level_id]
        if len(kept) == len(scheme.levels):
            raise ApiError("level_not_found", status_code=404)
        scheme.levels = kept
        write_scheme(root, scheme)

    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, f"supprime un niveau du schéma {scheme_id}", write)


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
    def write(root: Path) -> None:
        scheme = _find(root, scheme_id)
        by_id = {lv.id: lv for lv in scheme.levels}
        if set(body.order) != set(by_id):
            raise ApiError("invalid_request", status_code=400)
        scheme.levels = [by_id[i] for i in body.order]
        write_scheme(root, scheme)

    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, f"ordre des niveaux du schéma {scheme_id}", write)
    return {"ok": True}
