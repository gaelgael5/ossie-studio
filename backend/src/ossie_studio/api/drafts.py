"""Brouillon de la personne connectée sur une branche (résumé)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from ossie_studio.api.common import CurrentUser, SpacesRead
from ossie_studio.deps import ConnDep
from ossie_studio.drafts.store import db_get_draft, db_list_objects

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/draft")


@router.get("")
async def my_draft(
    conn: ConnDep, _: SpacesRead, user: CurrentUser, space_id: uuid.UUID, branch_id: uuid.UUID
) -> dict[str, object]:
    draft = await db_get_draft(conn, user.id, branch_id)
    if draft is None or draft.space_id != space_id:
        return {"count": 0, "revision": 0, "objects": []}
    objects = await db_list_objects(conn, draft.id)
    return {
        "count": len(objects),
        "revision": draft.revision,
        "objects": [{"path": o.path, "state": o.state} for o in objects],
    }
