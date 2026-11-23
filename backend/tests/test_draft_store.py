"""Stockage du brouillon : seulement les objets modifiés, base figée à la
première modification, brouillon vide supprimé."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI

from ossie_studio.drafts.store import (
    DRAFT_OBJECT_MAX_BYTES,
    DraftChange,
    DraftObjectTooLargeError,
    DraftPathError,
    db_get_draft,
    db_list_objects,
    db_rebase_object,
    db_replace_draft,
    validate_draft_path,
)


@pytest.fixture
async def ids(app: FastAPI) -> dict[str, uuid.UUID]:
    async with app.state.pool.acquire() as conn:
        user = await conn.fetchval(
            "INSERT INTO users (email, role) VALUES ('d@x.org', 'user') RETURNING id"
        )
        await conn.execute(
            "INSERT INTO secrets (slug, label, category, value_encrypted) "
            "VALUES ('s', 's', 'github', '\\x00')"
        )
        connection = await conn.fetchval(
            "INSERT INTO connections (label, slug, forge, url, secret_slug, identity, "
            "last_status) VALUES ('c', 'c', 'github', 'https://g', 's', 'i', 'valid') RETURNING id"
        )
        space = await conn.fetchval(
            "INSERT INTO git_spaces (name, connection_id, repo_url, last_probe_status) "
            "VALUES ('e', $1, 'https://g/o/r', 'valid') RETURNING id",
            connection,
        )
        branch = await conn.fetchval(
            "INSERT INTO space_branches (space_id, label, slug, kind, git_ref) "
            "VALUES ($1, 'B', 'b', 'base', 'work/b') RETURNING id",
            space,
        )
    return {"user": user, "space": space, "branch": branch}


async def _replace(app: FastAPI, ids: dict[str, uuid.UUID], changes: list[DraftChange]):  # type: ignore[no-untyped-def]
    async with app.state.pool.acquire() as conn:
        return await db_replace_draft(
            conn,
            user_id=ids["user"],
            space_id=ids["space"],
            branch_id=ids["branch"],
            head_commit="h1",
            changes=changes,
        )


async def _objects(
    app: FastAPI, ids: dict[str, uuid.UUID]
) -> dict[str, tuple[str | None, bytes | None]]:
    async with app.state.pool.acquire() as conn:
        draft = await db_get_draft(conn, ids["user"], ids["branch"])
        if draft is None:
            return {}
        return {o.path: (o.base_blob, o.content) for o in await db_list_objects(conn, draft.id)}


async def test_premiere_modif_fige_la_base(app: FastAPI, ids: dict[str, uuid.UUID]) -> None:
    await _replace(app, ids, [DraftChange("a.yaml", "blob1", b"v1")])
    assert await _objects(app, ids) == {"a.yaml": ("blob1", b"v1")}


async def test_remodif_garde_la_base_d_origine(app: FastAPI, ids: dict[str, uuid.UUID]) -> None:
    await _replace(app, ids, [DraftChange("a.yaml", "blob1", b"v1")])
    # La tête publiée a bougé (blob2) : la base reste celle de la 1re modification.
    draft = await _replace(app, ids, [DraftChange("a.yaml", "blob2", b"v2")])
    assert await _objects(app, ids) == {"a.yaml": ("blob1", b"v2")}
    assert draft is not None and draft.revision == 2


async def test_objet_absent_des_changements_sort_du_brouillon(
    app: FastAPI, ids: dict[str, uuid.UUID]
) -> None:
    await _replace(
        app, ids, [DraftChange("a.yaml", "b1", b"v1"), DraftChange("b.yaml", "b2", b"w")]
    )
    await _replace(app, ids, [DraftChange("b.yaml", "b2", b"w")])
    assert set(await _objects(app, ids)) == {"b.yaml"}


async def test_brouillon_vide_supprime(app: FastAPI, ids: dict[str, uuid.UUID]) -> None:
    await _replace(app, ids, [DraftChange("a.yaml", "b1", b"v1")])
    assert await _replace(app, ids, []) is None
    async with app.state.pool.acquire() as conn:
        assert await db_get_draft(conn, ids["user"], ids["branch"]) is None


async def test_creation_suppression(app: FastAPI, ids: dict[str, uuid.UUID]) -> None:
    await _replace(
        app, ids, [DraftChange("neuf.yaml", None, b"n"), DraftChange("vieux.yaml", "b9", None)]
    )
    objects = await _objects(app, ids)
    assert objects == {"neuf.yaml": (None, b"n"), "vieux.yaml": ("b9", None)}


@pytest.mark.parametrize("path", ["../x", "/etc/x", ".git/config", "a/../b", "a//b", "a\\b"])
async def test_chemin_hors_depot_refuse(app: FastAPI, ids: dict[str, uuid.UUID], path: str) -> None:
    with pytest.raises(DraftPathError):
        await _replace(app, ids, [DraftChange(path, None, b"x")])
    assert await _objects(app, ids) == {}


def test_chemin_valide() -> None:
    assert (
        validate_draft_path("sources/api/raw/User.schema.yaml")
        == "sources/api/raw/User.schema.yaml"
    )


async def test_objet_trop_gros_refuse(app: FastAPI, ids: dict[str, uuid.UUID]) -> None:
    with pytest.raises(DraftObjectTooLargeError):
        await _replace(
            app, ids, [DraftChange("gros.schema.yaml", None, b"x" * (DRAFT_OBJECT_MAX_BYTES + 1))]
        )


async def test_objet_de_5_mo_accepte(app: FastAPI, ids: dict[str, uuid.UUID]) -> None:
    payload = b"x" * (5 * 1024 * 1024)
    await _replace(app, ids, [DraftChange("gros.schema.yaml", None, payload)])
    assert (await _objects(app, ids))["gros.schema.yaml"] == (None, payload)


async def test_rebase_objet(app: FastAPI, ids: dict[str, uuid.UUID]) -> None:
    draft = await _replace(app, ids, [DraftChange("a.yaml", "b1", b"v1")])
    assert draft is not None
    async with app.state.pool.acquire() as conn:
        await db_rebase_object(conn, draft.id, "a.yaml", "b2", b"fusion")
    assert await _objects(app, ids) == {"a.yaml": ("b2", b"fusion")}


async def test_suppression_espace_ou_branche_cascade(
    app: FastAPI, ids: dict[str, uuid.UUID]
) -> None:
    await _replace(app, ids, [DraftChange("a.yaml", "b1", b"v1")])
    async with app.state.pool.acquire() as conn:
        await conn.execute("DELETE FROM space_branches WHERE id = $1", ids["branch"])
        assert await conn.fetchval("SELECT count(*) FROM drafts") == 0
        assert await conn.fetchval("SELECT count(*) FROM draft_objects") == 0


async def test_suppression_utilisateur_cascade(app: FastAPI, ids: dict[str, uuid.UUID]) -> None:
    await _replace(app, ids, [DraftChange("a.yaml", "b1", b"v1")])
    async with app.state.pool.acquire() as conn:
        await conn.execute("DELETE FROM users WHERE id = $1", ids["user"])
        assert await conn.fetchval("SELECT count(*) FROM drafts") == 0
