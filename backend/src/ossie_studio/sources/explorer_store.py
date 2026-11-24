"""Index de l'explorateur raw en base (table `raw_explorer_index`).

Cache dérivé de Git : une ligne par source, pour un utilisateur, une branche et
une vue (fusionnée avec son brouillon, ou publiée). La signature dit de quel
état il a été tiré ; une signature différente impose de le reconstruire.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

import asyncpg

from ossie_studio.sources.explorer import SourceEntries

# Ligne témoin d'une branche sans source : l'index existe, il est vide.
EMPTY_MARKER = ""


@dataclass(frozen=True)
class IndexKey:
    space_id: uuid.UUID
    branch_id: uuid.UUID
    user_id: uuid.UUID
    view: str  # merged | published


@dataclass(frozen=True)
class IndexedSource:
    slug: str
    label: str
    type: str
    collected: bool
    objects: int
    documents: int


_KEY_WHERE = "space_id = $1 AND branch_id = $2 AND user_id = $3 AND view = $4"


def _key_args(key: IndexKey) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, str]:
    return (key.space_id, key.branch_id, key.user_id, key.view)


async def db_index_signature(conn: asyncpg.Connection, key: IndexKey) -> str | None:
    """Signature de l'index (toutes ses lignes la partagent), ou None s'il n'existe pas."""
    value = await conn.fetchval(
        f"SELECT signature FROM raw_explorer_index WHERE {_KEY_WHERE} LIMIT 1",  # noqa: S608
        *_key_args(key),
    )
    return str(value) if value is not None else None


async def db_list_indexed_sources(conn: asyncpg.Connection, key: IndexKey) -> list[IndexedSource]:
    rows = await conn.fetch(
        "SELECT source_slug, label, source_type, collected, objects, documents "  # noqa: S608
        f"FROM raw_explorer_index WHERE {_KEY_WHERE} AND source_slug <> '' "
        "ORDER BY source_slug",
        *_key_args(key),
    )
    return [
        IndexedSource(
            slug=r["source_slug"],
            label=r["label"],
            type=r["source_type"],
            collected=r["collected"],
            objects=r["objects"],
            documents=r["documents"],
        )
        for r in rows
    ]


async def db_get_source_entries(
    conn: asyncpg.Connection, key: IndexKey, slug: str
) -> SourceEntries | None:
    row = await conn.fetchrow(
        "SELECT label, source_type, collected, entries FROM raw_explorer_index "  # noqa: S608
        f"WHERE {_KEY_WHERE} AND source_slug = $5",
        *_key_args(key),
        slug,
    )
    if row is None:
        return None
    data = row["entries"]
    if isinstance(data, str):
        data = json.loads(data)
    return SourceEntries.from_json(slug, row["label"], row["source_type"], row["collected"], data)


async def db_replace_index(
    conn: asyncpg.Connection, key: IndexKey, signature: str, sources: list[SourceEntries]
) -> None:
    """Remplace tout l'index de la clé, en une transaction (jamais à moitié)."""
    async with conn.transaction():
        await conn.execute(
            f"DELETE FROM raw_explorer_index WHERE {_KEY_WHERE}",  # noqa: S608
            *_key_args(key),
        )
        rows = [
            (
                *_key_args(key),
                s.slug,
                signature,
                s.label,
                s.type,
                s.collected,
                len(s.objects),
                # Fichiers hors objets : documents et contrats complets (ticket
                # e79d008f), comptés ensemble pour ne pas ajouter de colonne.
                len(s.documents) + len(s.contracts),
                json.dumps(s.to_json(), ensure_ascii=False),
            )
            for s in sources
        ] or [(*_key_args(key), EMPTY_MARKER, signature, "", "", False, 0, 0, "{}")]
        await conn.executemany(
            "INSERT INTO raw_explorer_index (space_id, branch_id, user_id, view, source_slug, "
            "signature, label, source_type, collected, objects, documents, entries) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb)",
            rows,
        )


async def db_all_source_entries(conn: asyncpg.Connection, key: IndexKey) -> list[SourceEntries]:
    """Toutes les sources de l'index (résolution des références dans l'espace)."""
    rows = await conn.fetch(
        "SELECT source_slug, label, source_type, collected, entries "  # noqa: S608
        f"FROM raw_explorer_index WHERE {_KEY_WHERE} AND source_slug <> '' "
        "ORDER BY source_slug",
        *_key_args(key),
    )
    out: list[SourceEntries] = []
    for r in rows:
        data = r["entries"]
        if isinstance(data, str):
            data = json.loads(data)
        out.append(
            SourceEntries.from_json(
                r["source_slug"], r["label"], r["source_type"], r["collected"], data
            )
        )
    return out
