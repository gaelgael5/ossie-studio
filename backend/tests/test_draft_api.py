"""Résumé du brouillon, signal « a changé côté publié » objet par objet, et
lecture de la vue publiée (?view=published)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from tests.space_fixture import ApiSpace, forge_edit, make_api_space

MANIFEST = "sources/api-x/manifest.yaml"


def _manifest(slug: str, label: str) -> str:
    return (
        f"version: 2\nslug: {slug}\nlabel: {label}\ntype: openapi\n"
        "connection: ''\nparameters: {}\n"
    )


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    return await make_api_space(client, app, tmp_path)


async def _declare(sp: ApiSpace, slug: str = "api-x") -> None:
    body = {"slug": slug, "label": slug.upper(), "type": "openapi"}
    response = await sp.client.post(f"{sp.base}/sources", json=body)
    assert response.status_code == 201, response.text


async def _draft(sp: ApiSpace) -> dict[str, object]:
    response = await sp.client.get(f"{sp.base}/draft")
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


async def test_sans_brouillon_rien(sp: ApiSpace) -> None:
    assert await _draft(sp) == {
        "count": 0,
        "revision": 0,
        "changed_upstream_count": 0,
        "objects": [],
    }


async def test_brouillon_liste_ses_objets(sp: ApiSpace) -> None:
    await _declare(sp)
    draft = await _draft(sp)
    assert draft["objects"] == [{"path": MANIFEST, "state": "created", "changed_upstream": False}]
    assert draft["changed_upstream_count"] == 0


async def test_objet_publie_hors_brouillon_ne_signale_rien(sp: ApiSpace) -> None:
    await _declare(sp)
    forge_edit(sp, {"sources/ailleurs.txt": "x"})
    assert (await _draft(sp))["changed_upstream_count"] == 0


async def test_meme_objet_publie_par_un_autre_signale(sp: ApiSpace) -> None:
    await _declare(sp)
    forge_edit(sp, {MANIFEST: _manifest("api-x", "AUTRE")})
    draft = await _draft(sp)
    assert draft["changed_upstream_count"] == 1
    assert draft["objects"][0]["changed_upstream"] is True  # type: ignore[index]


async def test_suppression_cote_publie_signalee(sp: ApiSpace) -> None:
    forge_edit(sp, {"sources/api-y/manifest.yaml": _manifest("api-y", "Y")})
    put = await sp.client.put(f"{sp.base}/sources/api-y", json={"label": "Y modifié"})
    assert put.status_code in (200, 204), put.text
    forge_edit(sp, {"sources/api-y/manifest.yaml": None})
    draft = await _draft(sp)
    assert draft["objects"][0]["state"] == "modified"  # type: ignore[index]
    assert draft["objects"][0]["changed_upstream"] is True  # type: ignore[index]


async def test_vue_publiee_ignore_le_brouillon(sp: ApiSpace) -> None:
    await _declare(sp)
    merged = (await sp.client.get(f"{sp.base}/sources")).json()["sources"]
    published = (await sp.client.get(f"{sp.base}/sources", params={"view": "published"})).json()[
        "sources"
    ]
    assert [s["slug"] for s in merged] == ["api-x"]
    assert published == []
