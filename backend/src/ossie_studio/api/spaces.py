from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import AdminApiDep
from ossie_studio.connections.service import db_get_connection, resolve_connection_token
from ossie_studio.crypto import CryptoService
from ossie_studio.deps import ConnDep, GitProbe, get_crypto, get_git_probe
from ossie_studio.errors import ApiError
from ossie_studio.forges.registry import get_strategy
from ossie_studio.models import GitSpace
from ossie_studio.spaces.forms import validate_space
from ossie_studio.spaces.git_probe import ProbeResult
from ossie_studio.spaces.service import (
    SpaceNameTakenError,
    SpaceRepoTakenError,
    db_create_space,
    db_delete_space,
    db_get_space,
    db_list_spaces,
    db_update_space_probe,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/spaces")

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]
GitProbeDep = Annotated[GitProbe, Depends(get_git_probe)]


class SpaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    connection_id: str = ""
    repo_url: str = ""


def _payload(space: GitSpace) -> dict[str, object]:
    return {
        "id": str(space.id),
        "name": space.name,
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
async def list_spaces(conn: ConnDep, _: AdminApiDep) -> dict[str, object]:
    spaces = await db_list_spaces(conn)
    return {"spaces": [_payload(s) for s in spaces]}


@router.post("", status_code=201)
async def create_space(
    conn: ConnDep, crypto: CryptoDep, _: AdminApiDep, git_probe: GitProbeDep, body: SpaceCreate
) -> dict[str, object]:
    errors = validate_space(body.name, body.connection_id, body.repo_url)
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    connection_id = uuid.UUID(body.connection_id)
    repo_url = body.repo_url.strip().rstrip("/")

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
        )
    except SpaceNameTakenError:
        raise ApiError("space_name_taken", status_code=409) from None
    except SpaceRepoTakenError as exc:
        raise ApiError("space_repo_taken", status_code=409, space=exc.existing_name) from None
    return {**_payload(space), "probe": _probe_detail(result)}


@router.post("/{space_id}/probe")
async def reprobe_space(
    conn: ConnDep, crypto: CryptoDep, _: AdminApiDep, git_probe: GitProbeDep, space_id: uuid.UUID
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
async def delete_space(conn: ConnDep, _: AdminApiDep, space_id: uuid.UUID) -> None:
    # Supprimer un espace ne touche pas au dépôt.
    deleted = await db_delete_space(conn, space_id)
    if not deleted:
        raise ApiError("space_not_found", status_code=404)
