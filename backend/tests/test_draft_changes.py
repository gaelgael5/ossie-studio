"""Vue « Modifications » (ticket docflow aad1d3c6) : mon brouillon rangé par
source et par nature, avec les assets orphelins de ma vue ; badges « nouveau » /
« modifié » par rapport à la branche publiée."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.parsers.conventions import load_schema
from tests.space_fixture import ApiSpace, draft_write, forge_edit, make_api_space
from tests.test_raw_explorer import _manifest, _schema


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    space = await make_api_space(client, app, tmp_path)
    forge_edit(
        space,
        {
            "sources/app/manifest.yaml": _manifest("app"),
            "sources/app/raw/order.schema.yaml": _schema("Order"),
            "sources/app/raw/customer.schema.yaml": _schema("Customer"),
            "sources/app/assets/order.schema.yaml": _schema("Order"),
            # Asset publié dont l'objet raw n'existe plus : orphelin.
            "sources/app/assets/ghost.schema.yaml": _schema("Ghost"),
        },
    )
    return space


def _by_file(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {i["file"]: i for i in items}


async def _badges(sp: ApiSpace) -> dict[str, list[str]]:
    r = await sp.client.get(f"{sp.base}/raw-explorer/app/folder", params={"path": ""})
    assert r.status_code == 200, r.text
    return {o["file"]: o["badges"] for o in r.json()["objects"]}


async def test_badges_par_rapport_a_la_branche_publiee(app: FastAPI, sp: ApiSpace) -> None:
    # Rien dans le brouillon : aucun badge, même sans revue jamais faite.
    badges = await _badges(sp)
    assert (
        "new" not in badges["customer.schema.yaml"]
        and "modified" not in badges["order.schema.yaml"]
    )
    await draft_write(
        app, sp, "sources/app/raw/order.schema.yaml", load_schema(_schema("Order", fields=3))
    )
    await draft_write(
        app, sp, "sources/app/raw/invoice.schema.yaml", load_schema(_schema("Invoice"))
    )
    badges = await _badges(sp)
    assert "modified" in badges["order.schema.yaml"]
    assert "new" in badges["invoice.schema.yaml"]
    assert not {"new", "modified"} & set(badges["customer.schema.yaml"])


async def test_vue_modifications_par_source_et_nature(app: FastAPI, sp: ApiSpace) -> None:
    await draft_write(
        app, sp, "sources/app/raw/order.schema.yaml", load_schema(_schema("Order", fields=3))
    )
    await draft_write(
        app, sp, "sources/app/raw/invoice.schema.yaml", load_schema(_schema("Invoice"))
    )
    await draft_write(app, sp, "sources/app/raw/customer.schema.yaml", None)
    await draft_write(
        app, sp, "sources/app/assets/invoice.schema.yaml", load_schema(_schema("Invoice"))
    )
    await draft_write(app, sp, "notes/lisez-moi.schema.yaml", {"a": 1})

    r = await sp.client.get(f"{sp.base}/raw-explorer/changes")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 5
    (source,) = body["sources"]
    assert source["slug"] == "app" and source["label"] == "APP"
    raw = _by_file(source["raw"])
    assert {f: i["state"] for f, i in raw.items()} == {
        "customer.schema.yaml": "deleted",
        "invoice.schema.yaml": "created",
        "order.schema.yaml": "modified",
    }
    assert (
        raw["order.schema.yaml"]["title"] == "Order"
        and raw["order.schema.yaml"]["folder"] is not None
    )
    assert raw["customer.schema.yaml"]["folder"] is None  # supprimé : pas de lien
    assert [(a["file"], a["state"]) for a in source["assets"]] == [
        ("invoice.schema.yaml", "created")
    ]
    # ghost.schema.yaml : publié, objet absent ; aucune entrée du brouillon nécessaire.
    assert [o["file"] for o in source["orphans"]] == ["ghost.schema.yaml"]
    assert body["other"] == [{"path": "notes/lisez-moi.schema.yaml", "state": "created"}]


async def test_vue_modifications_sans_brouillon(sp: ApiSpace) -> None:
    r = await sp.client.get(f"{sp.base}/raw-explorer/changes")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 0 and body["other"] == []
    (source,) = body["sources"]  # l'orphelin publié reste signalé
    assert source["raw"] == [] and [o["file"] for o in source["orphans"]] == ["ghost.schema.yaml"]
