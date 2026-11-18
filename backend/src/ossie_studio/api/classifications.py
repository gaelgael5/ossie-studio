from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import GovernanceRead, require_capability
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.governance.classifications import (
    derive_scheme_id,
    validate_level,
    validate_scheme,
)
from ossie_studio.governance.classifications_service import (
    LevelNameTakenError,
    LevelNotFoundError,
    SchemeIdTakenError,
    SchemeNotFoundError,
    db_add_level,
    db_create_scheme,
    db_delete_level,
    db_delete_scheme,
    db_get_scheme,
    db_list_schemes_with_levels,
    db_next_scheme_order,
    db_reorder_levels,
    db_update_level,
    db_update_scheme,
)
from ossie_studio.governance.yaml_io import (
    SchemeImportError,
    export_scheme_yaml,
    parse_scheme_yaml,
)
from ossie_studio.models import ClassificationLevel, ClassificationScheme, User

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/governance/classifications")


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


def _level_payload(level: ClassificationLevel) -> dict[str, object]:
    return {
        "id": str(level.id),
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


def _scheme_payload(
    scheme: ClassificationScheme, levels: list[ClassificationLevel]
) -> dict[str, object]:
    return {
        "id": scheme.id,
        "name": scheme.name,
        "description": scheme.description,
        "badge_prefix": scheme.badge_prefix,
        "multi_value": scheme.multi_value,
        "is_primary": scheme.is_primary,
        "display_order": scheme.display_order,
        "levels": [_level_payload(level) for level in levels],
    }


def _level_error(exc: LevelNameTakenError) -> ApiError:
    return ApiError(exc.code, status_code=409)


async def _require_scheme(conn: ConnDep, scheme_id: str) -> ClassificationScheme:
    scheme = await db_get_scheme(conn, scheme_id)
    if scheme is None:
        raise ApiError("scheme_not_found", status_code=404)
    return scheme


@router.get("")
async def list_schemes(conn: ConnDep, _: GovernanceRead) -> dict[str, object]:
    rows = await db_list_schemes_with_levels(conn)
    return {"schemes": [_scheme_payload(scheme, levels) for scheme, levels in rows]}


async def _create_scheme_with_levels(
    conn: ConnDep,
    user: User,
    *,
    scheme_id: str,
    name: str,
    description: str,
    badge_prefix: str,
    multi_value: bool,
    is_primary: bool,
    levels: list[LevelBody],
) -> dict[str, object]:
    display_order = await db_next_scheme_order(conn)
    try:
        async with conn.transaction():
            scheme = await db_create_scheme(
                conn, scheme_id, name, description, badge_prefix, multi_value,
                is_primary, display_order,
            )
            created: list[ClassificationLevel] = []
            for position, level in enumerate(levels):
                created.append(
                    await db_add_level(
                        conn, scheme_id, level.name, level.label, level.short_name, level.rank,
                        level.description, level.criteria, level.color, level.icon, position,
                    )
                )
    except SchemeIdTakenError:
        raise ApiError("scheme_id_taken", status_code=409, scheme_id=scheme_id) from None
    except LevelNameTakenError as exc:
        raise _level_error(exc) from None
    log.info("classification_scheme_created", scheme_id=scheme_id, author=user.email)
    return _scheme_payload(scheme, created)


@router.post("", status_code=201)
async def create_scheme(request: Request, conn: ConnDep, body: SchemeCreate) -> dict[str, object]:
    user = await require_capability("governance:write")(request, conn)
    errors = validate_scheme(body.name, body.multi_value, body.is_primary)
    for level in body.levels:
        errors.extend(validate_level(level.name, level.short_name))
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    scheme_id = derive_scheme_id(body.id) if body.id.strip() else derive_scheme_id(body.name)
    return await _create_scheme_with_levels(
        conn, user,
        scheme_id=scheme_id, name=body.name.strip(), description=body.description.strip(),
        badge_prefix=body.badge_prefix.strip(), multi_value=body.multi_value,
        is_primary=body.is_primary, levels=body.levels,
    )


@router.post("/import", status_code=201)
async def import_scheme(
    request: Request, conn: ConnDep, body: SchemeImportBody
) -> dict[str, object]:
    user = await require_capability("governance:write")(request, conn)
    try:
        parsed = parse_scheme_yaml(body.yaml)
    except SchemeImportError as exc:
        raise ApiError("scheme_import_invalid", status_code=400, detail=str(exc)) from None
    levels = [
        LevelBody(
            name=lv.name, label=lv.label, short_name=lv.short_name, rank=lv.rank,
            description=lv.description, criteria=lv.criteria, color=lv.color, icon=lv.icon,
        )
        for lv in parsed.levels
    ]
    return await _create_scheme_with_levels(
        conn, user,
        scheme_id=parsed.id, name=parsed.name, description=parsed.description,
        badge_prefix=parsed.badge_prefix, multi_value=parsed.multi_value,
        is_primary=parsed.is_primary, levels=levels,
    )


@router.get("/{scheme_id}/export")
async def export_scheme(conn: ConnDep, _: GovernanceRead, scheme_id: str) -> dict[str, str]:
    scheme = await _require_scheme(conn, scheme_id)
    rows = await db_list_schemes_with_levels(conn)
    levels = next((lv for s, lv in rows if s.id == scheme.id), [])
    return {"yaml": export_scheme_yaml(scheme, levels)}


@router.put("/{scheme_id}")
async def update_scheme(
    request: Request, conn: ConnDep, scheme_id: str, body: SchemeUpdate
) -> dict[str, object]:
    user = await require_capability("governance:write")(request, conn)
    errors = validate_scheme(body.name, body.multi_value, body.is_primary)
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    try:
        async with conn.transaction():
            scheme = await db_update_scheme(
                conn, scheme_id, body.name.strip(), body.description.strip(),
                body.badge_prefix.strip(), body.multi_value, body.is_primary, body.display_order,
            )
    except SchemeNotFoundError:
        raise ApiError("scheme_not_found", status_code=404) from None
    log.info("classification_scheme_updated", scheme_id=scheme_id, author=user.email)
    rows = await db_list_schemes_with_levels(conn)
    levels = next((lv for s, lv in rows if s.id == scheme.id), [])
    return _scheme_payload(scheme, levels)


@router.delete("/{scheme_id}", status_code=204)
async def delete_scheme(request: Request, conn: ConnDep, scheme_id: str) -> None:
    user = await require_capability("governance:write")(request, conn)
    # Le refus/invalidation sur les assets utilisant le schéma (critère 8)
    # arrivera avec les assets (epic Curation).
    deleted = await db_delete_scheme(conn, scheme_id)
    if not deleted:
        raise ApiError("scheme_not_found", status_code=404)
    log.info("classification_scheme_deleted", scheme_id=scheme_id, author=user.email)


@router.post("/{scheme_id}/levels", status_code=201)
async def add_level(
    request: Request, conn: ConnDep, scheme_id: str, body: LevelBody
) -> dict[str, object]:
    user = await require_capability("governance:write")(request, conn)
    await _require_scheme(conn, scheme_id)
    errors = validate_level(body.name, body.short_name)
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    position = await _next_level_position(conn, scheme_id)
    try:
        level = await db_add_level(
            conn, scheme_id, body.name.strip(), body.label.strip(), body.short_name.strip(),
            body.rank, body.description, body.criteria, body.color, body.icon, position,
        )
    except LevelNameTakenError as exc:
        raise _level_error(exc) from None
    log.info("classification_level_added", scheme_id=scheme_id, author=user.email)
    return _level_payload(level)


@router.put("/{scheme_id}/levels/{level_id}")
async def update_level(
    request: Request, conn: ConnDep, scheme_id: str, level_id: uuid.UUID, body: LevelBody
) -> dict[str, object]:
    user = await require_capability("governance:write")(request, conn)
    errors = validate_level(body.name, body.short_name)
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    # NB : renommer un niveau utilisé réécrira les assets (critère 7) quand les
    # assets existeront ; pour l'instant seul le vocabulaire est mis à jour.
    try:
        level = await db_update_level(
            conn, level_id, body.name.strip(), body.label.strip(), body.short_name.strip(),
            body.rank, body.description, body.criteria, body.color, body.icon,
        )
    except LevelNotFoundError:
        raise ApiError("level_not_found", status_code=404) from None
    except LevelNameTakenError as exc:
        raise _level_error(exc) from None
    log.info("classification_level_updated", scheme_id=scheme_id, author=user.email)
    return _level_payload(level)


@router.delete("/{scheme_id}/levels/{level_id}", status_code=204)
async def delete_level(
    request: Request, conn: ConnDep, scheme_id: str, level_id: uuid.UUID
) -> None:
    user = await require_capability("governance:write")(request, conn)
    deleted = await db_delete_level(conn, level_id)
    if not deleted:
        raise ApiError("level_not_found", status_code=404)
    log.info("classification_level_deleted", scheme_id=scheme_id, author=user.email)


@router.put("/{scheme_id}/levels")
async def reorder_levels(
    request: Request, conn: ConnDep, scheme_id: str, body: ReorderBody
) -> dict[str, bool]:
    user = await require_capability("governance:write")(request, conn)
    await _require_scheme(conn, scheme_id)
    try:
        ordered = [uuid.UUID(value) for value in body.order]
    except ValueError:
        raise ApiError("invalid_request", status_code=400) from None
    await db_reorder_levels(conn, scheme_id, ordered)
    log.info("classification_levels_reordered", scheme_id=scheme_id, author=user.email)
    return {"ok": True}


async def _next_level_position(conn: ConnDep, scheme_id: str) -> int:
    value: int | None = await conn.fetchval(
        "SELECT max(position) + 1 FROM classification_levels WHERE scheme_id = $1", scheme_id
    )
    return value or 0
