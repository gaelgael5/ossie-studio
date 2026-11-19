from __future__ import annotations

import re
import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.branches import space_credentials
from ossie_studio.api.common import CurationRead, CurationReview, CurationWrite
from ossie_studio.crypto import CryptoService
from ossie_studio.deps import ConnDep, get_crypto, get_workdir_manager
from ossie_studio.errors import ApiError
from ossie_studio.sources.collect_core import RAW_DIR
from ossie_studio.sources.curation import (
    AssetNotRetainedError,
    AssetOriginMissingError,
    CurationDetail,
    CurationObject,
    EnrichScope,
    EnrichView,
    ReviewDecisions,
    ReviewObject,
    apply_review_fn,
    build_review,
    enrich_asset_fn,
    get_object_detail,
    is_schema_file,
    list_curation,
    parse_schema_texts,
    read_enrichment,
    read_review_point,
    read_source_trees,
    refresh_assets_fn,
    refresh_targets,
    remove_asset_fn,
    retain_asset_fn,
)
from ossie_studio.sources.paths import is_valid_source_slug, source_rel_dir
from ossie_studio.spaces.branch_service import db_get_branch
from ossie_studio.spaces.workdir import WorkdirError, WorkdirManager

_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")

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


class RefreshBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paths: list[str] | None = None  # None = tous les assets retenus


class ReviewFieldRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    name: str


class ReviewApplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    head_sha: str
    apply_fields: list[ReviewFieldRef] = []    # champs retenus changés à rafraîchir
    take_fields: list[ReviewFieldRef] = []     # champs apparus à reprendre
    remove_fields: list[ReviewFieldRef] = []   # champs disparus à retirer
    remove_objects: list[str] = []             # objets disparus : retirer l'asset
    retain_objects: list[str] = []             # objets nouveaux : retenir en entier


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
        "orphan": obj.orphan,
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
            {
                "name": f.name, "type": f.type, "retained": f.retained,
                "ref": f.ref, "missing": f.missing,
            }
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


@router.post("/refresh")
async def refresh_source_assets(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationWrite,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    body: RefreshBody,
) -> dict[str, object]:
    if not is_valid_source_slug(slug):
        raise ApiError("curation_path_invalid", status_code=400)
    if body.paths is not None and not all(is_schema_file(p) for p in body.paths):
        raise ApiError("curation_path_invalid", status_code=400)
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        # On calcule d'abord les assets qui changeraient : le message les liste et
        # on évite un commit à vide (asset déjà à jour).
        changed = await workdir.read_branch(
            space.id, branch_slug, space.repo_url, username, token,
            lambda wc: refresh_targets(wc, slug, body.paths),
        )
        if not changed:
            return {"ok": True, "commit": None, "refreshed": []}
        message = f"curation {slug} : rafraîchir {len(changed)} asset(s) : {', '.join(changed)}"
        commit = await workdir.commit_to_branch(
            space.id, branch_slug, space.repo_url, username, token,
            user.email, user.email, message,
            refresh_assets_fn(slug, changed),
        )
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    log.info("curation_refresh", author=user.email, slug=slug, count=len(changed))
    return {"ok": True, "commit": commit, "refreshed": changed}


# --- Revue des changements (réservée à l'administrateur, curation:review) ------


def _review_object_payload(obj: ReviewObject) -> dict[str, object]:
    return {
        "path": obj.path,
        "title": obj.title,
        "status": obj.status,
        "fields": [
            {
                "name": f.name, "change": f.change, "type": f.type,
                "lost_enrichment": f.lost_enrichment,
            }
            for f in obj.fields
        ],
    }


async def _review_divergence_or_refuse(
    workdir: WorkdirManager, space_id: uuid.UUID, slug: str, repo_url: str,
    username: str, token: str | None,
) -> None:
    try:
        ahead, behind = await workdir.divergence(space_id, slug, repo_url, username, token)
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    if ahead > 0 and behind > 0:
        # Base périmée : comme la collecte, on refuse plutôt que de revoir à l'aveugle.
        raise ApiError("review_branch_behind", status_code=409)


@router.get("/review")
async def get_source_review(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: CurationReview,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
) -> dict[str, object]:
    if not is_valid_source_slug(slug):
        raise ApiError("curation_path_invalid", status_code=400)
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    await _review_divergence_or_refuse(
        workdir, space.id, branch_slug, space.repo_url, username, token
    )
    raw_rel = f"{source_rel_dir(slug)}/{RAW_DIR}"
    try:
        head_sha, base_sha, raw_base_text, trees = await workdir.review_inputs(
            space.id, branch_slug, space.repo_url, username, token,
            raw_rel,
            lambda wc: read_review_point(wc, slug),
            lambda wc: read_source_trees(wc, slug),
        )
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    raw_head, assets = trees
    result = build_review(parse_schema_texts(raw_base_text), raw_head, assets)
    return {
        "base_sha": base_sha,
        "head_sha": head_sha,
        "up_to_date": base_sha == head_sha,
        "objects": [_review_object_payload(o) for o in result.objects],
    }


def _group(refs: list[ReviewFieldRef]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for ref in refs:
        grouped.setdefault(ref.path, []).append(ref.name)
    return grouped


@router.post("/review/apply")
async def apply_source_review(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationReview,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    body: ReviewApplyBody,
) -> dict[str, object]:
    if not is_valid_source_slug(slug) or not _SHA_RE.match(body.head_sha):
        raise ApiError("curation_path_invalid", status_code=400)
    field_paths = [r.path for r in (*body.apply_fields, *body.take_fields, *body.remove_fields)]
    all_paths = (*field_paths, *body.remove_objects, *body.retain_objects)
    if not all(is_schema_file(p) for p in all_paths):
        raise ApiError("curation_path_invalid", status_code=400)
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    await _review_divergence_or_refuse(
        workdir, space.id, branch_slug, space.repo_url, username, token
    )
    decisions = ReviewDecisions(
        apply_fields=_group(body.apply_fields),
        take_fields=_group(body.take_fields),
        remove_fields=_group(body.remove_fields),
        remove_objects=list(dict.fromkeys(body.remove_objects)),
        retain_objects=list(dict.fromkeys(body.retain_objects)),
    )
    try:
        commit = await workdir.commit_to_branch(
            space.id, branch_slug, space.repo_url, username, token,
            user.email, user.email, f"curation {slug} : revue validée",
            apply_review_fn(slug, body.head_sha, decisions),
            expect_head=body.head_sha,
        )
    except WorkdirError as exc:
        if exc.code == "review_stale":
            raise ApiError("review_stale", status_code=409) from None
        raise _workdir_error(exc) from None
    # Journalisation de la revue validée : auteur, source, volume des décisions.
    log.info(
        "curation_review_apply", author=user.email, slug=slug, commit=commit,
        apply=len(body.apply_fields), take=len(body.take_fields),
        remove=len(body.remove_fields), remove_objects=len(body.remove_objects),
        retain=len(body.retain_objects),
    )
    return {"ok": True, "commit": commit}


@router.get("/review/history")
async def get_source_review_history(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: CurationReview,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    path: str,
) -> dict[str, object]:
    _check(slug, path)
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    rel = f"{source_rel_dir(slug)}/{RAW_DIR}/{path}"
    try:
        commits = await workdir.file_history(
            space.id, branch_slug, space.repo_url, username, token, rel
        )
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    return {
        "commits": [
            {"sha": c.sha, "author": c.author, "date": c.date, "message": c.message}
            for c in commits
        ],
    }


@router.get("/review/raw-at")
async def get_source_raw_at(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: CurationReview,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    path: str,
    sha: str,
) -> dict[str, object]:
    _check(slug, path)
    if not _SHA_RE.match(sha):
        raise ApiError("curation_path_invalid", status_code=400)
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    rel = f"{source_rel_dir(slug)}/{RAW_DIR}/{path}"
    try:
        content = await workdir.file_at(
            space.id, branch_slug, space.repo_url, username, token, sha, rel
        )
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    if content is None:
        raise ApiError("asset_origin_missing", status_code=404, path=path)
    return {"path": path, "sha": sha, "content": content}
