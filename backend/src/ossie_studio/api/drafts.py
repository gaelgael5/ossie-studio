"""Brouillon de la personne connectée sur une branche : résumé et signal
« a changé côté publié » objet par objet."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
import yaml
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.branches import space_credentials
from ossie_studio.api.common import SpacesRead
from ossie_studio.crypto import CryptoService
from ossie_studio.deps import ConnDep, get_crypto, get_draft_workdir
from ossie_studio.drafts.impacts import Impact, compute_impacts, fingerprint
from ossie_studio.drafts.merge import (
    MISSING,
    Conflict,
    ObjectMerge,
    is_structured,
    merge_object,
    parse,
    serialize,
    set_pointer,
)
from ossie_studio.drafts.store import (
    Draft,
    DraftObject,
    db_decisions,
    db_get_draft,
    db_list_objects,
    db_rebase_object,
    db_set_decision,
)
from ossie_studio.drafts.workdir import DraftWorkdir, ObjectVersions
from ossie_studio.errors import ApiError
from ossie_studio.spaces.branch_service import db_get_branch
from ossie_studio.spaces.workdir import WorkdirError

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/draft")

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]
DraftWorkdirDep = Annotated[DraftWorkdir, Depends(get_draft_workdir)]


def changed_upstream(obj: DraftObject, current: str | None) -> bool:
    """La version publiée de l'objet a-t-elle bougé depuis le début du
    brouillon ? Comparaison **objet par objet** (empreinte de base contre blob
    actuel du même chemin), jamais la tête de branche."""
    return obj.base_blob != current


@router.get("")
async def my_draft(
    conn: ConnDep,
    crypto: CryptoDep,
    _: SpacesRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    draft = await db_get_draft(conn, workdir.user_id, branch_id)
    if draft is None or draft.space_id != space_id:
        return {"count": 0, "revision": 0, "changed_upstream_count": 0, "objects": []}
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    objects = await db_list_objects(conn, draft.id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        current = await workdir.published_blobs(
            space_id, branch.slug, space.repo_url, username, token, [o.path for o in objects]
        )
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    items = [
        {
            "path": o.path,
            "state": o.state,
            "changed_upstream": changed_upstream(o, current.get(o.path)),
        }
        for o in objects
    ]
    return {
        "count": len(items),
        "revision": draft.revision,
        "changed_upstream_count": sum(1 for i in items if i["changed_upstream"]),
        "objects": items,
    }


# --- Fusion ------------------------------------------------------------------


class MergeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    # Pointeur JSON → "theirs" | "mine" | {"value": …} ; « "" » = l'objet entier
    # (repli texte).
    resolutions: dict[str, Any] = {}
    # Objet supprimé d'un côté : True = accepter la suppression, False = garder
    # l'objet (version du brouillon, ou version publiée).
    delete: bool | None = None


async def _merge_context(
    conn: ConnDep,
    crypto: CryptoService,
    workdir: DraftWorkdir,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> tuple[Draft, list[DraftObject], dict[str, ObjectVersions]]:
    draft = await db_get_draft(conn, workdir.user_id, branch_id)
    if draft is None or draft.space_id != space_id:
        raise ApiError("draft_object_not_found", status_code=404)
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    objects = await db_list_objects(conn, draft.id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        versions = await workdir.object_versions(
            space_id, branch.slug, space.repo_url, username, token, objects
        )
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    return draft, objects, versions


def _json(value: Any) -> Any:
    return None if value is MISSING else value


def _object(objects: list[DraftObject], path: str) -> DraftObject:
    for obj in objects:
        if obj.path == path:
            return obj
    raise ApiError("draft_object_not_found", status_code=404, path=path)


@router.get("/merge")
async def get_merge(
    conn: ConnDep,
    crypto: CryptoDep,
    _: SpacesRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    path: str,
) -> dict[str, object]:
    """Les trois versions d'un objet du brouillon et la fusion proposée. Sans
    changement côté publié : lecture seule (correspondance des modifications)."""
    _draft, objects, versions = await _merge_context(conn, crypto, workdir, space_id, branch_id)
    obj = _object(objects, path)
    v = versions[path]
    upstream = changed_upstream(obj, v.current_blob)
    mine = obj.content
    merged = merge_object(path, v.base, v.theirs, mine) if upstream else None
    shown = merged or merge_object(path, v.base, v.base, mine)
    proposed: Any = None
    if merged is not None and merged.result is not None:
        proposed = parse(path, merged.result) if merged.structured else merged.result.decode()
    return {
        "path": path,
        "state": obj.state,
        "changed_upstream": upstream,
        "readonly": not upstream,
        "kind": merged.kind if merged else "none",
        "structured": shown.structured,
        "base": _json(shown.base),
        "theirs": _json(merged.theirs if merged else shown.base),
        "mine": _json(shown.mine),
        "theirs_absent": v.theirs is None,
        "mine_absent": mine is None,
        "proposed": _json(proposed),
        "conflicts": [
            {
                "pointer": c.pointer,
                "base": _json(c.base),
                "theirs": _json(c.theirs),
                "mine": _json(c.mine),
            }
            for c in (merged.conflicts if merged else [])
        ],
    }


def _choice(choice: Any, conflict: Conflict) -> Any:
    if choice == "theirs":
        return conflict.theirs
    if choice == "mine":
        return conflict.mine
    if isinstance(choice, dict) and "value" in choice:
        return choice["value"]
    return None


def _resolved_content(
    path: str, merged: ObjectMerge, body: MergeBody, theirs: bytes | None
) -> bytes | None:
    """Contenu final de l'objet après les choix de l'utilisateur (None =
    supprimé). Lève merge_unresolved si un conflit reste sans choix."""
    if merged.kind in ("deleted_upstream", "deleted_in_draft"):
        if body.delete is None:
            raise ApiError("merge_unresolved", status_code=400, pointers=[""])
        if body.delete:
            return None
        return merged.result if merged.kind == "deleted_upstream" else theirs
    if merged.kind == "text_conflict":
        choice = body.resolutions.get("")
        if choice == "theirs":
            return theirs
        if choice == "mine":
            return merged.result
        if isinstance(choice, dict) and isinstance(choice.get("value"), str):
            return str(choice["value"]).encode("utf-8")
        raise ApiError("merge_unresolved", status_code=400, pointers=[""])
    if merged.kind == "conflict":
        missing = [c.pointer for c in merged.conflicts if c.pointer not in body.resolutions]
        if missing:
            raise ApiError("merge_unresolved", status_code=400, pointers=missing)
        document = parse(path, merged.result)
        for conflict in merged.conflicts:
            value = _choice(body.resolutions[conflict.pointer], conflict)
            document = set_pointer(document, conflict.pointer, value)
        return serialize(path, document)
    return merged.result


def _same(path: str, content: bytes | None, theirs: bytes | None) -> bool:
    """Même contenu que le publié ? Par valeur pour un objet structuré (la mise
    en forme peut différer), octet par octet sinon."""
    if content is None or theirs is None:
        return content is theirs
    if content == theirs:
        return True
    if not is_structured(path):
        return False
    try:
        return bool(parse(path, content) == parse(path, theirs))
    except (ValueError, yaml.YAMLError):
        return False


async def _rebase(
    conn: ConnDep,
    draft: Draft,
    path: str,
    current_blob: str | None,
    theirs: bytes | None,
    content: bytes | None,
) -> None:
    # Résultat identique au publié : l'objet sort du brouillon.
    if _same(path, content, theirs):
        await db_rebase_object(conn, draft.id, path, None, None)
    else:
        await db_rebase_object(conn, draft.id, path, current_blob, content)


@router.post("/merge")
async def post_merge(
    conn: ConnDep,
    crypto: CryptoDep,
    _: SpacesRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: MergeBody,
) -> dict[str, object]:
    """Enregistre la version réconciliée dans le brouillon ; sa base devient la
    version publiée actuelle (l'objet n'est plus en conflit)."""
    draft, objects, versions = await _merge_context(conn, crypto, workdir, space_id, branch_id)
    obj = _object(objects, body.path)
    v = versions[body.path]
    merged = merge_object(body.path, v.base, v.theirs, obj.content)
    content = _resolved_content(body.path, merged, body, v.theirs)
    await _rebase(conn, draft, body.path, v.current_blob, v.theirs, content)
    log.info("draft_object_merged", draft_id=str(draft.id), kind=merged.kind)
    return {"path": body.path, "merged": True}


@router.post("/automerge")
async def post_automerge(
    conn: ConnDep,
    crypto: CryptoDep,
    _: SpacesRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    """Fusionne d'office tout objet changé côté publié sans conflit ; rend les
    chemins fusionnés et ceux qui demandent l'utilisateur."""
    draft, objects, versions = await _merge_context(conn, crypto, workdir, space_id, branch_id)
    merged_paths: list[str] = []
    conflicts: list[str] = []
    for obj in objects:
        v = versions[obj.path]
        if not changed_upstream(obj, v.current_blob):
            continue
        merged = merge_object(obj.path, v.base, v.theirs, obj.content)
        if merged.kind in ("auto", "text"):
            await _rebase(conn, draft, obj.path, v.current_blob, v.theirs, merged.result)
            merged_paths.append(obj.path)
        else:
            conflicts.append(obj.path)
    return {"merged": merged_paths, "conflicts": conflicts}


# --- Impacts -----------------------------------------------------------------


class VerifyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str


async def draft_impacts(
    conn: ConnDep,
    crypto: CryptoService,
    workdir: DraftWorkdir,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> tuple[Draft | None, list[DraftObject], list[Impact]]:
    """Impacts du brouillon de la personne (vide sans brouillon). Réutilisé par la
    publication pour refuser tant qu'un impact n'est pas traité."""
    draft = await db_get_draft(conn, workdir.user_id, branch_id)
    if draft is None or draft.space_id != space_id:
        return None, [], []
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    objects = await db_list_objects(conn, draft.id)
    verified = await db_decisions(conn, draft.id, "impact_verified")
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        impacts = await workdir.read_branch(
            space_id,
            branch.slug,
            space.repo_url,
            username,
            token,
            lambda view: compute_impacts(view, objects, verified),
        )
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    return draft, objects, impacts


@router.get("/impacts")
async def get_impacts(
    conn: ConnDep,
    crypto: CryptoDep,
    _: SpacesRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    _draft, _objects, impacts = await draft_impacts(conn, crypto, workdir, space_id, branch_id)
    return {
        "impacts": [i.payload() for i in impacts],
        "pending": sum(1 for i in impacts if not i.verified),
    }


@router.post("/impacts/verify")
async def verify_impact(
    conn: ConnDep,
    crypto: CryptoDep,
    _: SpacesRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: VerifyBody,
) -> dict[str, object]:
    """« Vérifié, rien à corriger » : la décision vaut tant que les objets lus ne
    changent plus ; elle partira dans le message du commit."""
    draft, objects, impacts = await draft_impacts(conn, crypto, workdir, space_id, branch_id)
    impact = next((i for i in impacts if i.path == body.path), None)
    if draft is None or impact is None:
        raise ApiError("impact_not_found", status_code=404, path=body.path)
    await db_set_decision(
        conn, draft.id, "impact_verified", body.path, fingerprint(impact.reads, objects)
    )
    return {"path": body.path, "verified": True}
