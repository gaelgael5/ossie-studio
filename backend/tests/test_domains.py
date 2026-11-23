"""Domaines (tickets docflow bfae2d97, a7baaaa3, 36d35a88).

Gael, 23/11/2026 : un domaine = un modèle sémantique Ossie 0.1.1 ; « un asset
donne un dataset » ; `source` virtuel = `raw:<source>/<fichier>` ; tout ce qui
sort de la norme dans `<nom>.extend.yaml`.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from jsonschema import Draft202012Validator

from ossie_studio.domains.derive import (
    apply_plan,
    dataset_from_asset,
    datatype_of,
    plan_add,
    remove_dataset,
)
from ossie_studio.domains.documents import (
    AssetRef,
    Dataset,
    Domain,
    DomainError,
    DomainField,
    Reference,
    Relationship,
    read_domain,
    render_domain,
    write_domain,
)
from ossie_studio.parsers.conventions import dump_schema, load_schema
from ossie_studio.sources.explorer import SchemaRef, clear_caches
from tests.space_fixture import ApiSpace, forge_edit, make_api_space
from tests.test_curation_assets import _files

_SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "src/ossie_studio/domains/schemas/osi-schema-0.1.1.json"
    ).read_text(encoding="utf-8")
)


def _valid_ossie(text: str) -> None:
    errors = list(Draft202012Validator(_SCHEMA).iter_errors(load_schema(text)))
    assert errors == [], [e.message for e in errors]


def _table(name: str, fields: list[str], **extra: Any) -> dict[str, Any]:
    return {
        "title": name,
        "type": "object",
        "x-kind": "table",
        "properties": {f: {"type": "integer"} for f in fields},
        **extra,
    }


# --- documents ------------------------------------------------------------------


def test_domaine_consultable_valide_et_sans_donnee_studio(tmp_path: Path) -> None:
    orders = dataset_from_asset(
        ("db", "dbo.orders.schema.yaml"),
        _table("orders", ["id", "customer_id"], **{"x-primary-key": ["id"]}),
        "orders",
    )
    customers = dataset_from_asset(
        ("db", "dbo.customers.schema.yaml"),
        _table("customers", ["id"], **{"x-primary-key": ["id"]}),
        "customers",
    )
    domain = Domain(
        name="ventes",
        description="Ventes",
        datasets=[orders, customers],
        relationships=[
            Relationship("orders_customer_id", "orders", "customers", ["customer_id"], ["id"])
        ],
    )
    texts = render_domain(domain)
    assert texts["ventes.virtuel.ossie.yaml"] is None
    ossie = texts["ventes.ossie.yaml"]
    assert ossie is not None
    _valid_ossie(ossie)
    doc = load_schema(ossie)
    model = doc["semantic_model"][0]
    assert doc["version"] == "0.1.1" and model["name"] == "ventes"
    assert model["datasets"][0]["source"] == "dbo.orders"
    assert model["datasets"][0]["primary_key"] == ["id"]
    # Aucune donnée propre à Ossie-studio dans le document Ossie.
    for word in ("asset", "datatype", "consultable", "derived", "custom_extensions"):
        assert word not in ossie
    extend = load_schema(texts["ventes.extend.yaml"] or "")
    assert extend["datasets"]["orders"]["asset"] == {
        "source": "db",
        "path": "dbo.orders.schema.yaml",
    }
    assert extend["datasets"]["orders"]["fields"]["id"] == {"datatype": "Integer", "property": "id"}


def test_domaine_virtuel_source_raw_et_references(tmp_path: Path) -> None:
    colis = dataset_from_asset(
        ("colis-21", "colis.schema.yaml"),
        {
            "title": "Colis",
            "type": "object",
            "x-kind": "class",
            "properties": {
                "Numero": {"type": "string"},
                "Adresse": {"$ref": "adresse.schema.yaml"},
            },
        },
        "Colis",
    )
    adresse = dataset_from_asset(
        ("colis-21", "adresse.schema.yaml"),
        {"title": "Adresse", "type": "object", "properties": {"Ville": {"type": "string"}}},
        "Adresse",
    )
    domain = Domain(
        name="expedition",
        datasets=[colis, adresse],
        references=[Reference("Colis_Adresse", "Colis", "Adresse", "Adresse", "field", "n:1")],
    )
    write_domain(tmp_path, domain)
    base = tmp_path / "domaines"
    assert sorted(p.name for p in base.iterdir()) == [
        "expedition.extend.yaml",
        "expedition.virtuel.ossie.yaml",
    ]
    virtual = (base / "expedition.virtuel.ossie.yaml").read_text(encoding="utf-8")
    _valid_ossie(virtual)
    assert "raw:colis-21/colis.schema.yaml" in virtual
    again = read_domain(tmp_path, "expedition")
    assert again is not None and again.warnings == []
    assert [d.name for d in again.datasets] == ["Colis", "Adresse"]
    assert again.datasets[0].asset == AssetRef("colis-21", "colis.schema.yaml")
    assert again.references[0].to_dataset == "Adresse"
    # Écriture déterministe.
    before = {p.name: p.read_bytes() for p in base.iterdir()}
    write_domain(tmp_path, again)
    assert {p.name: p.read_bytes() for p in base.iterdir()} == before


def test_domaine_vide_seul_extend(tmp_path: Path) -> None:
    write_domain(tmp_path, Domain(name="vide"))
    assert [p.name for p in (tmp_path / "domaines").iterdir()] == ["vide.extend.yaml"]
    assert read_domain(tmp_path, "vide") == Domain(name="vide")


def test_retirer_un_dataset_retire_ses_liens(tmp_path: Path) -> None:
    domain = Domain(
        name="d",
        datasets=[
            Dataset("A", "raw:s/a", False, True, fields=[DomainField("b", "b")]),
            Dataset("B", "raw:s/b", False, True),
        ],
        references=[Reference("A_b", "A", "b", "B", "field")],
    )
    assert remove_dataset(domain, "B")
    assert domain.references == [] and [d.name for d in domain.datasets] == ["A"]
    assert not remove_dataset(domain, "absent")


def test_refus_nom_invalide_doublon_et_relation_entre_documents() -> None:
    with pytest.raises(DomainError) as exc:
        render_domain(Domain(name="Pas Bon"))
    assert exc.value.code == "domain_name_invalid"
    a = Dataset("A", "t.a", True, True, fields=[DomainField("x", "x")])
    with pytest.raises(DomainError) as exc:
        render_domain(Domain(name="d", datasets=[a, a]))
    assert exc.value.code == "dataset_name_taken"
    v = Dataset("V", "raw:s/v", False, True, fields=[DomainField("y", "y")])
    with pytest.raises(DomainError) as exc:
        render_domain(
            Domain(
                name="d", datasets=[a, v], relationships=[Relationship("r", "A", "V", ["x"], ["y"])]
            )
        )
    assert exc.value.code == "relationship_cross_document"


def test_document_ossie_invalide_refuse_a_la_lecture(tmp_path: Path) -> None:
    write_domain(tmp_path, Domain(name="d", datasets=[Dataset("A", "raw:s/a", False, True)]))
    bad = tmp_path / "domaines" / "d.virtuel.ossie.yaml"
    doc = load_schema(bad.read_text(encoding="utf-8"))
    del doc["semantic_model"][0]["datasets"][0]["source"]
    bad.write_text(dump_schema(doc), encoding="utf-8")
    with pytest.raises(DomainError) as exc:
        read_domain(tmp_path, "d")
    assert exc.value.code == "ossie_invalid" and "source" in exc.value.detail


def test_entree_extend_orpheline_signalee(tmp_path: Path) -> None:
    write_domain(tmp_path, Domain(name="d", datasets=[Dataset("A", "raw:s/a", False, True)]))
    ext = tmp_path / "domaines" / "d.extend.yaml"
    doc = load_schema(ext.read_text(encoding="utf-8"))
    doc["datasets"]["Fantome"] = {"consultable": False, "derived": True}
    ext.write_text(dump_schema(doc), encoding="utf-8")
    domain = read_domain(tmp_path, "d")
    assert domain is not None and domain.warnings == ["extend_orphan_dataset:Fantome"]


# --- déduction ------------------------------------------------------------------


def test_types_deduits() -> None:
    assert datatype_of({"type": "string", "format": "date-time"}) == "DateTime"
    assert datatype_of({"type": ["integer", "null"]}) == "Integer"
    assert datatype_of({"type": "number", "format": "double"}) == "Float"
    assert datatype_of({"type": "number"}) == "Decimal"
    assert datatype_of({"$ref": "x.schema.yaml"}) == "Opaque"


class _Graph:
    """Doublure du graphe de références : refs par asset, résolution par cible."""

    def __init__(
        self, refs: dict[tuple[str, str], list[SchemaRef]], targets: dict[str, tuple[str, str]]
    ):
        self._refs, self._targets = refs, targets

    def located(self, key: tuple[str, str]) -> Any:
        return (None, SimpleNamespace(refs=self._refs.get(key, []))) if key in self._refs else None

    def resolve(self, ref: SchemaRef) -> tuple[str, str] | None:
        return self._targets.get(ref.target)

    def descendants(self, key: tuple[str, str]) -> list[dict[str, Any]]:
        return []


def test_cle_etrangere_donne_une_relation_ossie() -> None:
    orders_key, customers_key = (
        ("db", "dbo.orders.schema.yaml"),
        ("db", "dbo.customers.schema.yaml"),
    )
    orders = _table("orders", ["id", "customer_id"])
    orders["properties"]["customer_id"]["x-references"] = {"table": "dbo.customers", "column": "id"}
    assets = {orders_key: orders, customers_key: _table("customers", ["id"])}
    graph = _Graph(
        {
            orders_key: [SchemaRef("customer_id", "foreign_key", "dbo.customers", "1")],
            customers_key: [],
        },
        {"dbo.customers": customers_key},
    )
    plan = plan_add(Domain(name="ventes"), [orders_key, customers_key], assets, graph, False)  # type: ignore[arg-type]
    [link] = plan.links
    assert (link.kind, link.from_dataset, link.to_dataset, link.to_column) == (
        "relationship",
        "orders",
        "customers",
        "id",
    )
    domain = Domain(name="ventes")
    apply_plan(domain, plan, None)
    assert domain.relationships == [
        Relationship("orders_customer_id", "orders", "customers", ["customer_id"], ["id"])
    ]
    _valid_ossie(render_domain(domain)["ventes.ossie.yaml"] or "")


def test_lien_decoche_non_ecrit_et_asset_deja_present_non_duplique() -> None:
    a_key, b_key = ("s", "a.schema.yaml"), ("s", "b.schema.yaml")
    assets = {
        a_key: {"title": "A", "type": "object", "properties": {"b": {"$ref": "b.schema.yaml"}}},
        b_key: {"title": "B", "type": "object", "properties": {"x": {"type": "string"}}},
    }
    graph = _Graph(
        {a_key: [SchemaRef("b", "field", "b.schema.yaml", "1")], b_key: []},
        {"b.schema.yaml": b_key},
    )
    domain = Domain(name="d")
    plan = plan_add(domain, [a_key, b_key], assets, graph, False)  # type: ignore[arg-type]
    assert [lk.id for lk in plan.links] == ["ref:A.b->B"]
    apply_plan(domain, plan, accepted=set())  # tout décoché
    assert domain.references == [] and len(domain.datasets) == 2
    again = plan_add(domain, [a_key], assets, graph, False)  # type: ignore[arg-type]
    assert again.added == [] and again.already == ["A"]


def test_noms_distincts_pour_deux_assets_de_meme_titre() -> None:
    assets = {
        ("s1", "c.schema.yaml"): {"title": "Commande", "type": "object"},
        ("s2", "c.schema.yaml"): {"title": "Commande", "type": "object"},
    }
    graph = _Graph({}, {})
    plan = plan_add(Domain(name="d"), list(assets), assets, graph, False)  # type: ignore[arg-type]
    assert [d.name for d in plan.added] == ["Commande", "Commande_s2"]


# --- API ------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_caches() -> None:
    clear_caches()


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    return await make_api_space(client, app, tmp_path)


async def test_creer_regrouper_ouvrir_retirer_supprimer(sp: ApiSpace) -> None:
    forge_edit(sp, _files())
    cur = f"{sp.base}/sources/app/curation"
    r = await sp.client.post(
        f"{cur}/retain-many", json={"paths": ["order.schema.yaml", "customer.schema.yaml"]}
    )
    assert r.status_code == 200, r.text
    api = f"{sp.base}/domains"
    assert (await sp.client.get(api)).json() == {"domains": []}
    r = await sp.client.post(api, json={"name": "expedition", "description": "Colis"})
    assert r.status_code == 201, r.text
    assert (await sp.client.post(api, json={"name": "expedition"})).status_code == 409
    assert (await sp.client.post(api, json={"name": "Pas Bon"})).status_code == 400

    # « Ajouter avec ses références » sur Order : Customer (retenu) vient avec.
    body = {"assets": [{"source": "app", "path": "order.schema.yaml"}], "with_references": True}
    preview = await sp.client.post(f"{api}/expedition/preview", json=body)
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert sorted(d["name"] for d in data["added"]) == ["Customer", "Order"]
    assert [(lk["kind"], lk["from_dataset"], lk["to_dataset"]) for lk in data["links"]] == [
        ("reference", "Order", "Customer")
    ]
    added = await sp.client.post(f"{api}/expedition/assets", json=body)
    assert added.status_code == 200, added.text

    detail = (await sp.client.get(f"{api}/expedition")).json()
    assert detail["virtual"] == 2 and detail["consultable"] == 0
    order = next(d for d in detail["datasets"] if d["name"] == "Order")
    assert order["source"] == "raw:app/order.schema.yaml" and order["derived"] is True
    assert detail["references"][0]["field"] == "customer"
    listing = (await sp.client.get(api)).json()["domains"]
    assert {"source": "app", "path": "order.schema.yaml"} in listing[0]["assets"]

    # Le même ajout ne duplique rien.
    again = (await sp.client.post(f"{api}/expedition/preview", json=body)).json()
    assert again["added"] == [] and sorted(again["already"]) == ["Customer", "Order"]

    # Un asset non retenu est refusé.
    r = await sp.client.post(
        f"{api}/expedition/preview",
        json={"assets": [{"source": "app", "path": "status.schema.yaml"}]},
    )
    assert r.status_code == 404

    r = await sp.client.delete(f"{api}/expedition/datasets/Customer")
    assert r.status_code == 204
    detail = (await sp.client.get(f"{api}/expedition")).json()
    assert [d["name"] for d in detail["datasets"]] == ["Order"] and detail["references"] == []

    assert (await sp.client.delete(f"{api}/expedition")).status_code == 204
    assert (await sp.client.get(f"{api}/expedition")).status_code == 404


async def test_regrouper_dans_un_domaine_neuf_en_un_commit(sp: ApiSpace) -> None:
    forge_edit(sp, _files())
    cur = f"{sp.base}/sources/app/curation"
    r = await sp.client.post(f"{cur}/retain-many", json={"paths": ["order.schema.yaml"]})
    assert r.status_code == 200, r.text
    api = f"{sp.base}/domains"
    body = {
        "assets": [{"source": "app", "path": "order.schema.yaml"}],
        "create": True,
        "description": "Expédition",
    }
    # L'aperçu ne crée rien.
    preview = await sp.client.post(f"{api}/expedition/preview", json=body)
    assert preview.status_code == 200, preview.text
    assert (await sp.client.get(api)).json() == {"domains": []}
    # Sans `create`, un domaine absent est introuvable.
    r = await sp.client.post(f"{api}/expedition/assets", json={**body, "create": False})
    assert r.status_code == 404
    r = await sp.client.post(f"{api}/expedition/assets", json=body)
    assert r.status_code == 200, r.text
    [domain] = (await sp.client.get(api)).json()["domains"]
    assert (domain["name"], domain["description"], domain["virtual"]) == (
        "expedition",
        "Expédition",
        1,
    )
