"""Les tables de l'index des documents (migration 0054, enabler 15863091).

Fonctions d'accès seulement : le contenu (global par espace, par chemin et
empreinte), les vues (branche publiée, brouillon d'une personne) et leurs fichiers.
Ce qu'on en fait est dans `service.py`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import asyncpg

from ossie_studio.doc_index.extract import INDEXER_VERSION, IndexedFile

A_VALIDER = "a_valider"
VALIDE = "valide"
EN_ERREUR = "en_erreur"


@dataclass(frozen=True)
class ViewKey:
    """Une vue : la branche publiée (`user_id` None) ou le brouillon d'une personne."""

    space_id: uuid.UUID
    branch_id: uuid.UUID
    user_id: uuid.UUID | None = None

    @property
    def published(self) -> ViewKey:
        return ViewKey(self.space_id, self.branch_id)


@dataclass(frozen=True)
class View:
    id: int
    key: ViewKey
    head: str | None
    compared_at: datetime | None

    @property
    def compared(self) -> bool:
        return self.compared_at is not None


@dataclass(frozen=True)
class IndexedRow:
    """Un fichier d'une vue, avec ce que l'index sait de son contenu."""

    path: str
    blob: str
    content_id: int
    status: str
    keys: list[str]
    refs: list[str]
    documents: list[dict[str, object]]


def _view(row: asyncpg.Record, key: ViewKey) -> View:
    return View(row["id"], key, row["head"], row["compared_at"])


async def db_get_view(conn: asyncpg.Connection, key: ViewKey) -> View | None:
    row = await conn.fetchrow(
        "SELECT id, head, compared_at FROM doc_index_view "
        "WHERE space_id = $1 AND branch_id = $2 AND user_id IS NOT DISTINCT FROM $3",
        key.space_id,
        key.branch_id,
        key.user_id,
    )
    return _view(row, key) if row is not None else None


async def db_ensure_view(conn: asyncpg.Connection, key: ViewKey) -> View:
    row = await conn.fetchrow(
        """
        INSERT INTO doc_index_view (space_id, branch_id, user_id) VALUES ($1, $2, $3)
        ON CONFLICT (space_id, branch_id, user_id) DO UPDATE SET space_id = EXCLUDED.space_id
        RETURNING id, head, compared_at
        """,
        key.space_id,
        key.branch_id,
        key.user_id,
    )
    assert row is not None
    return _view(row, key)


async def db_mark_compared(
    conn: asyncpg.Connection,
    view_id: int,
    head: str | None,
    disk_files: int | None = None,
    detected: Iterable[str] = (),
) -> None:
    """La vue est d'accord avec le disque sur `head`. `disk_files` : les fichiers sur
    le disque (par défaut, ceux de la vue) ; `detected` : ceux que la comparaison a
    trouvés nouveaux ou changés, c'est-à-dire modifiés hors de l'application
    (ticket 48618517)."""
    await conn.execute(
        """
        UPDATE doc_index_view SET head = $2, compared_at = now(),
            disk_files = coalesce($3, (SELECT count(*) FROM doc_index_file WHERE view_id = $1)),
            detected = $4::text[]
        WHERE id = $1
        """,
        view_id,
        head,
        disk_files,
        sorted(detected),
    )


async def db_reset_view(conn: asyncpg.Connection, view_id: int) -> None:
    """La vue redevient « jamais comparée », sans fichier."""
    await conn.execute("DELETE FROM doc_index_file WHERE view_id = $1", view_id)
    await conn.execute(
        "UPDATE doc_index_view SET head = NULL, compared_at = NULL WHERE id = $1", view_id
    )


# --- Contenu ---------------------------------------------------------------------------------


async def db_find_contents(
    conn: asyncpg.Connection, space_id: uuid.UUID, entries: Iterable[tuple[str, str]]
) -> dict[tuple[str, str], tuple[int, list[str]]]:
    """Les contenus déjà indexés (par la version courante de l'extraction) parmi ces
    (chemin, empreinte) : leur identifiant et leurs clés."""
    pairs = list(entries)
    if not pairs:
        return {}
    rows = await conn.fetch(
        """
        SELECT c.id, c.path, c.blob, c.keys
        FROM doc_index_content c
        JOIN unnest($2::text[], $3::text[]) AS e(path, blob)
          ON c.path = e.path AND c.blob = e.blob
        WHERE c.space_id = $1 AND c.indexer_version = $4
        """,
        space_id,
        [p for p, _b in pairs],
        [b for _p, b in pairs],
        INDEXER_VERSION,
    )
    return {(r["path"], r["blob"]): (r["id"], list(r["keys"])) for r in rows}


async def db_put_content(
    conn: asyncpg.Connection, space_id: uuid.UUID, path: str, blob: str, indexed: IndexedFile
) -> int:
    """Range le contenu indexé ; une extraction plus ancienne est remplacée (et sa
    validation oubliée : elle se recalcule)."""
    content_id = await conn.fetchval(
        """
        INSERT INTO doc_index_content
            (space_id, path, blob, indexer_version, documents, keys, refs)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
        ON CONFLICT (space_id, path, blob) DO UPDATE SET
            indexer_version = EXCLUDED.indexer_version,
            documents = EXCLUDED.documents,
            keys = EXCLUDED.keys,
            refs = EXCLUDED.refs,
            problems = NULL,
            rules_version = NULL,
            indexed_at = now()
        RETURNING id
        """,
        space_id,
        path,
        blob,
        INDEXER_VERSION,
        json.dumps([d.payload() for d in indexed.documents], ensure_ascii=False),
        indexed.keys,
        indexed.refs,
    )
    assert isinstance(content_id, int)
    return content_id


async def db_purge_contents(conn: asyncpg.Connection, space_id: uuid.UUID) -> int:
    """Retire les contenus qu'aucune vue ne contient plus ; rend leur nombre."""
    status = await conn.execute(
        """
        DELETE FROM doc_index_content c
        WHERE c.space_id = $1
          AND NOT EXISTS (SELECT 1 FROM doc_index_file f WHERE f.content_id = c.id)
        """,
        space_id,
    )
    return int(status.split()[-1])


async def db_clear_space(conn: asyncpg.Connection, space_id: uuid.UUID) -> None:
    """Vide l'index d'un espace (vues, fichiers, contenus)."""
    await conn.execute("DELETE FROM doc_index_view WHERE space_id = $1", space_id)
    await conn.execute("DELETE FROM doc_index_content WHERE space_id = $1", space_id)


# --- Fichiers d'une vue ----------------------------------------------------------------------


async def db_view_files(
    conn: asyncpg.Connection, view_id: int, paths: Iterable[str] | None = None
) -> dict[str, IndexedRow]:
    """Les fichiers de la vue (seulement ceux de `paths` si donnés)."""
    only = list(paths) if paths is not None else None
    rows = await conn.fetch(
        """
        SELECT f.path, c.blob, f.content_id, f.status, c.keys, c.refs, c.documents
        FROM doc_index_file f JOIN doc_index_content c ON c.id = f.content_id
        WHERE f.view_id = $1 AND ($2::text[] IS NULL OR f.path = ANY($2::text[]))
        """,
        view_id,
        only,
    )
    return {
        r["path"]: IndexedRow(
            r["path"],
            r["blob"],
            r["content_id"],
            r["status"],
            list(r["keys"]),
            list(r["refs"]),
            json.loads(r["documents"]) if isinstance(r["documents"], str) else r["documents"],
        )
        for r in rows
    }


async def db_set_files(conn: asyncpg.Connection, view_id: int, rows: dict[str, int]) -> None:
    """Pose (chemin → contenu) dans la vue ; chacun passe `a_valider`."""
    if not rows:
        return
    await conn.execute(
        """
        INSERT INTO doc_index_file (view_id, path, content_id, status, problems)
        SELECT $1, e.path, e.content_id, 'a_valider', '[]'::jsonb
        FROM unnest($2::text[], $3::bigint[]) AS e(path, content_id)
        ON CONFLICT (view_id, path) DO UPDATE SET
            content_id = EXCLUDED.content_id,
            status = 'a_valider',
            problems = '[]'::jsonb,
            updated_at = now()
        """,
        view_id,
        list(rows),
        list(rows.values()),
    )


async def db_remove_files(conn: asyncpg.Connection, view_id: int, paths: Iterable[str]) -> None:
    gone = list(paths)
    if gone:
        await conn.execute(
            "DELETE FROM doc_index_file WHERE view_id = $1 AND path = ANY($2::text[])",
            view_id,
            gone,
        )


async def db_mark_dependents(conn: asyncpg.Connection, view_id: int, keys: Iterable[str]) -> int:
    """Les fichiers de la vue qui citent l'une de ces clés passent `a_valider` ; rend
    leur nombre (ceux qui y étaient déjà compris)."""
    cited = sorted(set(keys))
    if not cited:
        return 0
    status = await conn.execute(
        """
        UPDATE doc_index_file f SET status = 'a_valider', updated_at = now()
        FROM doc_index_content c
        WHERE f.view_id = $1 AND c.id = f.content_id AND c.refs && $2::text[]
        """,
        view_id,
        cited,
    )
    return int(status.split()[-1])


async def db_copy_view(conn: asyncpg.Connection, source_id: int, target_id: int) -> None:
    """Les fichiers d'une vue recopiés dans une autre (un brouillon part de la branche
    publiée), avec leur état."""
    await conn.execute("DELETE FROM doc_index_file WHERE view_id = $1", target_id)
    await conn.execute(
        """
        INSERT INTO doc_index_file (view_id, path, content_id, status, problems)
        SELECT $2, path, content_id, status, problems FROM doc_index_file WHERE view_id = $1
        """,
        source_id,
        target_id,
    )


# --- Résultats de validation (passes, enabler 6b171b74) ---------------------------------------


async def db_document_results(
    conn: asyncpg.Connection, content_ids: Iterable[int]
) -> dict[int, tuple[list[dict[str, object]], int | None]]:
    """La validation du document seul de ces contenus : (problèmes, version des
    règles), la version None quand il n'a jamais été validé."""
    ids = sorted(set(content_ids))
    if not ids:
        return {}
    rows = await conn.fetch(
        "SELECT id, problems, rules_version FROM doc_index_content WHERE id = ANY($1::bigint[])",
        ids,
    )
    out: dict[int, tuple[list[dict[str, object]], int | None]] = {}
    for r in rows:
        raw = r["problems"]
        problems = json.loads(raw) if isinstance(raw, str) else (raw or [])
        out[r["id"]] = (problems, r["rules_version"])
    return out


async def db_set_document_result(
    conn: asyncpg.Connection, content_id: int, problems: list[dict[str, object]], version: int
) -> None:
    await conn.execute(
        "UPDATE doc_index_content SET problems = $2::jsonb, rules_version = $3 WHERE id = $1",
        content_id,
        json.dumps(problems, ensure_ascii=False),
        version,
    )


async def db_set_statuses(
    conn: asyncpg.Connection, view_id: int, results: dict[str, tuple[str, list[dict[str, object]]]]
) -> None:
    """Pose l'état et les problèmes (règles entre fichiers comprises) de ces fichiers."""
    if not results:
        return
    paths = list(results)
    await conn.execute(
        """
        UPDATE doc_index_file f SET status = e.status, problems = e.problems::jsonb,
            updated_at = now()
        FROM unnest($2::text[], $3::text[], $4::text[]) AS e(path, status, problems)
        WHERE f.view_id = $1 AND f.path = e.path
        """,
        view_id,
        paths,
        [results[p][0] for p in paths],
        [json.dumps(results[p][1], ensure_ascii=False) for p in paths],
    )


async def db_mark_validated(
    conn: asyncpg.Connection, view_id: int, trigger: str | None = None
) -> None:
    """Une passe complète vient de valider la vue ; `trigger` : ce qui l'a demandée."""
    await conn.execute(
        "UPDATE doc_index_view SET validated_at = now(), validation_error = NULL, "
        "validated_trigger = $2 WHERE id = $1",
        view_id,
        trigger,
    )


async def db_mark_failed(conn: asyncpg.Connection, key: ViewKey, error: str) -> None:
    """Une passe a échoué : la vue n'est pas validée (et le dit)."""
    view = await db_ensure_view(conn, key)
    await conn.execute(
        "UPDATE doc_index_view SET validated_at = NULL, validation_error = $2 WHERE id = $1",
        view.id,
        error[:500],
    )


@dataclass(frozen=True)
class ViewSummary:
    """L'état de validation d'une vue : fichiers par état, dernière passe réussie."""

    files: int
    to_validate: int
    errors: int
    compared: bool
    validated_at: datetime | None
    validation_error: str | None

    def payload(self) -> dict[str, object]:
        return {
            "files": self.files,
            "to_validate": self.to_validate,
            "errors": self.errors,
            "compared": self.compared,
            "validated_at": self.validated_at.isoformat() if self.validated_at else None,
            "validation_error": self.validation_error,
        }


async def db_view_summary(conn: asyncpg.Connection, key: ViewKey) -> ViewSummary | None:
    row = await conn.fetchrow(
        """
        SELECT v.compared_at, v.validated_at, v.validation_error,
            count(f.path) AS files,
            count(f.path) FILTER (WHERE f.status = 'a_valider') AS to_validate,
            count(f.path) FILTER (WHERE f.status = 'en_erreur') AS errors
        FROM doc_index_view v LEFT JOIN doc_index_file f ON f.view_id = v.id
        WHERE v.space_id = $1 AND v.branch_id = $2 AND v.user_id IS NOT DISTINCT FROM $3
        GROUP BY v.id
        """,
        key.space_id,
        key.branch_id,
        key.user_id,
    )
    if row is None:
        return None
    return ViewSummary(
        row["files"],
        row["to_validate"],
        row["errors"],
        row["compared_at"] is not None,
        row["validated_at"],
        row["validation_error"],
    )


async def db_view_problems(
    conn: asyncpg.Connection, key: ViewKey
) -> list[tuple[str, dict[str, object]]]:
    """Les problèmes de chaque fichier de la vue (chemin, problème), par chemin."""
    rows = await conn.fetch(
        """
        SELECT f.path, f.problems
        FROM doc_index_file f JOIN doc_index_view v ON v.id = f.view_id
        WHERE v.space_id = $1 AND v.branch_id = $2 AND v.user_id IS NOT DISTINCT FROM $3
          AND jsonb_array_length(f.problems) > 0
        ORDER BY f.path
        """,
        key.space_id,
        key.branch_id,
        key.user_id,
    )
    out: list[tuple[str, dict[str, object]]] = []
    for r in rows:
        raw = r["problems"]
        for found in json.loads(raw) if isinstance(raw, str) else raw:
            out.append((r["path"], found))
    return out
