from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import (
    SpacesBranch,
    SpacesRead,
    SpacesVersion,
)
from ossie_studio.connections.service import db_get_connection, resolve_connection_token
from ossie_studio.crypto import CryptoService
from ossie_studio.deps import (
    ConnDep,
    get_crypto,
    get_current_user,
    get_draft_workdir,
    get_workdir_manager,
)
from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.errors import ApiError
from ossie_studio.forges.registry import get_strategy
from ossie_studio.models import GitSpace, SpaceBranch
from ossie_studio.spaces.branch_service import (
    KIND_BASE,
    KIND_CHILD,
    db_create_branch,
    db_get_base,
    db_get_branch,
    db_get_branch_by_slug,
    db_get_viewed_branch,
    db_list_branches,
    db_set_viewed_branch,
    is_valid_git_ref,
    slugify,
)
from ossie_studio.spaces.service import db_get_space
from ossie_studio.spaces.workdir import WorkdirError, WorkdirManager

router = APIRouter(prefix="/api/spaces/{space_id}/branches")

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]
WorkdirDep = Annotated[WorkdirManager, Depends(get_workdir_manager)]
# Copie vue par la personne connectée : le retour à une version va dans SON brouillon.
DraftWorkdirDep = Annotated[DraftWorkdir, Depends(get_draft_workdir)]


class ChildBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_ref: str = ""


class BaseBody(BaseModel):
    """Branche de base : on ADOPTE une branche existante de la forge (`git_ref`)
    ou on en CRÉE une, tirée de la racine, sous le nom saisi (`new_ref`). Pas de
    slug à saisir : le nom git est l'identité visible de la branche."""

    model_config = ConfigDict(extra="forbid")
    git_ref: str = ""
    new_ref: str = ""


class ViewedBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    branch_id: str = ""


class VersionLabelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = ""


def _payload(branch: SpaceBranch) -> dict[str, object]:
    return {
        "id": str(branch.id),
        "label": branch.label,
        "slug": branch.slug,
        "kind": branch.kind,
        "git_ref": branch.git_ref,
    }


async def space_credentials(
    conn: ConnDep, crypto: CryptoService, space_id: uuid.UUID
) -> tuple[GitSpace, str, str | None]:
    space = await db_get_space(conn, space_id)
    if space is None:
        raise ApiError("space_not_found", status_code=404)
    connection = await db_get_connection(conn, space.connection_id)
    if connection is None:
        raise ApiError("space_not_found", status_code=404)
    token = (await resolve_connection_token(conn, crypto, connection)).reveal()
    username = get_strategy(connection.forge).git_username()
    return space, username, token


@router.get("")
async def list_branches(
    request: Request, conn: ConnDep, _: SpacesRead, space_id: uuid.UUID
) -> dict[str, object]:
    if await db_get_space(conn, space_id) is None:
        raise ApiError("space_not_found", status_code=404)
    branches = await db_list_branches(conn, space_id)
    user = await get_current_user(request, conn)
    assert user is not None
    viewed = await db_get_viewed_branch(conn, user.id, space_id)
    return {
        "branches": [_payload(b) for b in branches],
        "viewed_branch_id": str(viewed) if viewed else None,
    }


@router.get("/remote")
async def list_remote_branches(
    conn: ConnDep,
    crypto: CryptoDep,
    _: SpacesBranch,
    workdir: WorkdirDep,
    space_id: uuid.UUID,
) -> dict[str, object]:
    """Branches du dépôt, pour choisir la branche de base. La racine est
    signalée (elle peut servir de base, notamment quand elle est seule)."""
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        heads, root = await workdir.remote_branches(space_id, space.repo_url, username, token)
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    tracked = {b.git_ref for b in await db_list_branches(conn, space_id)}
    return {
        "root": root,
        "branches": [{"name": h, "is_root": h == root, "tracked": h in tracked} for h in heads],
    }


async def _free_branch_slug(conn: ConnDep, space_id: uuid.UUID, ref: str) -> str:
    """Slug interne (jamais saisi) déduit du nom git, rendu libre par suffixe."""
    base = slugify(ref.replace("/", "-")) or "branche"
    slug, counter = base, 2
    while await db_get_branch_by_slug(conn, space_id, slug) is not None:
        slug, counter = f"{base}-{counter}", counter + 1
    return slug


@router.post("/base", status_code=201)
async def create_base(
    conn: ConnDep,
    crypto: CryptoDep,
    _: SpacesBranch,
    workdir: WorkdirDep,
    space_id: uuid.UUID,
    body: BaseBody,
) -> dict[str, object]:
    space, username, token = await space_credentials(conn, crypto, space_id)
    existing_base = await db_get_base(conn, space_id)
    if existing_base is not None:
        # Rejeu : la base existe déjà, ni seconde branche ni erreur.
        return {**_payload(existing_base), "already_exists": True}
    adopt, new = body.git_ref.strip(), body.new_ref.strip()
    if bool(adopt) == bool(new):
        raise ApiError("invalid_request", status_code=400, fields=["base_ref_required"])
    ref = adopt or new
    if not is_valid_git_ref(ref):
        raise ApiError("invalid_request", status_code=400, fields=["branch_name_invalid"])
    try:
        heads, _root = await workdir.remote_branches(space_id, space.repo_url, username, token)
        # La racine peut être ADOPTÉE comme base (cas d'un dépôt qui n'a qu'elle) ;
        # la recréer sous son nom n'a pas de sens (elle existe) : « branch_exists ».
        if adopt:
            if adopt not in heads:
                raise ApiError("branch_not_found", status_code=404)
        else:
            if new in heads:
                raise ApiError("branch_exists", status_code=409)
            await workdir.create_base_branch(space_id, new, space.repo_url, username, token)
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=409 if exc.code == "branch_exists" else 400) from None
    slug = await _free_branch_slug(conn, space_id, ref)
    branch = await db_create_branch(conn, space_id, ref, slug, KIND_BASE, git_ref=ref)
    return _payload(branch)


@router.post("/child", status_code=201)
async def create_child(
    conn: ConnDep,
    crypto: CryptoDep,
    _: SpacesBranch,
    workdir: WorkdirDep,
    space_id: uuid.UUID,
    body: ChildBody,
) -> dict[str, object]:
    """Branche fille, tirée de la tête de la base, sous le nom git saisi (pas de
    slug : le nom git est l'identité visible, comme pour la base)."""
    space, username, token = await space_credentials(conn, crypto, space_id)
    base = await db_get_base(conn, space_id)
    if base is None:
        raise ApiError("space_base_required", status_code=409)
    ref = body.new_ref.strip()
    if not is_valid_git_ref(ref):
        raise ApiError("invalid_request", status_code=400, fields=["branch_name_invalid"])
    tracked = next((b for b in await db_list_branches(conn, space_id) if b.git_ref == ref), None)
    if tracked is not None:
        if tracked.kind != KIND_CHILD:
            # Le nom désigne la base (la racine adoptée, par exemple) : refus.
            raise ApiError("branch_exists", status_code=409)
        # Rejeu : fille déjà déclarée sous ce nom, ni doublon ni erreur.
        return {**_payload(tracked), "already_exists": True}
    try:
        heads, root = await workdir.remote_branches(space_id, space.repo_url, username, token)
        if ref == root:
            raise ApiError("branch_root_readonly", status_code=409, ref=ref)
        if ref in heads:
            raise ApiError("branch_exists", status_code=409)
        await workdir.create_child_branch(
            space_id, base.git_ref, ref, space.repo_url, username, token
        )
    except WorkdirError as exc:
        status = 409 if exc.code in ("branch_exists", "branch_not_found") else 400
        raise ApiError(exc.code, status_code=status) from None
    slug = await _free_branch_slug(conn, space_id, ref)
    branch = await db_create_branch(conn, space_id, ref, slug, KIND_CHILD, git_ref=ref)
    return _payload(branch)


@router.put("/viewed")
async def set_viewed(
    conn: ConnDep, user: SpacesRead, space_id: uuid.UUID, body: ViewedBody
) -> dict[str, bool]:
    try:
        branch_id = uuid.UUID(body.branch_id)
    except ValueError:
        raise ApiError("branch_not_found", status_code=404) from None
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    await db_set_viewed_branch(conn, user.id, space_id, branch_id)
    return {"ok": True}


async def _branch_in_space(conn: ConnDep, space_id: uuid.UUID, branch_id: uuid.UUID) -> SpaceBranch:
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    return branch


def _version_error(exc: WorkdirError) -> ApiError:
    status = {
        "branch_not_found": 404,
        "version_not_found": 404,
        "publish_required": 409,
        "nothing_to_version": 409,
        "version_taken": 409,
        "repo_access_lost": 503,
    }.get(exc.code, 502)
    return ApiError(exc.code, status_code=status)


def _validate_version_label(label: str) -> None:
    # Un libellé tient sur une seule ligne, non vide, d'au plus 200 caractères.
    errors: list[str] = []
    if not label.strip():
        errors.append("label_required")
    if len(label) > 200:
        errors.append("label_too_long")
    if "\n" in label or "\r" in label:
        errors.append("label_single_line")
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)


@router.get("/{branch_id}/versions")
async def list_versions(
    conn: ConnDep,
    crypto: CryptoDep,
    _: SpacesRead,
    workdir: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    branch = await _branch_in_space(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        versions = await workdir.list_versions(
            space_id, branch.git_ref, space.repo_url, username, token
        )
    except WorkdirError as exc:
        raise _version_error(exc) from None
    return {
        "versions": [
            {
                "number": v.number,
                "label": v.label,
                "commit": v.commit,
                "author": v.author,
                "date": v.date,
            }
            for v in versions
        ]
    }


@router.post("/{branch_id}/versions", status_code=201)
async def create_version(
    conn: ConnDep,
    crypto: CryptoDep,
    user: SpacesVersion,
    workdir: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: VersionLabelBody,
) -> dict[str, object]:
    branch = await _branch_in_space(conn, space_id, branch_id)
    _validate_version_label(body.label)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        result = await workdir.tag_version(
            space_id,
            branch.git_ref,
            space.repo_url,
            username,
            token,
            body.label.strip(),
            user.email,
            user.email,
        )
    except WorkdirError as exc:
        raise _version_error(exc) from None
    return {
        "number": result.number,
        "commit": result.commit,
        "already_exists": result.already_exists,
    }


@router.post("/{branch_id}/versions/{number}/restore")
async def restore_version(
    conn: ConnDep,
    crypto: CryptoDep,
    user: SpacesVersion,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    number: int,
) -> dict[str, object]:
    branch = await _branch_in_space(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        result = await workdir.restore_version(
            space_id,
            branch.git_ref,
            space.repo_url,
            username,
            token,
            number,
            user.email,
            user.email,
        )
    except WorkdirError as exc:
        raise _version_error(exc) from None
    return {
        "number": result.number,
        "label": result.label,
        "already_current": result.already_current,
        "created": result.created,
    }


@router.get("/{branch_id}/head")
async def branch_head(
    conn: ConnDep,
    crypto: CryptoDep,
    _: SpacesRead,
    workdir: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, str]:
    """Tête publiée de la branche (route légère, interrogée par les écrans)."""
    branch = await _branch_in_space(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        sha = await workdir.published_head(
            space_id, branch.git_ref, space.repo_url, username, token
        )
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    return {"sha": sha}
