from __future__ import annotations

import re
import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.branches import space_credentials
from ossie_studio.api.common import (
    CurationRead,
    CurationReview,
    CurationRules,
    CurationWrite,
)
from ossie_studio.crypto import CryptoService
from ossie_studio.deps import ConnDep, get_crypto, get_draft_workdir
from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.errors import ApiError
from ossie_studio.models import GitSpace, User
from ossie_studio.sources import curation_actions
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
    build_review,
    get_object_detail,
    is_schema_file,
    list_curation,
    published_raw,
    read_enrichment,
    read_source_trees,
    read_tri_rules,
    read_tri_rules_raw,
)
from ossie_studio.sources.curation_actions import CurationTarget
from ossie_studio.sources.manifest import ManifestError
from ossie_studio.sources.paths import is_valid_source_slug, source_rel_dir
from ossie_studio.sources.tri import TRI_KINDS, TRI_TARGETS
from ossie_studio.spaces.branch_service import db_get_branch
from ossie_studio.spaces.workdir import WorkdirError

_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/sources/{slug}/curation")

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]
# Copie vue par la personne connectée : lectures avec son brouillon, écritures
# dans son brouillon (jamais de commit).
WorkdirDep = Annotated[DraftWorkdir, Depends(get_draft_workdir)]


class RetainBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = ""
    fields: list[str] | None = None  # None = objet entier


class RetainManyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paths: list[str] = []  # objets à retenir en entier ; les déjà retenus sont laissés


class RemoveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = ""


class RefreshBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paths: list[str] | None = None  # None = tous les assets retenus


class TriRuleModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str
    pattern: str
    kind: str
    label: str = ""


class RulesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rules: list[TriRuleModel] = []


class ReviewFieldRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    name: str


class ReviewApplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    head_sha: str
    apply_fields: list[ReviewFieldRef] = []  # champs retenus changés à rafraîchir
    take_fields: list[ReviewFieldRef] = []  # champs apparus à reprendre
    remove_fields: list[ReviewFieldRef] = []  # champs disparus à retirer
    remove_objects: list[str] = []  # objets disparus : retirer l'asset
    retain_objects: list[str] = []  # objets nouveaux : retenir en entier


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
        "excluded": obj.excluded,
        "excluded_by": obj.excluded_by,
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
    return branch.git_ref


@router.get("")
async def list_source_curation(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: CurationRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    show_excluded: bool = False,
) -> dict[str, object]:
    if not is_valid_source_slug(slug):
        raise ApiError("curation_path_invalid", status_code=400)
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        objects = await workdir.read_branch(
            space.id,
            branch_slug,
            space.repo_url,
            username,
            token,
            lambda wc: list_curation(wc, slug, read_tri_rules(wc, slug), show_excluded),
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
                "name": f.name,
                "type": f.type,
                "retained": f.retained,
                "ref": f.ref,
                "missing": f.missing,
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
            space.id,
            branch_slug,
            space.repo_url,
            username,
            token,
            lambda wc: get_object_detail(wc, slug, path, read_tri_rules(wc, slug)),
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
            space.id,
            branch_slug,
            space.repo_url,
            username,
            token,
            lambda wc: read_enrichment(wc, slug, path),
        )
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    if view is None:
        raise ApiError("asset_not_retained", status_code=404, path=path)
    return _enrich_payload(view)


def _target(
    workdir: DraftWorkdir,
    space: GitSpace,
    branch_slug: str,
    username: str,
    token: str | None,
    user: User,
) -> CurationTarget:
    return CurationTarget(workdir, space, branch_slug, username, token, user.email)


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
    # Seules les clés envoyées changent : la fenêtre de rédaction de la page
    # Curation n'envoie que la description, sans effacer nom métier ni tags.
    object_values = body.obj.model_dump(exclude_unset=True) if body.obj is not None else None
    field_values = {
        name: values.model_dump(exclude_unset=True) for name, values in body.fields.items()
    }
    try:
        result = await curation_actions.enrich(
            _target(workdir, space, branch_slug, username, token, user),
            slug,
            body.path,
            object_values,
            field_values,
        )
    except AssetNotRetainedError as exc:
        raise ApiError("asset_not_retained", status_code=404, path=exc.path) from None
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    log.info("curation_enrich", author=user.email, slug=slug, path=body.path)
    return {"ok": True, "commit": result.revision}


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
    try:
        result = await curation_actions.retain(
            _target(workdir, space, branch_slug, username, token, user),
            slug,
            body.path,
            body.fields,
        )
    except AssetOriginMissingError as exc:
        raise ApiError("asset_origin_missing", status_code=404, path=exc.path) from None
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    log.info(
        "curation_retain",
        author=user.email,
        slug=slug,
        path=body.path,
        fields=("all" if body.fields is None else len(body.fields)),
    )
    return {"ok": True, "commit": result.revision}


@router.post("/retain-many")
async def retain_source_assets(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationWrite,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    body: RetainManyBody,
) -> dict[str, object]:
    """Retenir plusieurs objets d'une source en un commit (« créer un asset avec
    ses enfants » depuis l'explorateur raw)."""
    if not body.paths:
        raise ApiError("curation_path_invalid", status_code=400)
    for path in body.paths:
        _check(slug, path)
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        result = await curation_actions.retain_many(
            _target(workdir, space, branch_slug, username, token, user), slug, body.paths
        )
    except AssetOriginMissingError as exc:
        raise ApiError("asset_origin_missing", status_code=404, path=exc.path) from None
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    log.info("curation_retain_many", author=user.email, slug=slug, paths=len(body.paths))
    return {"ok": True, "commit": result.revision, "retained": result.paths}


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
    try:
        result = await curation_actions.remove(
            _target(workdir, space, branch_slug, username, token, user), slug, body.path
        )
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    log.info("curation_remove", author=user.email, slug=slug, path=body.path)
    return {"ok": True, "commit": result.revision}


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
        result = await curation_actions.refresh(
            _target(workdir, space, branch_slug, username, token, user), slug, body.paths
        )
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    refreshed = [p.rsplit("/", 1)[-1] for p in result.paths]
    if refreshed:
        log.info("curation_refresh", author=user.email, slug=slug, count=len(refreshed))
    return {"ok": True, "commit": result.revision, "refreshed": refreshed}


# --- Règles de tri (écarter le bruit), réservées à l'administrateur -----------


@router.get("/rules")
async def get_source_rules(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: CurationRules,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
) -> dict[str, object]:
    if not is_valid_source_slug(slug):
        raise ApiError("curation_path_invalid", status_code=400)
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        rules = await workdir.read_branch(
            space.id,
            branch_slug,
            space.repo_url,
            username,
            token,
            lambda wc: read_tri_rules_raw(wc, slug),
        )
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    return {"rules": rules, "targets": list(TRI_TARGETS), "kinds": list(TRI_KINDS)}


@router.put("/rules")
async def set_source_rules(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationRules,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    body: RulesBody,
) -> dict[str, object]:
    if not is_valid_source_slug(slug):
        raise ApiError("curation_path_invalid", status_code=400)
    branch_slug = await _branch_slug(conn, space_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        result, cleaned = await curation_actions.set_tri_rules(
            _target(workdir, space, branch_slug, username, token, user),
            slug,
            [rule.model_dump() for rule in body.rules],
        )
    except curation_actions.RulesInvalidError as exc:
        # Motif / cible / genre invalide → refus nommant la règle (triage[i]:...).
        raise ApiError("invalid_request", status_code=400, fields=exc.fields) from None
    except curation_actions.SourceMissingError:
        raise ApiError("source_not_found", status_code=404, slug=slug) from None
    except ManifestError as exc:
        raise ApiError("invalid_request", status_code=400, fields=exc.fields) from None
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    commit = result.revision
    log.info("curation_rules_set", author=user.email, slug=slug, count=len(cleaned))
    return {"ok": True, "commit": commit, "rules": cleaned}


# --- Revue des changements (réservée à l'administrateur, curation:review) ------


def _review_object_payload(obj: ReviewObject) -> dict[str, object]:
    return {
        "path": obj.path,
        "title": obj.title,
        "status": obj.status,
        "fields": [
            {
                "name": f.name,
                "change": f.change,
                "type": f.type,
                "lost_enrichment": f.lost_enrichment,
            }
            for f in obj.fields
        ],
    }


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
    raw_rel = f"{source_rel_dir(slug)}/{RAW_DIR}"
    try:
        head_sha, published_texts, bundle = await workdir.review_inputs(
            space.id,
            branch_slug,
            space.repo_url,
            username,
            token,
            raw_rel,
            lambda wc: (read_source_trees(wc, slug), read_tri_rules(wc, slug)),
        )
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    (raw_view, assets), rules = bundle
    # Base : la branche publiée (ticket docflow aad1d3c6, plus de point de reprise).
    result = build_review(published_raw(raw_view, published_texts), raw_view, assets, rules)
    return {
        "head_sha": head_sha,
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
    decisions = ReviewDecisions(
        apply_fields=_group(body.apply_fields),
        take_fields=_group(body.take_fields),
        remove_fields=_group(body.remove_fields),
        remove_objects=list(dict.fromkeys(body.remove_objects)),
        retain_objects=list(dict.fromkeys(body.retain_objects)),
    )
    try:
        result = await curation_actions.apply_review(
            _target(workdir, space, branch_slug, username, token, user),
            slug,
            body.head_sha,
            decisions,
        )
        commit = result.revision
    except WorkdirError as exc:
        if exc.code == "review_stale":
            raise ApiError("review_stale", status_code=409) from None
        raise _workdir_error(exc) from None
    # Journalisation de la revue validée : auteur, source, volume des décisions.
    log.info(
        "curation_review_apply",
        author=user.email,
        slug=slug,
        commit=commit,
        apply=len(body.apply_fields),
        take=len(body.take_fields),
        remove=len(body.remove_fields),
        remove_objects=len(body.remove_objects),
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
