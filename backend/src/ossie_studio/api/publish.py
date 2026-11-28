"""Publication du brouillon : un brouillon = un commit, sous l'identité de la
personne, avec SON jeton, sous un verrou par branche.

Décisions (feature « Écran et procédure de publication sous verrou ») : le
bouton n'est actif que si rien ne bloque (droit sur l'espace, conflits,
impacts, erreurs liées au brouillon, identifiant valide, aucune publication en
cours) ; le serveur revérifie tout. Le verrou est un verrou consultatif
PostgreSQL tenu par une connexion dédiée : il se relâche si le backend tombe."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Annotated

import asyncpg
import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.branches import space_credentials
from ossie_studio.api.common import CurrentUser, SpacesRead
from ossie_studio.connections.service import db_get_connection
from ossie_studio.credentials.service import STATUS_VALID, resolve_personal_token
from ossie_studio.crypto import CryptoService
from ossie_studio.deps import ConnDep, get_crypto, get_draft_workdir
from ossie_studio.doc_index.passes import Validation
from ossie_studio.doc_index.queue import PUBLISH, request_pass
from ossie_studio.doc_index.store import ViewKey, db_view_summary
from ossie_studio.drafts.preparation import (
    auto_merge,
    draft_impacts,
    draft_validation,
    merge_context,
    pending_merges,
)
from ossie_studio.drafts.store import (
    db_decision_agents,
    db_get_draft,
    db_list_objects,
    db_remove_published,
    git_blob_sha,
)
from ossie_studio.drafts.validation import blocking
from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.errors import ApiError
from ossie_studio.forges.registry import get_strategy
from ossie_studio.models import User
from ossie_studio.profiles.service import load_capabilities
from ossie_studio.spaces.branch_service import db_get_branch
from ossie_studio.spaces.workdir import _COMMIT_PREFIX, PublishObject, WorkdirError

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/draft")

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]
DraftWorkdirDep = Annotated[DraftWorkdir, Depends(get_draft_workdir)]


class PublishBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comment: str = ""


@dataclass
class Readiness:
    reasons: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.reasons


def _lock_key(branch_id: uuid.UUID) -> str:
    return f"publish:{branch_id}"


async def _publish_in_progress(pool: asyncpg.Pool, branch_id: uuid.UUID) -> bool:
    """Une publication tient-elle le verrou de la branche ? Lecture de pg_locks,
    sans jamais prendre le verrou : une simple vérification ne doit pas faire
    échouer une vraie publication (clé bigint = classid haut, objid bas)."""
    async with pool.acquire() as conn:
        return bool(
            await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
                "AND granted AND objsubid = 1 "
                "AND classid::bigint = ((hashtextextended($1, 0) >> 32) & 4294967295) "
                "AND objid::bigint = (hashtextextended($1, 0) & 4294967295))",
                _lock_key(branch_id),
            )
        )


async def _credential_reason(
    conn: asyncpg.Connection, user: User, connection_id: uuid.UUID
) -> str | None:
    status = await conn.fetchval(
        "SELECT status FROM personal_credentials WHERE user_id = $1 AND connection_id = $2",
        user.id,
        connection_id,
    )
    if status is None or status == "a_saisir":
        return "credential_missing"
    if status != STATUS_VALID:
        return "credential_not_valid"
    return None


async def publish_readiness(
    pool: asyncpg.Pool,
    validation: Validation,
    conn: asyncpg.Connection,
    crypto: CryptoService,
    workdir: DraftWorkdir,
    user: User,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> Readiness:
    ready = Readiness()
    if "spaces:publish" not in await load_capabilities(conn, user, space_id):
        ready.reasons.append("no_publish_right")
    draft = await db_get_draft(conn, user.id, branch_id)
    if draft is None or draft.space_id != space_id:
        ready.reasons.append("empty_draft")
        return ready
    _d, objects, versions = await merge_context(conn, crypto, workdir, space_id, branch_id)
    ready.conflicts = [p.obj.path for p in pending_merges(objects, versions) if p.needs_decision]
    if ready.conflicts:
        ready.reasons.append("conflicts")
    _d2, _o, impacts = await draft_impacts(conn, crypto, workdir, space_id, branch_id)
    if any(not i.verified for i in impacts):
        ready.reasons.append("impacts_pending")
    # Règle d'or (ticket d7139b17) : toute erreur de la vue bloque, et rien ne se
    # publie tant que la vue a des fichiers à valider ou n'a pas été comparée au disque.
    _d3, log_entries = await draft_validation(
        conn, crypto, workdir, validation, space_id, branch_id
    )
    if blocking(log_entries):
        ready.reasons.append("validation_errors")
    summary = await db_view_summary(conn, ViewKey(space_id, branch_id, workdir.user_id))
    if summary is None or not summary.compared or summary.to_validate:
        ready.reasons.append("validation_pending")
    space = await conn.fetchrow("SELECT connection_id FROM git_spaces WHERE id = $1", space_id)
    reason = await _credential_reason(conn, user, space["connection_id"])
    if reason:
        ready.reasons.append(reason)
    if await _publish_in_progress(pool, branch_id):
        ready.reasons.append("publish_in_progress")
    return ready


@router.get("/readiness")
async def get_readiness(
    request: Request,
    conn: ConnDep,
    crypto: CryptoDep,
    _: SpacesRead,
    user: CurrentUser,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    ready = await publish_readiness(
        request.app.state.pool,
        request.app.state.validation,
        conn,
        crypto,
        workdir,
        user,
        space_id,
        branch_id,
    )
    return {"ok": ready.ok, "reasons": ready.reasons, "conflicts": ready.conflicts}


async def _automerge(
    conn: asyncpg.Connection,
    crypto: CryptoService,
    workdir: DraftWorkdir,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
) -> None:
    current = await db_get_draft(conn, workdir.user_id, branch_id)
    if current is None or current.space_id != space_id:
        return  # rien à fusionner : la vérification dira « brouillon vide »
    async with workdir.draft_lock(space_id, slug):
        await auto_merge(conn, crypto, workdir, space_id, branch_id)


def _decision_line(label: str, subject: str, agent: str | None) -> str:
    """Une décision du message de commit ; prise par un agent pour la personne,
    elle le dit."""
    return f"{label} : {subject}" + (f" (via l'agent {agent})" if agent else "")


def _message(comment: str, verified: list[tuple[str, str | None]]) -> str:
    """Message du commit. `verified` : (objet, agent ou None). Un avertissement ne
    s'accepte plus (ticket d7139b17) : il n'y figure pas."""
    lines = [f"{_COMMIT_PREFIX}{comment.strip()}"]
    decisions = [_decision_line("Impact vérifié", p, a) for p, a in verified]
    if decisions:
        lines += ["", *decisions]
    return "\n".join(lines) + "\n"


def _publish_error(exc: WorkdirError) -> ApiError:
    status = {
        "branch_changed_conflict": 409,
        "publish_credential_rejected": 403,
        "branch_not_found": 404,
        "repo_access_lost": 503,
    }.get(exc.code, 502)
    return ApiError(exc.code, status_code=status)


@router.post("/publish")
async def publish_draft(
    request: Request,
    conn: ConnDep,
    crypto: CryptoDep,
    _: SpacesRead,
    user: CurrentUser,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: PublishBody,
) -> dict[str, object]:
    if not body.comment.strip():
        raise ApiError("invalid_request", status_code=400, fields=["comment_required"])
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as lock_conn:
        got = await lock_conn.fetchval(
            "SELECT pg_try_advisory_lock(hashtextextended($1, 0))", _lock_key(branch_id)
        )
        if not got:
            raise ApiError("publish_in_progress", status_code=409)
        try:
            return await _publish_locked(
                request, conn, crypto, workdir, user, space_id, branch_id, body.comment
            )
        finally:
            await lock_conn.execute(
                "SELECT pg_advisory_unlock(hashtextextended($1, 0))", _lock_key(branch_id)
            )


async def _publish_locked(
    request: Request,
    conn: asyncpg.Connection,
    crypto: CryptoService,
    workdir: DraftWorkdir,
    user: User,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    comment: str,
) -> dict[str, object]:
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    await _automerge(conn, crypto, workdir, space_id, branch_id, branch.git_ref)
    ready = await publish_readiness(
        request.app.state.pool,
        request.app.state.validation,
        conn,
        crypto,
        workdir,
        user,
        space_id,
        branch_id,
    )
    # Le verrou est le nôtre : « publication en cours » ne bloque pas ici.
    reasons = [r for r in ready.reasons if r != "publish_in_progress"]
    if reasons:
        raise ApiError("publish_not_ready", status_code=409, reasons=reasons)
    draft = await db_get_draft(conn, user.id, branch_id)
    assert draft is not None
    objects = await db_list_objects(conn, draft.id)
    _d, _o, impacts = await draft_impacts(conn, crypto, workdir, space_id, branch_id)
    space, read_username, read_token = await space_credentials(conn, crypto, space_id)
    connection = await db_get_connection(conn, space.connection_id)
    token = await resolve_personal_token(conn, crypto, user.id, space.connection_id)
    if connection is None or token is None:
        raise ApiError("publish_not_ready", status_code=409, reasons=["credential_not_valid"])
    impact_agents = await db_decision_agents(conn, draft.id, "impact_verified")
    message = _message(
        comment, [(i.path, impact_agents.get(i.path)) for i in impacts if i.verified]
    )
    try:
        sha = await workdir.manager.push_objects(
            space_id,
            branch.git_ref,
            space.repo_url,
            read_username,
            read_token,
            get_strategy(connection.forge).git_username(),
            token.reveal(),
            [PublishObject(o.path, o.base_blob, o.content) for o in objects],
            user.display_name or user.email,
            user.email,
            message,
        )
    except WorkdirError as exc:
        raise _publish_error(exc) from None
    # Ne retirer du brouillon que ce qui vient d'être publié tel quel : une
    # écriture faite pendant le push (collecte, autre onglet) reste au brouillon.
    async with workdir.draft_lock(space_id, branch.git_ref):
        emptied = await db_remove_published(
            conn,
            draft.id,
            [
                (o.path, o.content, git_blob_sha(o.content) if o.content is not None else None)
                for o in objects
            ],
        )
    if emptied:
        await workdir.discard_view(space_id, branch.git_ref)
    log.info("draft_publication_done", space_id=str(space_id), objects=len(objects))
    # La branche publiée a changé : une passe de validation est demandée pour elle
    # (enabler 6b171b74).
    await request_pass(conn, ViewKey(space_id, branch_id), PUBLISH)
    return {"commit": sha, "published": len(objects)}
