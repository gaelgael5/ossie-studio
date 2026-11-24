"""Journal des imports de valeurs d'un référentiel (ticket « Importer des valeurs
depuis un fichier »).

Chaque import est une ligne du journal d'audit transverse (`audit_log`) : son
auteur, le fichier, le mode et le compte rendu (nombres seulement, jamais une
valeur). Un import refusé y figure aussi. La **date du dernier import** se lit
là : le dernier import abouti (écrit, ou sans changement), sans rien écrire
dans les fichiers du référentiel — un même fichier importé deux fois ne
produit donc pas de second changement.

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
from ossie_studio.referentiels.model import SEPARATOR

OBJECT_TYPE = "referentiel_import"

IMPORTED = "imported"
UNCHANGED = "unchanged"
REFUSED = "refused"

MAX_FILE_NAME = 200
# Les nombres du compte rendu repris dans le journal.
_COUNTS = ("lines", "new", "modified", "unchanged", "deleted", "errors")


@dataclass(frozen=True)
class LastImport:
    at: str
    by: str


def object_id(branch_id: uuid.UUID, name: str, slug: str) -> str:
    return f"{branch_id}/{name}/{slug}"


async def record_import(
    conn: asyncpg.Connection,
    actor: User,
    branch_id: uuid.UUID,
    name: str,
    slug: str,
    *,
    outcome: str,
    file: str,
    mode: str,
    result: dict[str, Any],
    revision: str | None = None,
) -> None:
    detail: dict[str, Any] = {
        "outcome": outcome,
        "mode": mode,
        "file": file[:MAX_FILE_NAME],
        **{name_: int(result.get(name_) or 0) for name_ in _COUNTS},
    }
    if revision:
        detail["revision"] = revision
    await record(
        conn,
        actor,
        AuditEntry(
            action=UPDATED,
            object_type=OBJECT_TYPE,
            object_id=object_id(branch_id, name, slug),
            object_label=f"{name}{SEPARATOR}{slug}",
            detail=detail,
        ),
    )


async def last_imports(
    conn: asyncpg.Connection, branch_id: uuid.UUID, name: str
) -> dict[str, LastImport]:
    """Le dernier import abouti de chaque import du référentiel, par slug."""
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
        OBJECT_TYPE,
        prefix,
        REFUSED,
    )
    return {
        r["object_id"][len(prefix) :]: LastImport(r["at"].isoformat(), r["actor_label"])
        for r in rows
    }
