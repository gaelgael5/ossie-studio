"""Journal d'audit transverse (ticket docflow cf4434da).

Gael, 18/11/2026 : « une brique d'audit générale, visible dans une page
Historique ». Un seul mécanisme pour tout l'état de configuration tenu en base ;
ajouter un point d'audit, c'est entourer l'écriture d'un `async with audited(...)`.

**Atomicité** : `audited` ouvre une transaction ; la ligne s'écrit à sa sortie,
dans la même transaction que l'écriture métier. Si l'action lève, la transaction
est annulée et aucune ligne n'est écrite ; si l'écriture de la ligne échoue,
l'action est annulée avec elle. Jamais de demi-trace ni de trace sans acte.

**Jamais de secret** : le détail libre n'accepte que des valeurs simples, et
toute clé dont le nom évoque une valeur sensible est **refusée** (l'action est
alors annulée) plutôt que filtrée en silence — un journal est précisément
l'endroit où un secret fuit par mégarde.

La gouvernance n'écrit rien ici : elle vit dans Git, et son historique est lu
dans les commits publiés (décision de Gael, 23/11/2026 ; voir `git_history`).
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import asyncpg

from ossie_studio.models import User

CREATED = "created"
UPDATED = "updated"
DELETED = "deleted"
ACTIONS = (CREATED, UPDATED, DELETED)

SYSTEM_LABEL = "system"

# Noms de clés interdits dans le détail (sous-chaîne, insensible à la casse).
_SENSITIVE = re.compile(
    r"secret|token|password|passwd|passphrase|private|credential|value|cipher|key_material",
    re.IGNORECASE,
)


class SensitiveDetailError(ValueError):
    """Le détail d'audit contient une clé sensible : l'écriture est refusée."""


def actor_label(user: User | None) -> str:
    """Empreinte lisible de l'auteur, figée dans la ligne (elle reste lisible si
    le compte est supprimé). `None` : le système."""
    if user is None:
        return SYSTEM_LABEL
    name = user.display_name.strip()
    return f"{name} <{user.email}>" if name else user.email


def clean_detail(detail: dict[str, Any] | None) -> dict[str, Any]:
    """Détail à stocker : valeurs simples (texte, nombre, booléen, liste de
    textes), aucune clé sensible. Lève SensitiveDetailError sinon."""
    out: dict[str, Any] = {}
    for key, value in (detail or {}).items():
        if _SENSITIVE.search(key):
            raise SensitiveDetailError(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        elif isinstance(value, list) and all(isinstance(v, str) for v in value):
            out[key] = list(value)
        else:
            raise SensitiveDetailError(key)  # forme non prévue : refusée
    return out


@dataclass
class AuditEntry:
    """Ligne en préparation : la route complète la cible une fois l'objet écrit
    (identifiant attribué par la base, libellé définitif)."""

    action: str
    object_type: str
    object_id: str = ""
    object_label: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    skip: bool = False  # rien n'a changé : pas de ligne

    def target(self, object_id: object, label: str = "") -> None:
        self.object_id = str(object_id)
        if label:
            self.object_label = label


async def record(conn: asyncpg.Connection, actor: User | None, entry: AuditEntry) -> None:
    if entry.action not in ACTIONS:
        raise ValueError(f"action d'audit inconnue : {entry.action}")
    if not entry.object_id:
        raise ValueError("ligne d'audit sans identifiant d'objet")
    await conn.execute(
        """
        INSERT INTO audit_log
            (actor_id, actor_label, action, object_type, object_id, object_label, detail)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
        """,
        actor.id if actor is not None else None,
        actor_label(actor),
        entry.action,
        entry.object_type,
        entry.object_id,
        entry.object_label,
        # Codec jsonb du pool (db/__init__.py) : le dict est encodé par lui.
        clean_detail(entry.detail),
    )


@asynccontextmanager
async def audited(
    conn: asyncpg.Connection,
    actor: User | None,
    action: str,
    object_type: str,
    object_id: object = "",
    label: str = "",
    detail: dict[str, Any] | None = None,
) -> AsyncIterator[AuditEntry]:
    """Écriture métier et ligne d'audit dans une même transaction (voir le
    module). La route peut compléter `entry` (cible, détail) dans le bloc."""
    entry = AuditEntry(
        action=action,
        object_type=object_type,
        object_id=str(object_id) if object_id != "" else "",
        object_label=label,
        detail=dict(detail or {}),
    )
    async with conn.transaction():
        yield entry
        if not entry.skip:
            await record(conn, actor, entry)


async def audited_secret_removal(
    conn: asyncpg.Connection,
    actor: User | None,
    slug: str,
    remove: Callable[[], Awaitable[str]],
) -> str:
    """Suppression d'un secret à la suite d'un autre objet (connexion,
    connecteur OIDC) : un acte à part, journalisé après celui-ci, seulement si
    le secret est réellement parti (`remove` rend « deleted »)."""
    async with audited(conn, actor, DELETED, "secret", slug) as entry:
        outcome = await remove()
        entry.skip = outcome != "deleted"
    return outcome


# --- Lecture -------------------------------------------------------------------


@dataclass(frozen=True)
class AuditEvent:
    at: datetime
    actor_label: str
    actor_id: uuid.UUID | None
    action: str
    object_type: str
    object_id: str
    object_label: str
    detail: dict[str, Any]
    origin: str  # "db" (table d'audit) ou "git" (commit publié)
    ref: str = ""  # identifiant de la ligne, ou sha du commit


@dataclass(frozen=True)
class AuditFilters:
    object_type: str = ""
    actor: str = ""  # sous-chaîne de l'empreinte de l'auteur
    since: datetime | None = None
    until: datetime | None = None
    before: datetime | None = None  # curseur : strictement avant


async def db_list_audit(
    conn: asyncpg.Connection, filters: AuditFilters, limit: int
) -> list[AuditEvent]:
    """Lignes d'audit du plus récent au plus ancien, filtrées, au plus `limit`."""
    # Requête fixe, filtres facultatifs en paramètres (jamais de SQL composé).
    rows = await conn.fetch(
        """
        SELECT id, at, actor_id, actor_label, action, object_type, object_id,
               object_label, detail
        FROM audit_log
        WHERE ($1 = '' OR object_type = $1)
          AND ($2 = '' OR actor_label ILIKE '%' || $2 || '%')
          AND ($3::timestamptz IS NULL OR at >= $3)
          AND ($4::timestamptz IS NULL OR at < $4)
          AND ($5::timestamptz IS NULL OR at < $5)
        ORDER BY at DESC, id DESC
        LIMIT $6
        """,
        filters.object_type,
        filters.actor,
        filters.since,
        filters.until,
        filters.before,
        limit,
    )
    return [
        AuditEvent(
            at=r["at"],
            actor_label=r["actor_label"],
            actor_id=r["actor_id"],
            action=r["action"],
            object_type=r["object_type"],
            object_id=r["object_id"],
            object_label=r["object_label"],
            detail=json.loads(r["detail"]) if isinstance(r["detail"], str) else dict(r["detail"]),
            origin="db",
            ref=str(r["id"]),
        )
        for r in rows
    ]
