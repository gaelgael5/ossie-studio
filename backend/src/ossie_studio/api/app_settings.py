"""Page « Paramètres » : lire et modifier les paramètres généraux de l'application
(ticket docflow ae8fc4b6). Lecture : `settings:read` ; modification et retour à la
valeur par défaut : `settings:write`. Chaque changement est une ligne du journal
d'audit (page Historique)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ossie_studio.api.common import SettingsRead, SettingsWrite
from ossie_studio.app_settings import service
from ossie_studio.audit.service import UPDATED, audited
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError

router = APIRouter(prefix="/api/settings")

AUDIT_TYPE = "app_setting"


class SettingBody(BaseModel):
    value: Any


def _payload(found: service.SettingValue) -> dict[str, Any]:
    data = asdict(found)
    data["updated_at"] = found.updated_at.isoformat() if found.updated_at else None
    return data


def _api_error(exc: service.SettingError) -> ApiError:
    status = 404 if exc.code == "setting_not_found" else 400
    return ApiError(exc.code, status_code=status, **exc.detail)


@router.get("")
async def list_settings(request: Request, conn: ConnDep, _user: SettingsRead) -> dict[str, Any]:
    """Les paramètres : valeur en vigueur, valeur par défaut (configuration), bornes,
    et d'où vient la valeur (`page` ou `config`)."""
    found = await service.list_settings(conn, request.app.state.settings)
    return {"settings": [_payload(s) for s in found]}


@router.put("/{key}")
async def set_setting(
    request: Request, conn: ConnDep, user: SettingsWrite, key: str, body: SettingBody
) -> dict[str, Any]:
    """Pose la valeur d'un paramètre ; elle prime sur la configuration et vaut dès la
    requête suivante. Valeur hors bornes, nulle ou non entière : refus nommé."""
    settings = request.app.state.settings
    try:
        found = service.definition(key)
        value = service.check_value(found, body.value)
        before = await service.current(conn, settings, key)
    except service.SettingError as exc:
        raise _api_error(exc) from None
    async with audited(conn, user, UPDATED, AUDIT_TYPE, key, label=key) as entry:
        entry.detail = {"bytes": value, "previous": before.value}
        entry.skip = before.source == "page" and before.value == value
        await service.db_set(conn, key, value, user.id)
    return _payload(await service.current(conn, settings, key))


@router.delete("/{key}")
async def reset_setting(
    request: Request, conn: ConnDep, user: SettingsWrite, key: str
) -> dict[str, Any]:
    """Revient à la valeur par défaut (celle de la configuration)."""
    settings = request.app.state.settings
    try:
        before = await service.current(conn, settings, key)
    except service.SettingError as exc:
        raise _api_error(exc) from None
    async with audited(conn, user, UPDATED, AUDIT_TYPE, key, label=key) as entry:
        entry.detail = {"reset": True, "previous": before.value, "bytes": before.default}
        entry.skip = not await service.db_reset(conn, key)
    return _payload(await service.current(conn, settings, key))
