from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.branches import space_credentials
from ossie_studio.api.common import CurationRead, CurationWrite
from ossie_studio.crypto import CryptoService
from ossie_studio.deps import ConnDep, get_crypto, get_workdir_manager
from ossie_studio.errors import ApiError
from ossie_studio.sources.curation import (
    AssetNotRetainedError,
    AssetOriginMissingError,
    CurationDetail,
    CurationObject,
    EnrichScope,
    EnrichView,
    enrich_asset_fn,
    get_object_detail,
    is_schema_file,
    list_curation,
    read_enrichment,
    remove_asset_fn,
    retain_asset_fn,
)
from ossie_studio.sources.paths import is_valid_source_slug
from ossie_studio.spaces.branch_service import db_get_branch
from ossie_studio.spaces.workdir import WorkdirError, WorkdirManager

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/sources/{slug}/curation")

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]
WorkdirDep = Annotated[WorkdirManager, Depends(get_workdir_manager)]


class RetainBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = ""
    fields: list[str] | None = None  # None = objet entier


class RemoveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = ""


class EnrichValues(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_name: str = ""
    description: str = ""
    concept: str = ""
    tags: list[str] = []


class EnrichBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = ""
    obj: EnrichValues | None = None
    fields: dict[str, EnrichValues] = {}


def _object_payload(obj: CurationObject) -> dict[str, object]:
    return {
        "path": obj.path,
        "title": obj.title,
        "kind": obj.kind,
        "fields": obj.fields,
        "retained": obj.retained,
    }


def _check(slug: str, path: str) -> None:
    if not is_valid_source_slug(slug) or not is_schema_file(path):
        raise ApiError("curation_path_invalid", status_code=400)


def _workdir_error(exc: WorkdirError) -> ApiError:
    return ApiError(exc.code, status_code=404 if exc.code == "branch_not_found" else 502)


async def _branch_slug(conn: ConnDep, space_id: uuid.UUID, branch_id: uuid.UUID) -> str:
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    return branch.slug


@router.get("")
async def list_source_curation(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: CurationRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
) -> dict[str, object]:
    if not is_valid_source_slug(slug):
        raise ApiError("curation_path_invalid", status_code=400)
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        objects = await workdir.read_branch(
            space.id, branch_slug, space.repo_url, username, token,
            lambda wc: list_curation(wc, slug),
        )
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    return {"objects": [_object_payload(o) for o in objects]}


def _detail_payload(detail: CurationDetail) -> dict[str, object]:
    return {
        "path": detail.path,
        "title": detail.title,
        "fields": [
            {"name": f.name, "type": f.type, "retained": f.retained, "ref": f.ref}
            for f in detail.fields
        ],
    }


@router.get("/object")
async def get_source_curation_object(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: CurationRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    path: str,
) -> dict[str, object]:
    _check(slug, path)
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        detail = await workdir.read_branch(
            space.id, branch_slug, space.repo_url, username, token,
            lambda wc: get_object_detail(wc, slug, path),
        )
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    if detail is None:
        raise ApiError("asset_origin_missing", status_code=404, path=path)
    return _detail_payload(detail)


def _scope_payload(scope: EnrichScope) -> dict[str, object]:
    return {
        "name": scope.name,
        "business_name": scope.business_name,
        "description": scope.description,
        "concept": scope.concept,
        "tags": scope.tags,
        "readonly": scope.readonly,
    }


def _enrich_payload(view: EnrichView) -> dict[str, object]:
    return {
        "path": view.path,
        "title": view.title,
        "object": _scope_payload(view.object),
        "fields": [_scope_payload(f) for f in view.fields],
    }


@router.get("/enrichment")
async def get_source_enrichment(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: CurationRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    path: str,
) -> dict[str, object]:
    _check(slug, path)
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        view = await workdir.read_branch(
            space.id, branch_slug, space.repo_url, username, token,
            lambda wc: read_enrichment(wc, slug, path),
        )
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    if view is None:
        raise ApiError("asset_not_retained", status_code=404, path=path)
    return _enrich_payload(view)


@router.put("/enrich")
async def enrich_source_asset(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationWrite,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    body: EnrichBody,
) -> dict[str, object]:
    _check(slug, body.path)
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    object_values = body.obj.model_dump() if body.obj is not None else None
    field_values = {name: values.model_dump() for name, values in body.fields.items()}
    message = f"curation {slug} : enrichir {body.path}"
    try:
        commit = await workdir.commit_to_branch(
            space.id, branch_slug, space.repo_url, username, token,
            user.email, user.email, message,
            enrich_asset_fn(slug, body.path, object_values, field_values),
        )
    except AssetNotRetainedError as exc:
        raise ApiError("asset_not_retained", status_code=404, path=exc.path) from None
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    log.info("curation_enrich", author=user.email, slug=slug, path=body.path)
    return {"ok": True, "commit": commit}


@router.post("/retain")
async def retain_source_asset(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationWrite,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    body: RetainBody,
) -> dict[str, object]:
    _check(slug, body.path)
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    message = f"curation {slug} : retenir {body.path}"
    try:
        commit = await workdir.commit_to_branch(
            space.id, branch_slug, space.repo_url, username, token,
            user.email, user.email, message,
            retain_asset_fn(slug, body.path, body.fields),
        )
    except AssetOriginMissingError as exc:
        raise ApiError("asset_origin_missing", status_code=404, path=exc.path) from None
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    log.info(
        "curation_retain", author=user.email, slug=slug, path=body.path,
        fields=("all" if body.fields is None else len(body.fields)),
    )
    return {"ok": True, "commit": commit}


@router.post("/remove")
async def remove_source_asset(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationWrite,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    body: RemoveBody,
) -> dict[str, object]:
    _check(slug, body.path)
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    message = f"curation {slug} : retirer {body.path}"
    try:
        commit = await workdir.commit_to_branch(
            space.id, branch_slug, space.repo_url, username, token,
            user.email, user.email, message,
            remove_asset_fn(slug, body.path),
        )
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    log.info("curation_remove", author=user.email, slug=slug, path=body.path)
    return {"ok": True, "commit": commit}
