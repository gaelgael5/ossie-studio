from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import ParsingManage, ParsingRead, SourcesRead
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.parsers.base import Plugin
from ossie_studio.parsers.params_service import db_get_plugin_params, db_set_plugin_param
from ossie_studio.parsers.registry import all_plugins, code_plugins, plugin_by_id

router = APIRouter(prefix="/api/parsing")


class ParamValueBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = ""


_CODE_IDS = frozenset(p.id for p in code_plugins())


def _plugin_payload(plugin: Plugin, stored: dict[str, str]) -> dict[str, object]:
    return {
        "id": plugin.id,
        "label": plugin.label,
        "version": plugin.version,
        "cible": plugin.cible,
        # Plugin de code (choisissable sur une source Git) vs lié à un type de source.
        "code": plugin.id in _CODE_IDS,
        "globs": plugin.globs(),
        "parametres": [
            {
                "slug": param.slug,
                "label": param.label,
                "doc": param.doc,
                "type": param.type,
                "default": param.default,
                "value": stored.get(param.slug, param.default),
            }
            for param in plugin.parameters()
        ],
    }


@router.get("/code-plugins")
async def list_code_plugins(_: SourcesRead) -> dict[str, object]:
    """Les parseurs de code choisissables sur une source Git (pour l'écran de
    déclaration). Accessible dès qu'on peut voir les sources."""
    return {
        "plugins": [{"id": p.id, "label": p.label, "globs": p.globs()} for p in code_plugins()]
    }


@router.get("/plugins")
async def list_parsing_plugins(conn: ConnDep, _: ParsingRead) -> dict[str, object]:
    """Les plugins installés, leurs motifs et leurs paramètres (valeur courante)."""
    values = await db_get_plugin_params(conn)
    return {"plugins": [_plugin_payload(p, values.get(p.id, {})) for p in all_plugins()]}


@router.put("/plugins/{plugin_id}/parameters/{slug}")
async def set_parsing_plugin_param(
    conn: ConnDep,
    _: ParsingManage,
    plugin_id: str,
    slug: str,
    body: ParamValueBody,
) -> dict[str, object]:
    plugin = plugin_by_id(plugin_id)
    if plugin is None:
        raise ApiError("plugin_not_found", status_code=404)
    param = next((p for p in plugin.parameters() if p.slug == slug), None)
    if param is None:
        raise ApiError("plugin_param_not_found", status_code=404)
    value = body.value.strip()
    if param.type == "int":
        try:
            int(value)
        except ValueError:
            raise ApiError("invalid_request", status_code=400, fields=["value_int"]) from None
    await db_set_plugin_param(conn, plugin_id=plugin_id, slug=slug, value=value)
    return {"plugin_id": plugin_id, "slug": slug, "value": value}
