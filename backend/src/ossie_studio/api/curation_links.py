"""Lier une propriété d'asset à une propriété de référentiel (ticket 8269c642).

Mêmes droits et même journal que « Enrichir un asset » (`curation:write`,
écriture dans le brouillon de la personne, ligne `curation_*` dans les
journaux de l'application) ; la branche racine du dépôt est en lecture seule
(règle des espaces Git). Le lien et ses règles de groupe : voir
`sources/referentiel_links.py`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.branches import space_credentials
from ossie_studio.api.common import CurationWrite
from ossie_studio.api.curation import WorkdirDep, _branch_slug, _check, _target
from ossie_studio.api.governance_common import CryptoDep
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.sources.asset_files import load_asset, write_asset
from ossie_studio.sources.curation import AssetNotRetainedError
from ossie_studio.sources.paths import safe_assets_path
from ossie_studio.sources.referentiel_links import (
    LinkRefusedError,
    link_group,
    read_targets,
    unlink,
)
from ossie_studio.spaces.workdir import WorkdirError

log = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api/spaces/{space_id}/branches/{branch_id}/sources/{slug}/curation/referentiel-link"
)


class LinkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = ""
    referentiel: str = ""
    # {propriété de l'asset : propriété du référentiel} — tout le groupe d'une clé
    # composée en une fois.
    links: dict[str, str] = {}
    # « Modifier » : une propriété dont le groupe actuel est remplacé.
    replace: str | None = None


Change = Callable[[Path, dict[str, Any]], list[str]]


async def _write(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationWrite,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    path: str,
    intent: str,
    change: Change,
) -> tuple[str | None, list[str]]:
    _check(slug, path)
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    touched: list[str] = []

    def write(root: Path) -> None:
        asset_file = safe_assets_path(root, slug) / path
        if not asset_file.is_file():
            raise AssetNotRetainedError(path)
        asset = load_asset(asset_file)
        touched[:] = change(root, asset)
        write_asset(asset_file, asset)

    try:
        # Pas de refus sur la branche racine (bug e7deb2b9) : l'écriture va dans
        # le brouillon de la personne, rien n'atteint la racine avant publication.
        revision = await _target(workdir, space, branch_slug, username, token, user).write(
            f"curation {slug} : {intent} {path}", write
        )
    except LinkRefusedError as exc:
        raise ApiError(exc.code, status_code=exc.status, **exc.detail) from None
    except AssetNotRetainedError as exc:
        raise ApiError("asset_not_retained", status_code=404, path=exc.path) from None
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    return revision, touched


@router.put("")
async def link_property(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationWrite,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    body: LinkBody,
) -> dict[str, object]:
    """Lier une propriété (et, pour une clé composée, les autres propriétés de
    la clé) à un référentiel de la branche, en une fois. Un groupe incomplet
    est refusé (`referentiel_link_incomplete`)."""

    def change(root: Path, asset: dict[str, Any]) -> list[str]:
        target = read_targets(root).get(body.referentiel)
        if target is None:
            raise LinkRefusedError("referentiel_not_found", 404, name=body.referentiel)
        return link_group(asset, target, body.links, body.replace)

    revision, fields = await _write(
        conn, crypto, workdir, user, space_id, branch_id, slug, body.path,
        f"lier à {body.referentiel}", change,
    )  # fmt: skip
    log.info(
        "curation_referentiel_link",
        author=user.email,
        slug=slug,
        path=body.path,
        referentiel=body.referentiel,
        fields=fields,
    )
    return {"ok": True, "commit": revision, "fields": fields}


@router.delete("")
async def unlink_property(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationWrite,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    path: str,
    field: str,
) -> dict[str, object]:
    """Délier une propriété : tout son groupe est délié avec elle."""

    def change(_root: Path, asset: dict[str, Any]) -> list[str]:
        return unlink(asset, field)

    revision, fields = await _write(
        conn, crypto, workdir, user, space_id, branch_id, slug, path,
        f"délier {field} de", change,
    )  # fmt: skip
    log.info("curation_referentiel_unlink", author=user.email, slug=slug, path=path,
             fields=fields)  # fmt: skip
    return {"ok": True, "commit": revision, "fields": fields}
