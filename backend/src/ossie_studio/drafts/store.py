"""Stockage du brouillon personnel : seulement les objets modifiés.

Règles (feature « Brouillon personnel ») :
- l'empreinte de base d'un objet est figée à sa **première** modification ;
- un objet revenu à son contenu de base sort du brouillon (géré par l'appelant,
  qui ne remonte que les objets qui diffèrent de la tête publiée) ;
- un brouillon vide disparaît.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath

import asyncpg
import structlog

log = structlog.get_logger(__name__)

# Au-delà, un objet n'est pas stocké : refus nommé plutôt qu'une base qui enfle.
DRAFT_OBJECT_MAX_BYTES = 20 * 1024 * 1024


class DraftPathError(Exception):
    """Chemin hors du dépôt (absolu, `..`, `.git/`)."""


class DraftObjectTooLargeError(Exception):
    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.path = path


@dataclass(frozen=True)
class DraftObject:
    path: str
    base_blob: str | None  # None = créé par le brouillon
    content: bytes | None  # None = supprimé par le brouillon

    @property
    def state(self) -> str:
        if self.content is None:
            return "deleted"
        return "created" if self.base_blob is None else "modified"


@dataclass(frozen=True)
class DraftChange:
    """Un objet qui diffère de la tête publiée, tel que vu dans la vue de
    l'utilisateur après une écriture. `current_blob` : blob du même chemin à la
    tête publiée (None s'il n'y existe pas)."""

    path: str
    current_blob: str | None
    content: bytes | None


@dataclass(frozen=True)
class Draft:
    id: uuid.UUID
    user_id: uuid.UUID
    space_id: uuid.UUID
    branch_id: uuid.UUID
    base_commit: str
    revision: int
    updated_at: datetime

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> Draft:
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            space_id=row["space_id"],
            branch_id=row["branch_id"],
            base_commit=row["base_commit"],
            revision=row["revision"],
            updated_at=row["updated_at"],
        )


def validate_draft_path(path: str) -> str:
    """Un chemin relatif au dépôt, sans remontée ni `.git` ; sinon DraftPathError."""
    if not path or path.startswith("/") or "\\" in path or "//" in path:
        raise DraftPathError(path)
    parts = PurePosixPath(path).parts
    if any(part in ("..", ".", "") for part in parts) or parts[0] == ".git":
        raise DraftPathError(path)
    return path


async def db_get_draft(
    conn: asyncpg.Connection, user_id: uuid.UUID, branch_id: uuid.UUID
) -> Draft | None:
    row = await conn.fetchrow(
        "SELECT * FROM drafts WHERE user_id = $1 AND branch_id = $2", user_id, branch_id
    )
    return Draft.from_row(row) if row is not None else None


async def db_list_objects(conn: asyncpg.Connection, draft_id: uuid.UUID) -> list[DraftObject]:
    rows = await conn.fetch(
        "SELECT path, base_blob, content FROM draft_objects WHERE draft_id = $1 ORDER BY path",
        draft_id,
    )
    return [
        DraftObject(
            path=r["path"],
            base_blob=r["base_blob"],
            content=bytes(r["content"]) if r["content"] is not None else None,
        )
        for r in rows
    ]


async def db_replace_draft(
    conn: asyncpg.Connection,
    *,
    user_id: uuid.UUID,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    head_commit: str,
    changes: list[DraftChange],
) -> Draft | None:
    """Remplace le contenu du brouillon par `changes` (tous les objets qui
    diffèrent de la tête publiée après l'écriture). Garde l'empreinte de base
    d'un objet déjà présent ; fige celle d'un nouvel objet sur la tête publiée.
    Rend le brouillon, ou None s'il est désormais vide (et supprimé)."""
    for change in changes:
        validate_draft_path(change.path)
        if change.content is not None and len(change.content) > DRAFT_OBJECT_MAX_BYTES:
            raise DraftObjectTooLargeError(change.path)
    async with conn.transaction():
        draft = await db_get_draft(conn, user_id, branch_id)
        if not changes:
            if draft is not None:
                await conn.execute("DELETE FROM drafts WHERE id = $1", draft.id)
            return None
        if draft is None:
            row = await conn.fetchrow(
                "INSERT INTO drafts (user_id, space_id, branch_id, base_commit) "
                "VALUES ($1, $2, $3, $4) RETURNING *",
                user_id,
                space_id,
                branch_id,
                head_commit,
            )
            draft = Draft.from_row(row)
            known: dict[str, str | None] = {}
        else:
            rows = await conn.fetch(
                "SELECT path, base_blob FROM draft_objects WHERE draft_id = $1", draft.id
            )
            known = {r["path"]: r["base_blob"] for r in rows}
        wanted = {c.path for c in changes}
        gone = [path for path in known if path not in wanted]
        if gone:
            await conn.execute(
                "DELETE FROM draft_objects WHERE draft_id = $1 AND path = ANY($2::text[])",
                draft.id,
                gone,
            )
        for change in changes:
            base = known[change.path] if change.path in known else change.current_blob
            if change.content is None and base is None:
                # Créé puis supprimé dans le brouillon : rien à garder.
                await conn.execute(
                    "DELETE FROM draft_objects WHERE draft_id = $1 AND path = $2",
                    draft.id,
                    change.path,
                )
                continue
            await conn.execute(
                "INSERT INTO draft_objects (draft_id, path, base_blob, content) "
                "VALUES ($1, $2, $3, $4) ON CONFLICT (draft_id, path) "
                "DO UPDATE SET content = EXCLUDED.content",
                draft.id,
                change.path,
                base,
                change.content,
            )
        if not await conn.fetchval("SELECT 1 FROM draft_objects WHERE draft_id = $1", draft.id):
            await conn.execute("DELETE FROM drafts WHERE id = $1", draft.id)
            return None
        row = await conn.fetchrow(
            "UPDATE drafts SET revision = revision + 1, updated_at = now() WHERE id = $1 "
            "RETURNING *",
            draft.id,
        )
    log.info("draft_updated", draft_id=str(draft.id), objects=len(changes))
    return Draft.from_row(row)


async def db_rebase_object(
    conn: asyncpg.Connection,
    draft_id: uuid.UUID,
    path: str,
    new_base_blob: str | None,
    content: bytes | None,
) -> None:
    """Après une fusion : nouvelle base (version publiée actuelle) et contenu
    réconcilié. Contenu None + base None : l'objet sort du brouillon."""
    async with conn.transaction():
        if content is None and new_base_blob is None:
            await conn.execute(
                "DELETE FROM draft_objects WHERE draft_id = $1 AND path = $2", draft_id, path
            )
        else:
            await conn.execute(
                "INSERT INTO draft_objects (draft_id, path, base_blob, content) "
                "VALUES ($1, $2, $3, $4) ON CONFLICT (draft_id, path) "
                "DO UPDATE SET base_blob = EXCLUDED.base_blob, content = EXCLUDED.content",
                draft_id,
                path,
                new_base_blob,
                content,
            )
        await conn.execute(
            "UPDATE drafts SET revision = revision + 1, updated_at = now() WHERE id = $1",
            draft_id,
        )


async def db_delete_draft(conn: asyncpg.Connection, draft_id: uuid.UUID) -> None:
    await conn.execute("DELETE FROM drafts WHERE id = $1", draft_id)
