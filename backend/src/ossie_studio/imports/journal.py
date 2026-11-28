"""Journal des imports (ticket « Importer des valeurs depuis un fichier » ;
module commun depuis l'enabler 4f32c8c6 : le type d'objet journalisé est celui
de la cible, `referentiel_import` pour un référentiel).

Chaque import est une ligne du journal d'audit transverse (`audit_log`) : son
auteur, le fichier, la stratégie de suppression et le compte rendu (nombres
seulement, jamais une valeur). Un import refusé y figure aussi. La **date du
dernier import** se lit là : le dernier import abouti (écrit, ou sans changement),
sans rien écrire dans les fichiers du référentiel — un même fichier importé deux
fois ne produit donc pas de second changement.

**Éléments gardés car utilisés** (ticket 6d36073b) : ceux qu'une exécution aurait dû
supprimer mais a gardés (encore utilisés) sont rattachés à sa ligne du journal
(`import_kept`), pour la page qui les explique (`read_run`).

L'objet journalisé est `<branche>/<référentiel>/<import>` : un import est
déclaré dans une branche suivie d'un espace.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg

from ossie_studio.audit.service import UPDATED, AuditEntry, record
from ossie_studio.models import User

# Libellé journalisé « <propriétaire> (séparateur) <import> » : celui des référentiels.
SEPARATOR = " \u203a "

IMPORTED = "imported"
UNCHANGED = "unchanged"
REFUSED = "refused"

MAX_FILE_NAME = 200
# Éléments gardés car utilisés conservés par exécution (au-delà : comptés seulement).
MAX_KEPT = 500
# Les nombres du compte rendu repris dans le journal.
_COUNTS = ("lines", "new", "modified", "unchanged", "deleted", "kept", "kept_in_use", "errors")


@dataclass(frozen=True)
class LastImport:
    at: str
    by: str


@dataclass(frozen=True)
class ImportRun:
    """Une exécution d'import, telle que le journal la garde."""

    id: int
    at: str
    by: str
    detail: dict[str, Any]
    kept: list[dict[str, Any]]


def object_id(branch_id: uuid.UUID, name: str, slug: str) -> str:
    return f"{branch_id}/{name}/{slug}"


async def record_import(
    conn: asyncpg.Connection,
    object_type: str,
    actor: User,
    branch_id: uuid.UUID,
    name: str,
    slug: str,
    *,
    outcome: str,
    file: str,
    deletion: str,
    result: dict[str, Any],
    revision: str | None = None,
    kept_in_use: list[dict[str, Any]] | None = None,
) -> int:
    """Journalise une exécution ; rend son identifiant (celui de sa page des
    éléments gardés car utilisés, s'il y en a)."""
    detail: dict[str, Any] = {
        "outcome": outcome,
        "deletion": deletion,
        "file": file[:MAX_FILE_NAME],
        **{name_: int(result.get(name_) or 0) for name_ in _COUNTS},
    }
    if revision:
        detail["revision"] = revision
    run_id = await record(
        conn,
        actor,
        AuditEntry(
            action=UPDATED,
            object_type=object_type,
            object_id=object_id(branch_id, name, slug),
            object_label=f"{name}{SEPARATOR}{slug}",
            detail=detail,
        ),
    )
    if kept_in_use:
        await conn.execute(
            "INSERT INTO import_kept (audit_id, items) VALUES ($1, $2::jsonb)",
            run_id,
            kept_in_use[:MAX_KEPT],
        )
    return run_id


async def read_run(
    conn: asyncpg.Connection,
    object_type: str,
    branch_id: uuid.UUID,
    name: str,
    slug: str,
    run_id: int,
) -> ImportRun | None:
    """Une exécution de cet import (None si l'identifiant n'en est pas une)."""
    row = await conn.fetchrow(
        """
        SELECT a.id, a.at, a.actor_label, a.detail, k.items
        FROM audit_log a
        LEFT JOIN import_kept k ON k.audit_id = a.id
        WHERE a.id = $1 AND a.object_type = $2 AND a.object_id = $3
        """,
        run_id,
        object_type,
        object_id(branch_id, name, slug),
    )
    if row is None:
        return None
    detail = row["detail"] if isinstance(row["detail"], dict) else {}
    kept = row["items"] if isinstance(row["items"], list) else []
    return ImportRun(row["id"], row["at"].isoformat(), row["actor_label"], detail, kept)


async def last_imports(
    conn: asyncpg.Connection, object_type: str, branch_id: uuid.UUID, name: str
) -> dict[str, LastImport]:
    """Le dernier import abouti de chaque import de ce propriétaire (un
    référentiel, un type de cartographie), par slug."""
    prefix = object_id(branch_id, name, "")
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (object_id) object_id, at, actor_label
        FROM audit_log
        WHERE object_type = $1
          AND left(object_id, length($2)) = $2
          AND detail->>'outcome' <> $3
        ORDER BY object_id, at DESC
        """,
        object_type,
        prefix,
        REFUSED,
    )
    return {
        r["object_id"][len(prefix) :]: LastImport(r["at"].isoformat(), r["actor_label"])
        for r in rows
    }
