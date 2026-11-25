"""Historique d'une source : porté par Git (plus de table source_collections)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from tests.space_fixture import ApiSpace, forge_edit, make_api_space


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    return await make_api_space(client, app, tmp_path)


async def test_table_supprimee(app: FastAPI) -> None:
    async with app.state.pool.acquire() as conn:
        assert await conn.fetchval("SELECT to_regclass('source_collections')") is None
        assert await conn.fetchval("SELECT to_regclass('scan_files')") is not None


async def test_historique_git_du_repertoire_de_la_source(sp: ApiSpace) -> None:
    forge_edit(sp, {"sources/api/raw/A.schema.yaml": "{}"})
    forge_edit(sp, {"sources/autre/raw/B.schema.yaml": "{}"})
    forge_edit(sp, {"sources/api/raw/A.schema.yaml": '{"v": 2}'})
    response = await sp.client.get(f"{sp.base}/sources/api/collections")
    assert response.status_code == 200, response.text
    runs = response.json()["collections"]
    assert len(runs) == 2  # seuls les commits qui touchent sources/api/
    assert set(runs[0]) == {"sha", "author", "date", "message"}
    assert runs[0]["author"] == "forge"


async def test_source_jamais_publiee_historique_vide(sp: ApiSpace) -> None:
    response = await sp.client.get(f"{sp.base}/sources/inconnue/collections")
    assert response.status_code == 200
    assert response.json()["collections"] == []


async def test_slug_invalide_404(sp: ApiSpace) -> None:
    response = await sp.client.get(f"{sp.base}/sources/..%2Fx/collections")
    assert response.status_code == 404
