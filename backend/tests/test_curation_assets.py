"""Page « Curation » : assets retenus de toutes les sources, et leurs descriptions."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.parsers.conventions import dump_schema
from ossie_studio.sources.explorer import clear_caches
from tests.space_fixture import ApiSpace, forge_edit, make_api_space
from tests.test_raw_explorer import _git_files


@pytest.fixture(autouse=True)
def _fresh_caches() -> None:
    clear_caches()


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    return await make_api_space(client, app, tmp_path)


def _files() -> dict[str, str]:
    files = _git_files()
    files["sources/app/raw/order.schema.yaml"] = dump_schema(
        {
            "$id": "order.schema.yaml",
            "title": "Order",
            "type": "object",
            "x-kind": "class",
            "required": ["customer"],
            "properties": {
                "id": {"type": "string", "format": "uuid"},
                "numero": {"type": "string", "maxLength": 20},
                "customer": {"$ref": "customer.schema.yaml"},
                "lines": {"type": "array", "items": {"type": "string"}},
            },
        }
    )
    return files


async def test_seuls_les_assets_retenus_avec_compteurs_et_proprietes(sp: ApiSpace) -> None:
    forge_edit(sp, _files())
    api = f"{sp.base}/curation-assets"
    empty = await sp.client.get(api)
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"sources": [], "assets": [], "schemes": []}

    cur = f"{sp.base}/sources/app/curation"
    r = await sp.client.post(
        f"{cur}/retain", json={"path": "order.schema.yaml", "fields": ["id", "numero", "customer"]}
    )
    assert r.status_code == 200, r.text
    r = await sp.client.put(
        f"{cur}/enrich",
        json={
            "path": "order.schema.yaml",
            "fields": {
                "numero": {"business_name": "numeroCommande"},
                "id": {"description": "Identifiant.\n\n```mermaid\ngraph TD; A-->B\n```"},
            },
        },
    )
    assert r.status_code == 200, r.text

    listing = (await sp.client.get(api)).json()
    assert [s["slug"] for s in listing["sources"]] == ["app"]
    assert [a["path"] for a in listing["assets"]] == ["order.schema.yaml"]
    order = listing["assets"][0]
    assert order["title"] == "Order"
    assert order["property_count"] == 3 and order["raw_fields"] == 4
    assert order["undescribed"] == 2 and order["described"] is False
    assert order["origin"] == "src/Domain/Order.cs" and order["orphan"] is False
    assert order["property_names"] == ["customer", "id", "numero", "numeroCommande"]

    detail = await sp.client.get(
        f"{api}/asset", params={"source": "app", "path": "order.schema.yaml"}
    )
    assert detail.status_code == 200, detail.text
    props = {p["name"]: p for p in detail.json()["properties"]}
    assert props["id"]["type"] == "string" and props["id"]["format"] == "uuid"
    assert props["id"]["description"].startswith("Identifiant.")
    assert props["numero"]["business_name"] == "numeroCommande"
    assert props["numero"]["max_length"] == 20
    ref = props["customer"]["ref"]
    assert ref["path"] == "customer.schema.yaml" and ref["title"] == "Customer"
    assert ref["retained"] is False

    # La cible retenue devient un lien vers son asset.
    await sp.client.post(f"{cur}/retain", json={"path": "customer.schema.yaml", "fields": None})
    detail = await sp.client.get(
        f"{api}/asset", params={"source": "app", "path": "order.schema.yaml"}
    )
    props = {p["name"]: p for p in detail.json()["properties"]}
    assert props["customer"]["ref"]["retained"] is True


async def test_enrichir_une_seule_cle_ne_touche_pas_les_autres(sp: ApiSpace) -> None:
    forge_edit(sp, _files())
    cur = f"{sp.base}/sources/app/curation"
    await sp.client.post(f"{cur}/retain", json={"path": "order.schema.yaml", "fields": None})
    await sp.client.put(
        f"{cur}/enrich",
        json={"path": "order.schema.yaml", "obj": {"business_name": "Commande", "tags": ["pii"]}},
    )
    await sp.client.put(
        f"{cur}/enrich", json={"path": "order.schema.yaml", "obj": {"description": "Une commande."}}
    )
    detail = await sp.client.get(
        f"{sp.base}/curation-assets/asset", params={"source": "app", "path": "order.schema.yaml"}
    )
    body = detail.json()
    assert body["business_name"] == "Commande"
    assert body["description"] == "Une commande."
    assert body["described"] is True


async def test_asset_absent_et_historique(sp: ApiSpace) -> None:
    forge_edit(sp, _files())
    api = f"{sp.base}/curation-assets"
    r = await sp.client.get(f"{api}/asset", params={"source": "app", "path": "order.schema.yaml"})
    assert r.status_code == 404
    assert "asset_not_retained" in r.text
    r = await sp.client.get(f"{api}/asset", params={"source": "../x", "path": "order.schema.yaml"})
    assert r.status_code == 404
    h = await sp.client.get(
        f"{api}/asset/history", params={"source": "app", "path": "order.schema.yaml"}
    )
    assert h.status_code == 200, h.text
    assert h.json() == {"commits": []}


_SCHEME = """id: classification
name: Classification
is_primary: true
multi_value: false
display_order: 0
levels:
  - {id: l1, name: Interne, rank: 1}
  - {id: l2, name: Confidentiel, rank: 2}
  - {id: l3, name: Restreint, rank: 3}
"""


async def test_classer_un_asset_et_ses_champs(sp: ApiSpace) -> None:
    # Ticket 5e8680d7 : classification déclarée / dérivée, écart, refus, invalides.
    forge_edit(sp, {**_files(), "governance/classifications/classification.yaml": _SCHEME})
    cur = f"{sp.base}/sources/app/curation"
    r = await sp.client.post(f"{cur}/retain", json={"path": "order.schema.yaml"})
    assert r.status_code == 200, r.text
    r = await sp.client.put(
        f"{cur}/classify",
        json={
            "path": "order.schema.yaml",
            "fields": {
                "numero": {"classification": ["Restreint"]},
                "id": {"classification": ["Interne"]},
            },
        },
    )
    assert r.status_code == 200, r.text
    asset_url = f"{sp.base}/curation-assets/asset"
    params = {"source": "app", "path": "order.schema.yaml"}
    gov = (await sp.client.get(asset_url, params=params)).json()["governance"]
    [obj] = gov["object"]
    assert (obj["values"], obj["declared"], obj["extra"]) == (["Restreint"], False, 1)
    fields = {f["name"]: f["values"] for f in gov["fields"]}
    assert fields["numero"] == {"classification": ["Restreint"]} and fields["lines"] == {}

    # Déclarer moins sensible qu'un champ : la déclaration l'emporte, l'écart est signalé.
    r = await sp.client.put(
        f"{cur}/classify",
        json={"path": "order.schema.yaml", "obj": {"classification": ["Interne"]}},
    )
    assert r.status_code == 200, r.text
    detail = (await sp.client.get(asset_url, params=params)).json()
    [obj] = detail["governance"]["object"]
    assert obj["values"] == ["Interne"] and obj["declared"] is True
    assert obj["gap"] == {"field": "numero", "level": "Restreint"}
    assert [(b["values"], b["declared"]) for b in detail["classification"]] == [(["Interne"], True)]
    listing = (await sp.client.get(f"{sp.base}/curation-assets")).json()
    assert "classification/Restreint" in listing["assets"][0]["class_levels"]
    assert [s["id"] for s in listing["schemes"]] == ["classification"]

    # Niveau inconnu : refusé, rien n'est écrit.
    r = await sp.client.put(
        f"{cur}/classify",
        json={"path": "order.schema.yaml", "obj": {"classification": ["Secret"]}},
    )
    assert r.status_code == 400 and r.json()["error"] == "classification_unknown_level"
    r = await sp.client.put(
        f"{cur}/classify",
        json={"path": "order.schema.yaml", "obj": {"classification": ["Interne", "Restreint"]}},
    )
    assert r.status_code == 400 and r.json()["error"] == "classification_not_single"

    # La source porte le niveau de ses assets.
    sources = (await sp.client.get(f"{sp.base}/sources")).json()["sources"]
    app_source = next(s for s in sources if s["slug"] == "app")
    assert app_source["classification"] == [{"scheme": "classification", "values": ["Interne"]}]

    # Aucune valeur invalide tant que les niveaux existent.
    invalid = await sp.client.get(f"{sp.base}/governance/classifications/invalid-values")
    assert invalid.status_code == 200, invalid.text
    assert invalid.json() == {"values": []}
