from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.branches import space_credentials
from ossie_studio.api.common import CurrentUser, SpacesCreate, SpacesRead
from ossie_studio.authz.catalog import SPACE_SCOPED
from ossie_studio.connections.service import db_get_connection, resolve_connection_token
from ossie_studio.crypto import CryptoService
from ossie_studio.deps import (
    ConnDep,
    GitProbe,
    get_crypto,
    get_current_user,
    get_git_probe,
    get_workdir_manager,
)
from ossie_studio.errors import ApiError
from ossie_studio.forges.registry import get_strategy
from ossie_studio.models import GitSpace
from ossie_studio.profiles.service import load_capabilities, load_capabilities_by_space
from ossie_studio.slugs import is_valid_slug, slugify
from ossie_studio.spaces.branch_service import db_get_viewed_branch, db_list_branches
from ossie_studio.spaces.forms import normalize_repo_url, validate_space
from ossie_studio.spaces.git_probe import ProbeResult
from ossie_studio.spaces.railmap import RailMap
from ossie_studio.spaces.service import (
    SpaceNameTakenError,
    SpaceRepoTakenError,
    SpaceSlugTakenError,
    db_create_space,
    db_delete_space,
    db_get_space,
    db_list_spaces,
    db_update_space_probe,
)
from ossie_studio.spaces.workdir import WorkdirError, WorkdirManager

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/spaces")

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]
GitProbeDep = Annotated[GitProbe, Depends(get_git_probe)]
WorkdirDep = Annotated[WorkdirManager, Depends(get_workdir_manager)]
# Fenêtre maximale d'un appel au diagramme (bornée côté serveur de toute façon).
_RAILMAP_MAX = 5000


class SpaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    # Optionnel : l'écran le déduit du nom et le laisse corriger ; absent, le
    # serveur le déduit lui-même.
    slug: str = ""
    connection_id: str = ""
    repo_url: str = ""


def _payload(space: GitSpace) -> dict[str, object]:
    return {
        "id": str(space.id),
        "name": space.name,
        "slug": space.slug,
        "connection_id": str(space.connection_id),
        "repo_url": space.repo_url,
        "last_probe_status": space.last_probe_status,
        "last_probe_at": space.last_probe_at.isoformat() if space.last_probe_at else None,
    }


def _probe_detail(result: ProbeResult) -> dict[str, object]:
    return {
        "ok": result.ok,
        "verify_branch": result.verify_branch,
        "verify_tag": result.verify_tag,
        "posed_main": result.posed_main,
        "has_sources": result.has_sources,
        "found_branches": result.found_branches,
        "found_tags": result.found_tags,
    }


async def _run_probe(
    conn: ConnDep,
    crypto: CryptoService,
    git_probe: GitProbe,
    connection_id: uuid.UUID,
    repo_url: str,
) -> ProbeResult:
    connection = await db_get_connection(conn, connection_id)
    if connection is None:
        raise ApiError("invalid_request", status_code=400, fields=["connection_required"])
    token = (await resolve_connection_token(conn, crypto, connection)).reveal()
    username = get_strategy(connection.forge).git_username()
    return await git_probe(repo_url, username=username, token=token)


@router.get("")
async def list_spaces(conn: ConnDep, user: CurrentUser) -> dict[str, object]:
    # `spaces:read` du profil de base → tous les espaces ; sinon, seulement ceux
    # où une équipe de la personne est rattachée avec ce droit (droits par espace).
    spaces = await db_list_spaces(conn)
    if "spaces:read" not in await load_capabilities(conn, user):
        by_space = await load_capabilities_by_space(conn, user.id)
        spaces = [s for s in spaces if "spaces:read" in by_space.get(s.id, set())]
    return {"spaces": [_payload(s) for s in spaces]}


@router.get("/{space_id}/me")
async def my_space_capabilities(
    conn: ConnDep, user: CurrentUser, space_id: uuid.UUID
) -> dict[str, object]:
    """Capacités de la personne sur cet espace (profil de base + équipes
    rattachées) : l'écran de l'espace s'en sert pour montrer ses gestes."""
    if await db_get_space(conn, space_id) is None:
        raise ApiError("space_not_found", status_code=404)
    caps = await load_capabilities(conn, user, space_id)
    return {"capabilities": sorted(caps & SPACE_SCOPED)}


@router.post("", status_code=201)
async def create_space(
    conn: ConnDep, crypto: CryptoDep, _: SpacesCreate, git_probe: GitProbeDep, body: SpaceCreate
) -> dict[str, object]:
    errors = validate_space(body.name, body.connection_id, body.repo_url)
    slug = body.slug.strip() or slugify(body.name, fallback="espace")
    if body.slug.strip() and not is_valid_slug(slug):
        errors.append("space_slug_invalid")
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    # Nom puis slug déjà pris : refusés AVANT l'épreuve des droits (pas
    # d'écriture inutile sur la forge).
    if await conn.fetchval("SELECT 1 FROM git_spaces WHERE name = $1", body.name.strip()):
        raise ApiError("space_name_taken", status_code=409)
    if await conn.fetchval("SELECT 1 FROM git_spaces WHERE slug = $1", slug):
        raise ApiError("space_slug_taken", status_code=409, slug=slug)
    connection_id = uuid.UUID(body.connection_id)
    repo_url = normalize_repo_url(body.repo_url)

    result = await _run_probe(conn, crypto, git_probe, connection_id, repo_url)
    if not result.ok:
        # Rien n'est enregistré ; le refus nomme le droit et sa cause.
        raise ApiError(
            "space_probe_failed",
            status_code=400,
            right=result.failed_right,
            cause=result.cause,
            residuals=result.residuals,
            verify_branch=result.verify_branch,
            verify_tag=result.verify_tag,
        )
    try:
        space = await db_create_space(
            conn,
            name=body.name.strip(),
            connection_id=connection_id,
            repo_url=repo_url,
            probe_status="valid",
            slug=slug,
        )
    except SpaceSlugTakenError:
        raise ApiError("space_slug_taken", status_code=409, slug=slug) from None
    except SpaceNameTakenError:
        raise ApiError("space_name_taken", status_code=409) from None
    except SpaceRepoTakenError as exc:
        raise ApiError("space_repo_taken", status_code=409, space=exc.existing_name) from None
    return {**_payload(space), "probe": _probe_detail(result)}


@router.post("/{space_id}/probe")
async def reprobe_space(
    conn: ConnDep, crypto: CryptoDep, _: SpacesCreate, git_probe: GitProbeDep, space_id: uuid.UUID
) -> dict[str, object]:
    space = await db_get_space(conn, space_id)
    if space is None:
        raise ApiError("space_not_found", status_code=404)
    result = await _run_probe(conn, crypto, git_probe, space.connection_id, space.repo_url)
    await db_update_space_probe(conn, space_id, "valid" if result.ok else "error")
    detail = _probe_detail(result)
    if not result.ok:
        detail["failed_right"] = result.failed_right
        detail["cause"] = result.cause
        detail["residuals"] = result.residuals
    return detail


@router.delete("/{space_id}", status_code=204)
async def delete_space(conn: ConnDep, _: SpacesCreate, space_id: uuid.UUID) -> None:
    # Supprimer un espace ne touche pas au dépôt.
    deleted = await db_delete_space(conn, space_id)
    if not deleted:
        raise ApiError("space_not_found", status_code=404)


def _railmap_payload(rm: RailMap) -> dict[str, object]:
    return {
        "lines": [
            {
                "ref": ln.ref,
                "name": ln.name,
                "slug": ln.slug,
                "kind": ln.kind,
                "lane": ln.lane,
                "shape": ln.shape,
            }
            for ln in rm.lines
        ],
        "nodes": [
            {
                "sha": n.sha,
                "short": n.short,
                "lane": n.lane,
                "row": n.row,
                "kind": n.kind,
                "subject": n.subject,
                "author": n.author,
                "date": n.date,
                "is_switch": n.is_switch,
                "is_merge": n.is_merge,
                "number": n.number,
                "label": n.label,
                "version_author": n.version_author,
            }
            for n in rm.nodes
        ],
        "edges": [
            {
                "child": e.child,
                "parent": e.parent,
                "from_lane": e.from_lane,
                "to_lane": e.to_lane,
                "from_row": e.from_row,
                "to_row": e.to_row,
                "kind": e.kind,
            }
            for e in rm.edges
        ],
        "total": rm.total,
        "offset": rm.offset,
        "limit": rm.limit,
        "has_more": rm.has_more,
    }


@router.get("/{space_id}/railmap")
async def get_railmap(
    request: Request,
    conn: ConnDep,
    crypto: CryptoDep,
    _: SpacesRead,
    workdir: WorkdirDep,
    space_id: uuid.UUID,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, object]:
    space, username, token = await space_credentials(conn, crypto, space_id)
    branch_rows = await db_list_branches(conn, space_id)
    branches = [(b.slug, b.kind) for b in branch_rows]
    user = await get_current_user(request, conn)
    assert user is not None
    viewed_id = await db_get_viewed_branch(conn, user.id, space_id)
    viewed_slug = next((b.slug for b in branch_rows if b.id == viewed_id), None)
    window = max(1, min(limit, _RAILMAP_MAX))
    start = max(0, offset)
    try:
        rm = await workdir.railmap(
            space_id, space.repo_url, username, token, branches, viewed_slug,
            offset=start, limit=window,
        )
    except WorkdirError as exc:
        status = 503 if exc.code == "repo_access_lost" else 502
        raise ApiError(exc.code, status_code=status) from None
    return _railmap_payload(rm)
