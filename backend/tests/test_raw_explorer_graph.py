"""Explorateur raw : vue graphe (cardinalités, entrantes, profondeur, cycles)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.parsers.conventions import dump_schema
from ossie_studio.sources.explorer import (
    ExplorerObject,
    SchemaRef,
    SourceEntries,
    clear_caches,
    schema_refs,
)
from ossie_studio.sources.explorer_graph import MAX_NODES, ReferenceGraph
from tests.space_fixture import ApiSpace, forge_edit, make_api_space
from tests.test_raw_explorer import _git_files


@pytest.fixture(autouse=True)
def _fresh_caches() -> None:
    clear_caches()


def test_regles_de_cardinalite() -> None:
    refs = schema_refs(
        {
            "required": ["client", "lignes", "fk"],
            "allOf": [{"$ref": "base.json"}],
            "properties": {
                "client": {"$ref": "client.json"},
                "adresse": {"$ref": "adresse.json"},
                "lignes": {"type": "array", "minItems": 1, "items": {"$ref": "ligne.json"}},
                "tags": {"type": "array", "items": {"$ref": "tag.json"}},
                "attributs": {"type": "object", "additionalProperties": {"$ref": "attr.json"}},
                "money": {"x-unresolved-type": "Money"},
                "fk": {"type": "integer", "x-references": {"table": "dbo.client", "column": "id"}},
                "fk_opt": {"type": "integer", "x-references": [{"table": "dbo.pays"}]},
            },
        }
    )
    got = {(r.field, r.kind): (r.target, r.cardinality, r.external) for r in refs}
    assert got == {
        ("", "inherits"): ("base.json", "", False),
        ("client", "field"): ("client.json", "1", False),
        ("adresse", "field"): ("adresse.json", "0..1", False),
        ("lignes", "list"): ("ligne.json", "1..N", False),
        ("tags", "list"): ("tag.json", "0..N", False),
        ("attributs", "map"): ("attr.json", "0..N", False),
        ("money", "field"): ("Money", "0..1", True),
        ("fk", "foreign_key"): ("dbo.client", "1", False),
        ("fk_opt", "foreign_key"): ("dbo.pays", "0..1", False),
    }


def _obj(
    file: str, title: str, refs: tuple[SchemaRef, ...] = (), kind: str = "class", origin: str = ""
) -> ExplorerObject:
    return ExplorerObject(
        file=file,
        title=title,
        kind=kind,
        fields=0,
        origin=origin or f"src/{title}.cs",
        parser="csharp",
        badges=(),
        excluded_by="",
        schema_id=file,
        refs=refs,
    )


def _space() -> list[SourceEntries]:
    order = _obj(
        "order.json",
        "Order",
        (
            SchemaRef("customer", "field", "customer.json", "1"),
            SchemaRef("lines", "list", "line.json", "1..N"),
            SchemaRef("money", "field", "Money", "0..1", True),
        ),
    )
    line = _obj("line.json", "Line", (SchemaRef("order", "field", "order.json", "1"),))
    customer = _obj("customer.json", "Customer", (SchemaRef("addr", "field", "addr.json", "0..1"),))
    addr = _obj("addr.json", "Addr")
    invoice = _obj("invoice.json", "Invoice", (SchemaRef("order", "field", "order.json", "0..1"),))
    app = SourceEntries("app", "App", "git", "", True, [order, line, customer, addr, invoice], [])
    table = _obj(
        "dbo.orders.json",
        "orders",
        (SchemaRef("ref", "foreign_key", "dbo.customer", "1"),),
        kind="table",
        origin="dbo.orders",
    )
    cust_table = _obj("dbo.customer.json", "customer", kind="table", origin="dbo.customer")
    db = SourceEntries("db", "Db", "sqlserver", "ventes", True, [table, cust_table], [])
    return [app, db]


def test_graphe_profondeur_1_sortantes_entrantes_externe_cycle() -> None:
    graph = ReferenceGraph(_space(), "origin").build(("app", "order.json"), 1)
    assert graph is not None
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert nodes["app/order.json"]["level"] == 0
    assert nodes["app/customer.json"]["level"] == 1
    assert nodes["app/invoice.json"]["level"] == -1  # entrante
    assert nodes["/ext:Money"]["external"] is True
    assert "app/addr.json" not in nodes  # profondeur 1
    edges = {(e["from"], e["to"], e["field"]): e for e in graph["edges"]}
    assert edges[("app/order.json", "app/line.json", "lines")]["cardinality"] == "1..N"
    # Cycle Order ⇄ Line : les deux liens existent, chacun une seule fois, Line placé une fois.
    assert ("app/line.json", "app/order.json", "order") in edges
    assert len(graph["edges"]) == len(edges)
    assert list(nodes).count("app/line.json") == 1


def test_graphe_profondeur_2_et_entrante_sql_d_une_autre_source() -> None:
    graph = ReferenceGraph(_space(), "origin").build(("app", "order.json"), 2)
    assert graph is not None
    ids = {n["id"] for n in graph["nodes"]}
    assert "app/addr.json" in ids  # voisin du voisin
    sql = ReferenceGraph(_space(), "origin").build(("db", "dbo.customer.json"), 1)
    assert sql is not None
    edge = sql["edges"][0]
    assert (edge["from"], edge["kind"], edge["cardinality"]) == (
        "db/dbo.orders.json",
        "foreign_key",
        "1",
    )


def test_objet_sans_reference_et_inconnu() -> None:
    alone = SourceEntries("app", "App", "git", "", True, [_obj("solo.json", "Solo")], [])
    graph = ReferenceGraph([alone], "origin").build(("app", "solo.json"), 1)
    assert graph is not None
    assert [n["id"] for n in graph["nodes"]] == ["app/solo.json"] and graph["edges"] == []
    assert ReferenceGraph(_space(), "origin").build(("app", "absent.json"), 1) is None


def test_troncature_au_dela_du_seuil() -> None:
    hub = _obj("hub.json", "Hub")
    spokes = [
        _obj(f"s{i}.json", f"S{i}", (SchemaRef("hub", "field", "hub.json", "1"),))
        for i in range(MAX_NODES + 5)
    ]
    entries = SourceEntries("app", "App", "git", "", True, [hub, *spokes], [])
    graph = ReferenceGraph([entries], "origin").build(("app", "hub.json"), 1)
    assert graph is not None
    assert graph["truncated"] is True and len(graph["nodes"]) == MAX_NODES


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    return await make_api_space(client, app, tmp_path)


async def test_api_graphe(sp: ApiSpace) -> None:
    files = _git_files()
    files["sources/app/raw/order.json"] = dump_schema(
        {
            "$id": "order.json",
            "title": "Order",
            "type": "object",
            "x-kind": "class",
            "required": ["customer"],
            "properties": {"customer": {"$ref": "customer.json"}},
        }
    )
    forge_edit(sp, files)
    base = f"{sp.base}/raw-explorer/app"
    r = await sp.client.get(f"{base}/graph", params={"file": "order.json", "depth": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["center"] == "app/order.json"
    assert body["edges"][0]["cardinality"] == "1"
    r = await sp.client.get(f"{base}/graph", params={"file": "absent.json"})
    assert r.status_code == 404


def test_entrantes_avec_cardinalite_d_origine() -> None:
    graph = ReferenceGraph(_space(), "origin")
    incoming = graph.incoming(("app", "order.json"))
    assert [(r["target"]["title"], r["field"], r["cardinality"]) for r in incoming] == [
        ("Invoice", "order", "0..1"),
        ("Line", "order", "1"),
    ]


async def test_api_compteur_et_liste_des_references_entrantes(sp: ApiSpace) -> None:
    files = _git_files()
    files["sources/app/raw/order.json"] = dump_schema(
        {
            "$id": "order.json",
            "title": "Order",
            "type": "object",
            "x-kind": "class",
            "required": ["customer"],
            "properties": {"customer": {"$ref": "customer.json"}},
        }
    )
    forge_edit(sp, files)
    base = f"{sp.base}/raw-explorer/app"
    folder = await sp.client.get(f"{base}/folder", params={"path": "src/Domain/Sub"})
    assert folder.status_code == 200, folder.text
    counts = {o["title"]: o["referenced_by"] for o in folder.json()["objects"]}
    assert counts["Customer"] == 1
    found = await sp.client.get(f"{base}/search", params={"q": "ord"})
    assert found.json()["results"][0]["referenced_by"] == 0
    r = await sp.client.get(f"{base}/incoming", params={"file": "customer.json"})
    assert r.status_code == 200, r.text
    incoming = r.json()["incoming"]
    assert [(i["target"]["title"], i["field"], i["cardinality"]) for i in incoming] == [
        ("Order", "customer", "1")
    ]
    assert incoming[0]["target"]["folder"] == "src/Domain"
    r = await sp.client.get(f"{base}/incoming", params={"file": "absent.json"})
    assert r.json() == {"incoming": []}
