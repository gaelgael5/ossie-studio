"""Plugins utilisés par un espace, sur la branche regardée (ticket a30b4bd8).

Cadrage Gael (24/11/2026) : plugins natifs seulement (l'import n'est pas
cadré) ; onglet « Plugins » de l'espace ; branche regardée, brouillon compris ;
un plugin cité par une source mais absent de l'installation est ignoré.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from ossie_studio.api.branches import space_credentials
from ossie_studio.api.common import SourcesRead
from ossie_studio.api.sources import CryptoDep, WorkdirDep, _branch_slug, _workdir_http_error
from ossie_studio.deps import ConnDep
from ossie_studio.parsers.registry import ORIGIN_NATIVE, all_plugins, plugin_usage
from ossie_studio.sources.service import list_sources
from ossie_studio.spaces.workdir import WorkdirError

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/plugins")


def _parsers(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    return [str(v) for v in value if isinstance(v, str) and v]


@router.get("")
async def list_space_plugins(
    conn: ConnDep,
    crypto: CryptoDep,
    _: SourcesRead,
    workdir: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    """Plugins installés utilisés par les sources de la branche regardée (et par
    lesquelles), puis ceux que l'espace n'utilise pas."""
    slug = await _branch_slug(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        entries = await list_sources(workdir, space, slug, username, token)
    except WorkdirError as exc:
        raise _workdir_http_error(exc) from None
    manifests = [e.manifest for e in entries if e.manifest is not None]
    labels = {m.slug: m.label or m.slug for m in manifests}
    usage = plugin_usage(
        [(m.slug, m.label, m.type, _parsers(m.parameters.get("parsers"))) for m in manifests]
    )
    used: list[dict[str, object]] = []
    unused: list[dict[str, object]] = []
    for plugin in all_plugins():
        item: dict[str, object] = {
            "id": plugin.id,
            "label": plugin.label,
            "version": plugin.version,
            "origin": ORIGIN_NATIVE,
        }
        sources = usage.get(plugin.id, [])
        if sources:
            used.append(
                {**item, "sources": [{"slug": s, "label": labels.get(s, s)} for s in sources]}
            )
        else:
            unused.append(item)
    return {"sources": len(manifests), "used": used, "unused": unused}
