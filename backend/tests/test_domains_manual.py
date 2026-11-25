"""Dataset manuel d'un domaine (ticket docflow 1c68a20a) : créé et modifié à la
main, consultable (source saisie) ou virtuel (`manuel:<domaine>/<dataset>`),
types d'Ossie 0.3 dans le `.extend.yaml`, liens qui suivent les renommages."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.domains.documents import (
    Dataset,
    Domain,
    DomainError,
    DomainField,
    Reference,
    Relationship,
    read_domain,
    write_domain,
)
from ossie_studio.domains.manual import DatasetInput, FieldInput, save_manual_dataset
from ossie_studio.parsers.conventions import load_schema
from ossie_studio.sources.explorer import clear_caches
from tests.space_fixture import ApiSpace, admin_workdir, forge_read, make_api_space
from tests.test_domains import _valid_ossie


def _colis(**extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Colis",
        "consultable": True,
        "source": "logistique.public.colis",
        "description": "Colis expédiés",
        "fields": [
            {"name": "id", "expression": "id", "datatype": "Integer"},
            {"name": "cree_le", "expression": "created_at", "datatype": "DateTimeTz"},
            {"name": "poids", "expression": "poids_g / 1000.0"},
        ],
        "primary_key": ["id"],
    }
    body.update(extra)
    return body


# --- Règles (fonctions pures) ---------------------------------------------------


def test_refus_nommes() -> None:
    domain = Domain(name="d")
    cases = [
        (DatasetInput("A", consultable=True, source=""), "dataset_source_required"),
        (DatasetInput("A", source="t", fields=[FieldInput("x")]), "field_expression_required"),
        (
            DatasetInput("A", source="t", fields=[FieldInput("x", "x", datatype="Timestamp")]),
            "field_datatype_invalid",
        ),
        (
            DatasetInput("A", source="t", fields=[FieldInput("x", "x"), FieldInput("x", "y")]),
            "field_name_taken",
        ),
        (DatasetInput("A", source="t", primary_key=["absent"]), "key_field_missing"),
        (DatasetInput("a/b", source="t"), "dataset_name_invalid"),
    ]
    for data, code in cases:
        with pytest.raises(DomainError) as exc:
            save_manual_dataset(domain, data)
        assert exc.value.code == code, data


def test_virtuel_expression_et_source_automatiques() -> None:
    domain = Domain(name="expedition")
    ds, impacts = save_manual_dataset(
        domain, DatasetInput("Suivi", consultable=False, fields=[FieldInput("etape")])
    )
    assert ds.source == "manuel:expedition/Suivi"
    assert ds.fields[0].expression == "etape" and ds.fields[0].datatype == ""
    assert ds.derived is False and ds.asset is None and impacts == []


def test_renommer_champ_et_dataset_les_liens_suivent() -> None:
    domain = Domain(
        name="d",
        datasets=[
            Dataset(
                "A", "t.a", True, False, fields=[DomainField("id", "id"), DomainField("x", "x")]
            ),
            Dataset("B", "t.b", True, False, fields=[DomainField("a_id", "a_id")]),
        ],
        relationships=[Relationship("b_a", "B", "A", ["a_id"], ["id"])],
        references=[Reference("a_x", "A", "x", "B", "field")],
    )
    data = DatasetInput(
        "Alpha",
        source="t.a",
        fields=[FieldInput("ident", "id", previous="id"), FieldInput("x", "x", previous="x")],
        primary_key=["ident"],
    )
    ds, impacts = save_manual_dataset(domain, data, "A")
    assert {(i.kind, i.name, i.effect) for i in impacts} == {
        ("relationship", "b_a", "follows"),
        ("reference", "a_x", "follows"),
    }
    rel = domain.relationships[0]
    assert (rel.to_dataset, rel.to_columns) == ("Alpha", ["ident"])
    assert domain.references[0].from_dataset == "Alpha"
    assert ds.primary_key == ["ident"]


def test_supprimer_un_champ_retire_le_lien() -> None:
    domain = Domain(
        name="d",
        datasets=[
            Dataset("A", "t.a", True, False, fields=[DomainField("id", "id")]),
            Dataset("B", "t.b", True, False, fields=[DomainField("a_id", "a_id")]),
        ],
        relationships=[Relationship("b_a", "B", "A", ["a_id"], ["id"])],
    )
    _ds, impacts = save_manual_dataset(domain, DatasetInput("A", source="t.a"), "A")
    assert [(i.name, i.effect) for i in impacts] == [("b_a", "removed")]
    assert domain.relationships == []


def test_derive_en_lecture_seule() -> None:
    domain = Domain(name="d", datasets=[Dataset("A", "raw:s/a", False, True)])
    with pytest.raises(DomainError) as exc:
        save_manual_dataset(domain, DatasetInput("A", consultable=False), "A")
    assert exc.value.code == "dataset_derived_readonly"


# --- API ------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_caches() -> None:
    clear_caches()


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    space = await make_api_space(client, app, tmp_path)
    r = await space.client.post(f"{space.base}/domains", json={"name": "expedition"})
    assert r.status_code == 201, r.text
    return space


async def _commit(app: FastAPI, sp: ApiSpace, fn: Any) -> None:
    workdir = await admin_workdir(app, sp)
    await workdir.commit_to_branch(
        uuid.UUID(sp.space_id), "work/principale", sp.url, "x", None, "a", "a", "test", fn
    )


async def _read(app: FastAPI, sp: ApiSpace, rel: str) -> str | None:
    workdir = await admin_workdir(app, sp)

    def read(root: Path) -> str | None:
        target = root / rel
        return target.read_text(encoding="utf-8") if target.is_file() else None

    result: str | None = await workdir.read_branch(
        uuid.UUID(sp.space_id), "work/principale", sp.url, "x", None, read
    )
    return result


async def test_creer_consultable_puis_basculer_en_virtuel(sp: ApiSpace, app: FastAPI) -> None:
    api = f"{sp.base}/domains/expedition/datasets"
    r = await sp.client.post(api, json=_colis())
    assert r.status_code == 201, r.text
    assert r.json()["dataset"]["derived"] is False
    detail = (await sp.client.get(f"{sp.base}/domains/expedition")).json()
    colis = detail["datasets"][0]
    assert (colis["consultable"], colis["asset"], colis["source"]) == (
        True,
        None,
        "logistique.public.colis",
    )
    assert [f["datatype"] for f in colis["fields"]] == ["Integer", "DateTimeTz", ""]
    # Rien n'est publié : le brouillon seul porte le dataset, document Ossie valide
    # sans aucun type ; les types sont dans le `.extend.yaml`.
    assert forge_read(sp, "domaines/expedition.ossie.yaml") is None
    ossie = await _read(app, sp, "domaines/expedition.ossie.yaml")
    assert ossie is not None and "datatype" not in ossie
    _valid_ossie(ossie)
    extend = load_schema(await _read(app, sp, "domaines/expedition.extend.yaml") or "")
    assert extend["datasets"]["Colis"] == {
        "consultable": True,
        "derived": False,
        "fields": {"id": {"datatype": "Integer"}, "cree_le": {"datatype": "DateTimeTz"}},
    }

    # Consultable sans source : refusé, rien d'écrit.
    bad = await sp.client.post(api, json=_colis(name="Autre", source=""))
    assert (bad.status_code, bad.json()["error"]) == (400, "dataset_source_required")
    taken = await sp.client.post(api, json=_colis())
    assert (taken.status_code, taken.json()["error"]) == (409, "dataset_name_taken")
    wrong_type = await sp.client.post(
        api, json=_colis(name="X", fields=[{"name": "a", "expression": "a", "datatype": "Text"}])
    )
    assert wrong_type.json()["error"] == "field_datatype_invalid"

    # Décocher « consultable » : virtuel, source manuel:, document virtuel valide.
    r = await sp.client.put(f"{api}/Colis", json=_colis(consultable=False, source=""))
    assert r.status_code == 200, r.text
    assert r.json()["dataset"]["source"] == "manuel:expedition/Colis"
    detail = (await sp.client.get(f"{sp.base}/domains/expedition")).json()
    assert (detail["consultable"], detail["virtual"]) == (0, 1)
    assert detail["datasets"][0]["fields"][1]["expression"] == "cree_le"
    assert await _read(app, sp, "domaines/expedition.ossie.yaml") is None
    _valid_ossie(await _read(app, sp, "domaines/expedition.virtuel.ossie.yaml") or "")


async def test_renommer_un_champ_lie_demande_confirmation(sp: ApiSpace, app: FastAPI) -> None:
    api = f"{sp.base}/domains/expedition/datasets"
    assert (await sp.client.post(api, json=_colis())).status_code == 201
    sac = {
        "name": "Sac",
        "source": "logistique.public.sac",
        "fields": [{"name": "colis_id", "expression": "colis_id"}],
    }
    assert (await sp.client.post(api, json=sac)).status_code == 201

    # Une relation Sac → Colis posée dans le domaine (l'écran des relations est
    # un autre ticket : écrite ici directement dans le brouillon).
    def add_relation(root: Path) -> None:
        domain = read_domain(root, "expedition")
        assert domain is not None
        domain.relationships.append(Relationship("sac_colis", "Sac", "Colis", ["colis_id"], ["id"]))
        write_domain(root, domain)

    await _commit(app, sp, add_relation)
    renamed = _colis()
    renamed["fields"][0] = {"name": "ident", "expression": "id", "previous": "id"}
    renamed["fields"][1]["previous"] = "cree_le"
    renamed["fields"][2]["previous"] = "poids"
    renamed["primary_key"] = ["ident"]
    asked = await sp.client.put(f"{api}/Colis", json=renamed)
    assert asked.status_code == 409, asked.text
    assert asked.json()["error"] == "dataset_change_impacts"
    assert asked.json()["error_args"]["impacts"] == [
        {"kind": "relationship", "name": "sac_colis", "effect": "follows"}
    ]
    done = await sp.client.put(f"{api}/Colis", json={**renamed, "confirm": True})
    assert done.status_code == 200, done.text
    rel = (await sp.client.get(f"{sp.base}/domains/expedition")).json()["relationships"][0]
    assert (rel["to_dataset"], rel["to_columns"]) == ("Colis", ["ident"])

    # Basculer Colis en virtuel laisserait une relation entre deux documents :
    # refusé, la relation nommée.
    cross = await sp.client.put(
        f"{api}/Colis", json={**renamed, "consultable": False, "source": "", "confirm": True}
    )
    assert cross.status_code == 400, cross.text
    assert cross.json()["error"] == "relationship_cross_document"
    assert cross.json()["error_args"]["detail"] == "sac_colis"
