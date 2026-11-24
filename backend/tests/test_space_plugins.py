"""Plugins utilisés par un espace, sur la branche regardée (ticket a30b4bd8)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.parsers.registry import code_plugins, plugin_usage
from tests.space_fixture import ApiSpace, forge_edit, make_api_space
from tests.test_raw_explorer import _manifest


def test_usage_par_type_de_source() -> None:
    usage = plugin_usage(
        [
            ("app", "App", "git", ["csharp", "markdown", "inconnu"]),
            ("contrats", "Contrats", "openapi", None),
            ("base", "Base", "sqlserver", None),
            ("tout", "Tout", "git", None),
        ]
    )
    # Parseur inconnu ignoré ; source base de données : extracteur SQL, aucun plugin.
    assert usage["csharp"] == ["app", "tout"]
    assert usage["markdown"] == ["app", "tout"]
    assert usage["openapi"] == ["contrats", "tout"]
    assert "inconnu" not in usage
    # Source Git sans parseur nommé : tous les parseurs de code (ce que lance la collecte).
    assert {pid for pid, s in usage.items() if "tout" in s} == {p.id for p in code_plugins()}


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    return await make_api_space(client, app, tmp_path)


async def test_api_plugins_utilises_et_non_utilises(sp: ApiSpace) -> None:
    r = await sp.client.get(f"{sp.base}/plugins")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sources"] == 0 and body["used"] == []
    assert {p["id"] for p in body["unused"]} >= {"csharp", "markdown", "openapi"}

    forge_edit(
        sp,
        {
            "sources/app/manifest.yaml": _manifest(
                "app",
                repository="o/app",
                branch="main",
                parsers=["csharp", "markdown"],
            ),
            "sources/paiement/manifest.yaml": _manifest("paiement", "openapi"),
        },
    )
    body = (await sp.client.get(f"{sp.base}/plugins")).json()
    used = {p["id"]: p for p in body["used"]}
    assert set(used) == {"csharp", "markdown", "openapi"}
    assert used["csharp"]["sources"] == [{"slug": "app", "label": "APP"}]
    assert used["openapi"]["sources"] == [{"slug": "paiement", "label": "PAIEMENT"}]
    assert used["csharp"]["origin"] == "native" and used["csharp"]["version"]
    assert "csharp" not in {p["id"] for p in body["unused"]}
    assert body["sources"] == 2
