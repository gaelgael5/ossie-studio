"""La recherche par l'index des documents (ticket 06171fd3, epic « Index des documents et
validation avant publication »).

Gael : l'index est « un outil de validation **et un outil de recherche** ». Ce module lit
l'index d'une vue (branche publiée, ou brouillon d'une personne) sans parcourir le
dépôt :
- `snapshot` : les documents de certains types, avec leur fichier et sa version
  (empreinte Git) — de quoi bâtir une liste (`list_documents`, écrans) ;
- `citing` : les fichiers qui citent une identité (recherche inverse sur les références
  sortantes) — « qui utilise ce document » ; `holding` : ceux qui la portent ;
- `find` : les documents d'un type dont l'identité contient une identité partielle
  (une clé de cartographie sans son type, par exemple).

Fraîcheur : le contenu indexé d'un fichier est mis à jour à l'enregistrement (avant
toute validation), et la vue est comparée de nouveau à Git quand la tête de la branche
a bougé (`DraftWorkdir.indexed_view`). La recherche est donc à jour même quand la
validation ne l'est pas encore.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from ossie_studio.doc_index.store import ViewKey


@dataclass(frozen=True)
class IndexedDoc:
    """Un document de la vue, tel que l'index le connaît."""

    path: str
    blob: str
    document_type: str
    identity: dict[str, Any]
    name: str
    fields: dict[str, Any] = field(default_factory=dict)


def _path_order(path: str) -> tuple[str, ...]:
    """L'ordre de `sorted(Path.rglob(...))` : par segments du chemin, octet par octet
    (l'ordre du texte en base dépend de sa collation)."""
    return tuple(path.split("/"))


@dataclass(frozen=True)
class Snapshot:
    """Les documents de quelques types dans une vue, dans l'ordre des chemins."""

    docs: tuple[IndexedDoc, ...] = ()

    def of(self, document_type: str) -> list[IndexedDoc]:
        return [d for d in self.docs if d.document_type == document_type]


def _json(raw: object) -> Any:
    return json.loads(raw) if isinstance(raw, str) else raw


async def _view_id(conn: asyncpg.Connection, key: ViewKey) -> int | None:
    found: int | None = await conn.fetchval(
        "SELECT id FROM doc_index_view "
        "WHERE space_id = $1 AND branch_id = $2 AND user_id IS NOT DISTINCT FROM $3",
        key.space_id,
        key.branch_id,
        key.user_id,
    )
    return found


async def snapshot(
    conn: asyncpg.Connection, key: ViewKey, document_types: Iterable[str]
) -> Snapshot:
    """Les documents des types demandés dans la vue `key`."""
    view_id = await _view_id(conn, key)
    if view_id is None:
        return Snapshot()
    rows = await conn.fetch(
        """
        SELECT f.path, c.blob, d.doc
        FROM doc_index_file f
        JOIN doc_index_content c ON c.id = f.content_id
        CROSS JOIN LATERAL jsonb_array_elements(c.documents) AS d(doc)
        WHERE f.view_id = $1 AND d.doc->>'document_type' = ANY($2::text[])
        """,
        view_id,
        sorted(set(document_types)),
    )
    docs = []
    for r in rows:
        doc = _json(r["doc"])
        docs.append(
            IndexedDoc(
                r["path"],
                r["blob"],
                str(doc.get("document_type") or ""),
                dict(doc.get("identity") or {}),
                str(doc.get("name") or ""),
                dict(doc.get("fields") or {}),
            )
        )
    docs.sort(key=lambda d: _path_order(d.path))
    return Snapshot(tuple(docs))


async def _files(
    conn: asyncpg.Connection, key: ViewKey, column: str, values: Iterable[str]
) -> dict[str, list[IndexedDoc]]:
    """Les fichiers de la vue dont `column` (`refs` ou `keys`) contient une des valeurs."""
    view_id = await _view_id(conn, key)
    wanted = sorted(set(values))
    if view_id is None or not wanted:
        return {}
    assert column in ("refs", "keys")
    rows = await conn.fetch(
        f"""
        SELECT f.path, c.blob, c.documents
        FROM doc_index_file f JOIN doc_index_content c ON c.id = f.content_id
        WHERE f.view_id = $1 AND c.{column} && $2::text[]
        """,  # noqa: S608 — colonne choisie parmi deux, valeurs en paramètre
        view_id,
        wanted,
    )
    out: dict[str, list[IndexedDoc]] = {}
    for r in sorted(rows, key=lambda r: _path_order(r["path"])):
        out[r["path"]] = [
            IndexedDoc(
                r["path"],
                r["blob"],
                str(d.get("document_type") or ""),
                dict(d.get("identity") or {}),
                str(d.get("name") or ""),
                dict(d.get("fields") or {}),
            )
            for d in _json(r["documents"]) or []
        ]
    return out


async def citing(
    conn: asyncpg.Connection, key: ViewKey, refs: Iterable[str]
) -> dict[str, list[IndexedDoc]]:
    """Les fichiers de la vue qui citent une de ces identités (clés de l'index, par
    exemple `carto_objet:application/APP-01`) : chemin → ses documents."""
    return await _files(conn, key, "refs", refs)


async def holding(conn: asyncpg.Connection, key: ViewKey, identity_key: str) -> list[IndexedDoc]:
    """Les documents des fichiers de la vue qui portent cette identité."""
    found = await _files(conn, key, "keys", [identity_key])
    return [doc for docs in found.values() for doc in docs]


async def find(
    conn: asyncpg.Connection, key: ViewKey, document_type: str, identity: Mapping[str, Any]
) -> list[IndexedDoc]:
    """Les documents du type dont l'identité contient `identity` (identité partielle)."""
    found = await snapshot(conn, key, [document_type])
    return [d for d in found.docs if all(d.identity.get(k) == v for k, v in identity.items())]
