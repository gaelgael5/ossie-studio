"""Page Historique : lecture du journal d'audit (ticket docflow cf4434da).

Deux origines fusionnées, du plus récent au plus ancien :
- la table d'audit (état de configuration en base) ;
- les commits publiés qui touchent `governance/` de l'espace et de la branche
  regardés (décision de Gael, 23/11/2026), quand `space_id` et `branch_id` sont
  donnés.

Lecture seule : aucune route ne modifie ni ne supprime une ligne. Capacité
`audit:read`.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from ossie_studio.api.branches import space_credentials
from ossie_studio.api.common import AuditRead
from ossie_studio.audit.git_history import (
    GOVERNANCE_TYPES,
    REFERENTIEL_TYPES,
    governance_events,
    referentiel_events,
)
from ossie_studio.audit.service import AuditEvent, AuditFilters, db_list_audit
from ossie_studio.crypto import CryptoService
from ossie_studio.deps import ConnDep, get_crypto
from ossie_studio.errors import ApiError
from ossie_studio.governance.repo import GOVERNANCE_DIR
from ossie_studio.referentiels.repo import REFERENTIELS_DIR
from ossie_studio.spaces.branch_service import db_get_branch
from ossie_studio.spaces.workdir import WorkdirError, WorkdirManager

router = APIRouter(prefix="/api/audit")

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]

PAGE_MAX = 200
# Commits de gouvernance relus pour une page : l'historique publié récent.
GIT_LOG_LIMIT = 500

# Types d'objets tenus en base (les points d'audit des routes).
DB_TYPES = (
    "secret",
    "connection",
    "db_connection",
    "oidc_connector",
    "profile",
    "agent_profile",
    "api_key",
    "team",
    "user",
)
# Types d'objets tenus dans Git : leur historique est celui des commits publiés.
GIT_TYPES = (*GOVERNANCE_TYPES, *REFERENTIEL_TYPES)


def _payload(event: AuditEvent) -> dict[str, object]:
    data = asdict(event)
    data["at"] = event.at.isoformat()
    data["actor_id"] = str(event.actor_id) if event.actor_id else None
    return data


def _page(events: list[AuditEvent], limit: int) -> tuple[list[AuditEvent], bool]:
    """Les `limit` premiers, prolongés des suivants de même instant : un commit
    qui touche plusieurs fichiers n'est jamais coupé entre deux pages (le
    curseur est « strictement avant »)."""
    if len(events) <= limit:
        return events, False
    cut = limit
    while cut < len(events) and events[cut].at == events[limit - 1].at:
        cut += 1
    return events[:cut], cut < len(events)


@router.get("")
async def list_audit(
    conn: ConnDep,
    crypto: CryptoDep,
    request: Request,
    _: AuditRead,
    object_type: Annotated[str, Query(alias="type")] = "",
    actor: str = "",
    since: datetime | None = None,
    until: datetime | None = None,
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=PAGE_MAX)] = 50,
    space_id: uuid.UUID | None = None,
    branch_id: uuid.UUID | None = None,
) -> dict[str, object]:
    filters = AuditFilters(
        object_type=object_type, actor=actor.strip(), since=since, until=until, before=before
    )
    # Une ligne de plus que la page : savoir s'il en reste.
    events: list[AuditEvent] = []
    if object_type not in GIT_TYPES:
        events = await db_list_audit(conn, filters, limit + 1)
    if space_id is not None and branch_id is not None and object_type not in DB_TYPES:
        branch = await db_get_branch(conn, branch_id)
        if branch is None or branch.space_id != space_id:
            raise ApiError("branch_not_found", status_code=404)
        space, username, token = await space_credentials(conn, crypto, space_id)
        try:
            # Historique publié seulement (un brouillon n'a pas d'histoire) : la
            # copie partagée de la branche, sans passer par le brouillon.
            manager: WorkdirManager = request.app.state.workdir_manager
            commits = await manager.path_log(
                space.id,
                branch.git_ref,
                space.repo_url,
                username,
                token,
                GOVERNANCE_DIR,
                GIT_LOG_LIMIT,
            )
            ref_commits = await manager.path_log(
                space.id,
                branch.git_ref,
                space.repo_url,
                username,
                token,
                REFERENTIELS_DIR,
                GIT_LOG_LIMIT,
            )
        except WorkdirError as exc:
            raise ApiError(exc.code, status_code=502) from None
        events = [
            *events,
            *governance_events(commits, filters),
            *referentiel_events(ref_commits, filters),
        ]
    events.sort(key=lambda e: e.at, reverse=True)
    page, more = _page(events, limit)
    return {
        "events": [_payload(e) for e in page],
        "next": page[-1].at.isoformat() if more and page else None,
        "types": [*DB_TYPES, *GIT_TYPES],
    }
