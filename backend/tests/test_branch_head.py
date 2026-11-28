"""Tête publiée d'une branche, pour le rafraîchissement des écrans : au plus un
fetch par délai minimal, quel que soit le nombre d'écrans ouverts."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.spaces.workdir import PublishObject, WorkdirManager
from tests.space_fixture import ApiSpace, forge_edit, git, make_api_space


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    return await make_api_space(client, app, tmp_path)


def _remote_head(sp: ApiSpace) -> str:
    return git(sp.tmp, "--git-dir", sp.url.removeprefix("file://"), "rev-parse", "work/principale")


async def _head(sp: ApiSpace) -> str:
    response = await sp.client.get(f"{sp.base}/head")
    assert response.status_code == 200, response.text
    sha: str = response.json()["sha"]
    return sha


async def test_tete_publiee_puis_changement_vu_apres_le_delai(app: FastAPI, sp: ApiSpace) -> None:
    manager: WorkdirManager = app.state.workdir_manager
    clock = {"t": 1000.0}
    manager._clock = lambda: clock["t"]
    assert await _head(sp) == _remote_head(sp)
    forge_edit(sp, {"sources/x.txt": "x"})
    # Dans le délai : valeur en cache, pas de fetch.
    clock["t"] += 5
    stale = await _head(sp)
    assert stale != _remote_head(sp)
    # Délai passé : la nouvelle tête est vue.
    clock["t"] += 10
    assert await _head(sp) == _remote_head(sp)


async def test_dix_ecrans_un_seul_fetch(
    app: FastAPI, sp: ApiSpace, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager: WorkdirManager = app.state.workdir_manager
    manager._clock = lambda: 2000.0
    fetches = {"n": 0}
    original = manager.read_copy

    def counting(*args, **kwargs):  # type: ignore[no-untyped-def]
        fetches["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(manager, "read_copy", counting)
    await _head(sp)
    await asyncio.gather(*[_head(sp) for _ in range(9)])
    assert fetches["n"] == 1


async def test_branche_inconnue_404(sp: ApiSpace) -> None:
    response = await sp.client.get(f"/api/spaces/{sp.space_id}/branches/{sp.space_id}/head")
    assert response.status_code == 404


# --- Lectures : pas d'aller-retour vers la forge, réalignées à la publication (ticket 2daae568)


async def _read_file(manager: WorkdirManager, sp: ApiSpace, rel: str) -> str | None:
    def read(root: Path) -> str | None:
        target = root / rel
        return target.read_text() if target.exists() else None

    return await manager.read_branch(
        uuid.UUID(sp.space_id), "work/principale", sp.url, "x", None, read
    )


def _count_fetches(manager: WorkdirManager, monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    fetches = {"n": 0}
    original = manager._clone_or_refresh

    async def counting(*args, **kwargs):  # type: ignore[no-untyped-def]
        fetches["n"] += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(manager, "_clone_or_refresh", counting)
    return fetches


async def test_lectures_sans_fetch_puis_publication_vue(
    app: FastAPI, sp: ApiSpace, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager: WorkdirManager = app.state.workdir_manager
    monkeypatch.setattr(manager, "_read_follows_forge", False)
    fetches = _count_fetches(manager, monkeypatch)
    assert await _read_file(manager, sp, "sources/y.txt") is None
    # Modifiée hors de l'application : pas vue, et aucune lecture ne fetche.
    forge_edit(sp, {"sources/y.txt": "y"})
    results = await asyncio.gather(*[_read_file(manager, sp, "sources/y.txt") for _ in range(5)])
    assert results == [None] * 5 and fetches["n"] == 1
    # La tête des écrans non plus ne fetche pas.
    manager._head_cache.clear()
    await _head(sp)
    assert fetches["n"] == 1
    # Publication par l'application : la copie est réalignée, tout est vu.
    await manager.push_objects(
        uuid.UUID(sp.space_id),
        "work/principale",
        sp.url,
        "x",
        None,
        "x",
        "tok",
        [PublishObject("sources/z.txt", None, b"z")],
        "Ana",
        "ana@example.org",
        "publie z",
    )
    fetched = fetches["n"]
    assert await _read_file(manager, sp, "sources/z.txt") == "z"
    assert await _read_file(manager, sp, "sources/y.txt") == "y"
    assert fetches["n"] == fetched  # la lecture après publication ne refetche pas


async def test_mode_tests_realigne_a_chaque_lecture(app: FastAPI, sp: ApiSpace) -> None:
    manager: WorkdirManager = app.state.workdir_manager
    assert await _read_file(manager, sp, "sources/v.txt") is None
    forge_edit(sp, {"sources/v.txt": "v"})
    assert await _read_file(manager, sp, "sources/v.txt") == "v"
