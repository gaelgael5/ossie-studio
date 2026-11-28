"""Brouillon de la personne connectée sur une branche : résumé, signal « a
changé côté publié » objet par objet, et préparation de la publication (fusion,
impacts, journal de validation). La règle métier vit dans
`drafts/preparation.py`, partagée avec la publication et l'agent."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.branches import space_credentials
from ossie_studio.api.common import SpacesRead
from ossie_studio.crypto import CryptoService
from ossie_studio.deps import ConnDep, get_crypto, get_draft_workdir
from ossie_studio.doc_index.queue import PUBLICATION_PAGE, queue_state, request_pass
from ossie_studio.doc_index.store import ViewKey, db_view_summary
from ossie_studio.drafts import preparation
from ossie_studio.drafts.preparation import changed_upstream
from ossie_studio.drafts.store import db_get_draft, db_list_objects
from ossie_studio.drafts.validation import blocking
from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.errors import ApiError
from ossie_studio.spaces.branch_service import db_get_branch
from ossie_studio.spaces.workdir import WorkdirError

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/draft")

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]
DraftWorkdirDep = Annotated[DraftWorkdir, Depends(get_draft_workdir)]

__all__ = ["changed_upstream", "router"]


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
            space_id, branch.git_ref, space.repo_url, username, token, [o.path for o in objects]
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
    """Les trois versions d'un objet du brouillon et la fusion proposée."""
    _draft, objects, versions = await preparation.merge_context(
        conn, crypto, workdir, space_id, branch_id
    )
    return preparation.merge_detail(preparation.find_object(objects, path), versions[path])


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
    draft, merged = await preparation.resolve_merge(
        conn, crypto, workdir, space_id, branch_id, body.path, body.resolutions, body.delete
    )
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
    return dict(await preparation.auto_merge_locked(conn, crypto, workdir, space_id, branch_id))


# --- Impacts -----------------------------------------------------------------


class VerifyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str


@router.get("/impacts")
async def get_impacts(
    conn: ConnDep,
    crypto: CryptoDep,
    _: SpacesRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    _draft, _objects, impacts = await preparation.draft_impacts(
        conn, crypto, workdir, space_id, branch_id
    )
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
    """« Vérifié, rien à corriger » : vaut tant que les objets lus ne changent
    plus ; part dans le message du commit."""
    await preparation.verify_impact(conn, crypto, workdir, space_id, branch_id, body.path)
    return {"path": body.path, "verified": True}


# --- Journal de validation -----------------------------------------------------


class AcceptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule: str
    path: str
    message_code: str


@router.get("/validation")
async def get_validation(
    conn: ConnDep,
    crypto: CryptoDep,
    _: SpacesRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    # Ouverture ou réaffichage de la page de publication : une passe de validation est
    # demandée pour le brouillon de la personne (enabler 6b171b74).
    await request_pass(conn, ViewKey(space_id, branch_id, workdir.user_id), PUBLICATION_PAGE)
    _draft, log_entries = await preparation.draft_validation(
        conn, crypto, workdir, space_id, branch_id
    )
    return {
        "entries": [e.payload() for e in log_entries],
        "blocking": len(blocking(log_entries)),
    }


@router.get("/validation/file")
async def get_validation_queue(
    conn: ConnDep,
    _: SpacesRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    """L'état de la file de validation (enabler 6b171b74) : la passe en cours (quelle
    vue, combien de fichiers faits sur combien), le nombre de demandes en attente, la
    position du brouillon de la personne et de la branche publiée, et l'état de
    validation de ces deux vues dans l'index."""
    draft = ViewKey(space_id, branch_id, workdir.user_id)
    published = draft.published
    state = await queue_state(conn)
    out = state.payload({"draft": draft, "published": published})
    for name, key in (("draft", draft), ("published", published)):
        summary = await db_view_summary(conn, key)
        view = out[name]
        assert isinstance(view, dict)
        view["index"] = summary.payload() if summary else None
    return out


@router.post("/validation/accept")
async def accept_warning(
    conn: ConnDep,
    crypto: CryptoDep,
    _: SpacesRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: AcceptBody,
) -> dict[str, object]:
    """Accepter un avertissement (jamais une erreur) : vaut tant que l'objet ne
    change plus ; part dans le message du commit."""
    await preparation.accept_warning(
        conn, crypto, workdir, space_id, branch_id, body.rule, body.path, body.message_code
    )
    return {"accepted": True}
