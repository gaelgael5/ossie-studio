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


# --- Lectures : pas de réalignement si la copie vient de l'être (ticket 2daae568) ----------


async def _read_file(manager: WorkdirManager, sp: ApiSpace, rel: str) -> str | None:
    def read(root: Path) -> str | None:
        target = root / rel
        return target.read_text() if target.exists() else None

    return await manager.read_branch(
        uuid.UUID(sp.space_id), "work/principale", sp.url, "x", None, read
    )


async def test_lecture_recente_sans_nouveau_fetch_puis_realignee_apres_le_delai(
    app: FastAPI, sp: ApiSpace, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager: WorkdirManager = app.state.workdir_manager
    monkeypatch.setattr(manager, "_read_fresh_seconds", 5.0)
    clock = {"t": 3000.0}
    manager._clock = lambda: clock["t"]
    fetches = {"n": 0}
    original = manager._clone_or_refresh

    async def counting(*args, **kwargs):  # type: ignore[no-untyped-def]
        fetches["n"] += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(manager, "_clone_or_refresh", counting)
    assert await _read_file(manager, sp, "sources/y.txt") is None
    forge_edit(sp, {"sources/y.txt": "y"})
    # Dans le délai : plusieurs lectures (même simultanées), aucun aller-retour.
    clock["t"] += 2
    results = await asyncio.gather(*[_read_file(manager, sp, "sources/y.txt") for _ in range(5)])
    assert results == [None] * 5 and fetches["n"] == 1
    # Délai passé : réalignée, le changement est vu.
    clock["t"] += 4
    assert await _read_file(manager, sp, "sources/y.txt") == "y"
    assert fetches["n"] == 2


async def test_publication_force_le_realignement_suivant(
    app: FastAPI, sp: ApiSpace, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager: WorkdirManager = app.state.workdir_manager
    monkeypatch.setattr(manager, "_read_fresh_seconds", 60.0)
    manager._clock = lambda: 4000.0
    assert await _read_file(manager, sp, "sources/z.txt") is None
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
    forge_edit(sp, {"sources/w.txt": "w"})
    # Même horloge (dans le délai) : la publication a oublié l'alignement.
    assert await _read_file(manager, sp, "sources/w.txt") == "w"


async def test_delai_zero_realigne_a_chaque_lecture(app: FastAPI, sp: ApiSpace) -> None:
    manager: WorkdirManager = app.state.workdir_manager
    assert await _read_file(manager, sp, "sources/v.txt") is None
    forge_edit(sp, {"sources/v.txt": "v"})
    assert await _read_file(manager, sp, "sources/v.txt") == "v"
