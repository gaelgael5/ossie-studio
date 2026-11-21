"""Tête publiée d'une branche, pour le rafraîchissement des écrans : au plus un
fetch par délai minimal, quel que soit le nombre d'écrans ouverts."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.spaces.workdir import WorkdirManager
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
