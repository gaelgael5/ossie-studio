"""Brouillon de la personne connectée sur une branche : résumé et signal
« a changé côté publié » objet par objet."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from ossie_studio.api.branches import space_credentials
from ossie_studio.api.common import SpacesRead
from ossie_studio.crypto import CryptoService
from ossie_studio.deps import ConnDep, get_crypto, get_draft_workdir
from ossie_studio.drafts.store import DraftObject, db_get_draft, db_list_objects
from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.errors import ApiError
from ossie_studio.spaces.branch_service import db_get_branch
from ossie_studio.spaces.workdir import WorkdirError

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
