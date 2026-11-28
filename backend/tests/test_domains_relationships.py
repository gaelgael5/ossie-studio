"""Relations et références d'un domaine à la main (ticket 4e856094) : créer,
modifier, supprimer ; clés composées ; refus nommés ; document Ossie valide."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.sources.explorer import clear_caches
from tests.space_fixture import ApiSpace, admin_workdir, make_api_space
from tests.test_domains import _valid_ossie


def _dataset(name: str, fields: list[str], consultable: bool = True) -> dict[str, Any]:
    return {
        "name": name,
        "consultable": consultable,
        "source": f"logistique.public.{name.lower()}" if consultable else "",
        "fields": [{"name": f, "expression": f, "datatype": "Integer"} for f in fields],
        "primary_key": fields[:1],
    }


@pytest.fixture(autouse=True)
def _fresh_caches() -> None:
    clear_caches()


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    space = await make_api_space(client, app, tmp_path)
    api = f"{space.base}/domains"
    assert (await space.client.post(api, json={"name": "expedition"})).status_code == 201
    for body in (
        _dataset("Colis", ["id", "commande_id", "pays", "mode"]),
        _dataset("Commande", ["id", "client"]),
        _dataset("Tarif", ["pays", "mode", "prix"]),
        _dataset("Brouillon", ["id"], consultable=False),
    ):
        created = await space.client.post(f"{api}/expedition/datasets", json=body)
        assert created.status_code == 201, created.text
    return space


async def _ossie(app: FastAPI, sp: ApiSpace) -> str:
    workdir = await admin_workdir(app, sp)

    def read(root: Path) -> str:
        return (root / "domaines/expedition.ossie.yaml").read_text(encoding="utf-8")

    text: str = await workdir.read_branch(
        uuid.UUID(sp.space_id), "work/principale", sp.url, "x", None, read
    )
    return text


def _api(sp: ApiSpace, rest: str = "") -> str:
    return f"{sp.base}/domains/expedition{rest}"


async def test_relation_simple_et_composee(app: FastAPI, sp: ApiSpace) -> None:
    """Critère 1 : écrites dans le document Ossie, valides au schéma 0.1.1."""
    simple = {
        "name": "colis_commande",
        "from_dataset": "Colis",
        "to_dataset": "Commande",
        "from_columns": ["commande_id"],
        "to_columns": ["id"],
    }
    r = await sp.client.post(_api(sp, "/relationships"), json=simple)
    assert r.status_code == 201, r.text
    composite = {
        "name": "colis_tarif",
        "from_dataset": "Colis",
        "to_dataset": "Tarif",
        "from_columns": ["pays", "mode"],
        "to_columns": ["pays", "mode"],
    }
    r = await sp.client.post(_api(sp, "/relationships"), json=composite)
    assert r.status_code == 201, r.text
    names = [rel["name"] for rel in r.json()["relationships"]]
    assert names == ["colis_commande", "colis_tarif"]
    text = await _ossie(app, sp)
    _valid_ossie(text)
    assert "colis_tarif" in text and "commande_id" in text
    taken = await sp.client.post(_api(sp, "/relationships"), json=simple)
    assert (taken.status_code, taken.json()["error"]) == (409, "relationship_name_taken")


async def test_refus_nommes(sp: ApiSpace) -> None:
    """Critères 2 et 3, et champ non déclaré."""
    base = {"name": "x", "from_dataset": "Colis", "to_dataset": "Tarif"}
    mismatch = await sp.client.post(
        _api(sp, "/relationships"),
        json={**base, "from_columns": ["pays", "mode"], "to_columns": ["pays"]},
    )
    assert (mismatch.status_code, mismatch.json()["error"]) == (
        400,
        "relationship_columns_mismatch",
    )
    cross = await sp.client.post(
        _api(sp, "/relationships"),
        json={**base, "to_dataset": "Brouillon", "from_columns": ["id"], "to_columns": ["id"]},
    )
    assert (cross.status_code, cross.json()["error"]) == (400, "relationship_cross_document")
    unknown = await sp.client.post(
        _api(sp, "/relationships"),
        json={**base, "from_columns": ["absent"], "to_columns": ["pays"]},
    )
    assert (unknown.status_code, unknown.json()["error"]) == (400, "relationship_column_unknown")
    assert unknown.json()["error_args"]["detail"] == "Colis.absent"


async def test_modifier_puis_supprimer(sp: ApiSpace) -> None:
    """Critère 4 : supprimée, elle disparaît."""
    body = {
        "name": "colis_commande",
        "from_dataset": "Colis",
        "to_dataset": "Commande",
        "from_columns": ["commande_id"],
        "to_columns": ["id"],
    }
    await sp.client.post(_api(sp, "/relationships"), json=body)
    renamed = await sp.client.put(
        _api(sp, "/relationships/colis_commande"), json={**body, "name": "colis_de_commande"}
    )
    assert renamed.status_code == 200, renamed.text
    assert [r["name"] for r in renamed.json()["relationships"]] == ["colis_de_commande"]
    gone = await sp.client.delete(_api(sp, "/relationships/colis_de_commande"))
    assert gone.status_code == 200 and gone.json()["relationships"] == []
    missing = await sp.client.delete(_api(sp, "/relationships/colis_de_commande"))
    assert (missing.status_code, missing.json()["error"]) == (404, "relationship_not_found")


async def test_references_supprimer_et_recreer(sp: ApiSpace) -> None:
    ref = {
        "name": "colis_commande",
        "from_dataset": "Colis",
        "field": "commande_id",
        "to_dataset": "Commande",
        "kind": "field",
    }
    created = await sp.client.post(_api(sp, "/references"), json=ref)
    assert created.status_code == 201, created.text
    assert created.json()["references"][0]["to_dataset"] == "Commande"
    deleted = await sp.client.delete(_api(sp, "/references/colis_commande"))
    assert deleted.status_code == 200 and deleted.json()["references"] == []
    orphan = await sp.client.post(_api(sp, "/references"), json={**ref, "to_dataset": "Absent"})
    assert (orphan.status_code, orphan.json()["error"]) == (400, "reference_dataset_missing")
