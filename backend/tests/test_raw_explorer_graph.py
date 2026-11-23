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
            "allOf": [{"$ref": "base.schema.yaml"}],
            "properties": {
                "client": {"$ref": "client.schema.yaml"},
                "adresse": {"$ref": "adresse.schema.yaml"},
                "lignes": {"type": "array", "minItems": 1, "items": {"$ref": "ligne.schema.yaml"}},
                "tags": {"type": "array", "items": {"$ref": "tag.schema.yaml"}},
                "attributs": {
                    "type": "object",
                    "additionalProperties": {"$ref": "attr.schema.yaml"},
                },
                "money": {"x-unresolved-type": "Money"},
                "fk": {"type": "integer", "x-references": {"table": "dbo.client", "column": "id"}},
                "fk_opt": {"type": "integer", "x-references": [{"table": "dbo.pays"}]},
            },
        }
    )
    got = {(r.field, r.kind): (r.target, r.cardinality, r.external) for r in refs}
    assert got == {
        ("", "inherits"): ("base.schema.yaml", "", False),
        ("client", "field"): ("client.schema.yaml", "1", False),
        ("adresse", "field"): ("adresse.schema.yaml", "0..1", False),
        ("lignes", "list"): ("ligne.schema.yaml", "1..N", False),
        ("tags", "list"): ("tag.schema.yaml", "0..N", False),
        ("attributs", "map"): ("attr.schema.yaml", "0..N", False),
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
        "order.schema.yaml",
        "Order",
        (
            SchemaRef("customer", "field", "customer.schema.yaml", "1"),
            SchemaRef("lines", "list", "line.schema.yaml", "1..N"),
            SchemaRef("money", "field", "Money", "0..1", True),
        ),
    )
    line = _obj(
        "line.schema.yaml", "Line", (SchemaRef("order", "field", "order.schema.yaml", "1"),)
    )
    customer = _obj(
        "customer.schema.yaml",
        "Customer",
        (SchemaRef("addr", "field", "addr.schema.yaml", "0..1"),),
    )
    addr = _obj("addr.schema.yaml", "Addr")
    invoice = _obj(
        "invoice.schema.yaml",
        "Invoice",
        (SchemaRef("order", "field", "order.schema.yaml", "0..1"),),
    )
    app = SourceEntries("app", "App", "git", "", True, [order, line, customer, addr, invoice], [])
    table = _obj(
        "dbo.orders.schema.yaml",
        "orders",
        (SchemaRef("ref", "foreign_key", "dbo.customer", "1"),),
        kind="table",
        origin="dbo.orders",
    )
    cust_table = _obj("dbo.customer.schema.yaml", "customer", kind="table", origin="dbo.customer")
    db = SourceEntries("db", "Db", "sqlserver", "ventes", True, [table, cust_table], [])
    return [app, db]


def test_graphe_profondeur_1_sortantes_entrantes_externe_cycle() -> None:
    graph = ReferenceGraph(_space(), "origin").build(("app", "order.schema.yaml"), 1)
    assert graph is not None
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert nodes["app/order.schema.yaml"]["level"] == 0
    assert nodes["app/customer.schema.yaml"]["level"] == 1
    assert nodes["app/invoice.schema.yaml"]["level"] == -1  # entrante
    assert nodes["/ext:Money"]["external"] is True
    assert "app/addr.schema.yaml" not in nodes  # profondeur 1
    edges = {(e["from"], e["to"], e["field"]): e for e in graph["edges"]}
    assert (
        edges[("app/order.schema.yaml", "app/line.schema.yaml", "lines")]["cardinality"] == "1..N"
    )
    # Cycle Order ⇄ Line : les deux liens existent, chacun une seule fois, Line placé une fois.
    assert ("app/line.schema.yaml", "app/order.schema.yaml", "order") in edges
    assert len(graph["edges"]) == len(edges)
    assert list(nodes).count("app/line.schema.yaml") == 1


def test_graphe_profondeur_2_et_entrante_sql_d_une_autre_source() -> None:
    graph = ReferenceGraph(_space(), "origin").build(("app", "order.schema.yaml"), 2)
    assert graph is not None
    ids = {n["id"] for n in graph["nodes"]}
    assert "app/addr.schema.yaml" in ids  # voisin du voisin
    sql = ReferenceGraph(_space(), "origin").build(("db", "dbo.customer.schema.yaml"), 1)
    assert sql is not None
    edge = sql["edges"][0]
    assert (edge["from"], edge["kind"], edge["cardinality"]) == (
        "db/dbo.orders.schema.yaml",
        "foreign_key",
        "1",
    )


def test_objet_sans_reference_et_inconnu() -> None:
    alone = SourceEntries("app", "App", "git", "", True, [_obj("solo.schema.yaml", "Solo")], [])
    graph = ReferenceGraph([alone], "origin").build(("app", "solo.schema.yaml"), 1)
    assert graph is not None
    assert [n["id"] for n in graph["nodes"]] == ["app/solo.schema.yaml"] and graph["edges"] == []
    assert ReferenceGraph(_space(), "origin").build(("app", "absent.schema.yaml"), 1) is None


def test_troncature_au_dela_du_seuil() -> None:
    hub = _obj("hub.schema.yaml", "Hub")
    spokes = [
        _obj(f"s{i}.schema.yaml", f"S{i}", (SchemaRef("hub", "field", "hub.schema.yaml", "1"),))
        for i in range(MAX_NODES + 5)
    ]
    entries = SourceEntries("app", "App", "git", "", True, [hub, *spokes], [])
    graph = ReferenceGraph([entries], "origin").build(("app", "hub.schema.yaml"), 1)
    assert graph is not None
    assert graph["truncated"] is True and len(graph["nodes"]) == MAX_NODES


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    return await make_api_space(client, app, tmp_path)


async def test_api_graphe(sp: ApiSpace) -> None:
    files = _git_files()
    files["sources/app/raw/order.schema.yaml"] = dump_schema(
        {
            "$id": "order.schema.yaml",
            "title": "Order",
            "type": "object",
            "x-kind": "class",
            "required": ["customer"],
            "properties": {"customer": {"$ref": "customer.schema.yaml"}},
        }
    )
    forge_edit(sp, files)
    base = f"{sp.base}/raw-explorer/app"
    r = await sp.client.get(f"{base}/graph", params={"file": "order.schema.yaml", "depth": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["center"] == "app/order.schema.yaml"
    assert body["edges"][0]["cardinality"] == "1"
    r = await sp.client.get(f"{base}/graph", params={"file": "absent.schema.yaml"})
    assert r.status_code == 404


def test_entrantes_avec_cardinalite_d_origine() -> None:
    graph = ReferenceGraph(_space(), "origin")
    incoming = graph.incoming(("app", "order.schema.yaml"))
    assert [(r["target"]["title"], r["field"], r["cardinality"]) for r in incoming] == [
        ("Invoice", "order", "0..1"),
        ("Line", "order", "1"),
    ]


async def test_api_compteur_et_liste_des_references_entrantes(sp: ApiSpace) -> None:
    files = _git_files()
    files["sources/app/raw/order.schema.yaml"] = dump_schema(
        {
            "$id": "order.schema.yaml",
            "title": "Order",
            "type": "object",
            "x-kind": "class",
            "required": ["customer"],
            "properties": {"customer": {"$ref": "customer.schema.yaml"}},
        }
    )
    forge_edit(sp, files)
    base = f"{sp.base}/raw-explorer/app"
    folder = await sp.client.get(f"{base}/folder", params={"path": "src/Domain/Sub"})
    assert folder.status_code == 200, folder.text
    counts = {o["title"]: o["referenced_by"] for o in folder.json()["objects"]}
    assert counts["Customer"] == 1
    found = await sp.client.get(f"{sp.base}/raw-explorer/search", params={"q": "ord"})
    assert found.json()["results"][0]["referenced_by"] == 0
    r = await sp.client.get(f"{base}/incoming", params={"file": "customer.schema.yaml"})
    assert r.status_code == 200, r.text
    incoming = r.json()["incoming"]
    assert [(i["target"]["title"], i["field"], i["cardinality"]) for i in incoming] == [
        ("Order", "customer", "1")
    ]
    assert incoming[0]["target"]["folder"] == "src/Domain"
    r = await sp.client.get(f"{base}/incoming", params={"file": "absent.schema.yaml"})
    assert r.json() == {"incoming": []}


def test_descendants_recursifs_sans_boucle() -> None:
    def obj(file: str, refs: list[tuple[str, str]]) -> ExplorerObject:
        return ExplorerObject(
            file=file,
            title=file.removesuffix(".schema.yaml").upper(),
            kind="class",
            fields=len(refs),
            origin=f"src/{file}",
            parser="csharp",
            badges=("retained",) if file == "c.schema.yaml" else (),
            excluded_by="",
            schema_id=file,
            refs=tuple(SchemaRef(f, k, t, "0..1") for k, t, f in [(k, t, t[:1]) for k, t in refs]),
        )

    entries = SourceEntries(
        "app",
        "APP",
        "git",
        "",
        True,
        [
            obj("a.schema.yaml", [("field", "b.schema.yaml"), ("inherits", "base.schema.yaml")]),
            obj(
                "b.schema.yaml", [("list", "c.schema.yaml"), ("field", "a.schema.yaml")]
            ),  # boucle B → A
            obj("c.schema.yaml", [("map", "c.schema.yaml")]),  # auto-référence
            obj("base.schema.yaml", []),
        ],
        [],
    )
    graph = ReferenceGraph([entries], "origin")
    got = {d["file"]: d["retained"] for d in graph.descendants(("app", "a.schema.yaml"))}
    # L'héritage n'est pas suivi ; la boucle et l'auto-référence se terminent.
    assert got == {"b.schema.yaml": False, "c.schema.yaml": True}
    assert graph.descendants(("app", "base.schema.yaml")) == []


async def test_api_ajouter_avec_ses_enfants(sp: ApiSpace) -> None:
    files = _git_files()
    files["sources/app/raw/order.schema.yaml"] = dump_schema(
        {
            "$id": "order.schema.yaml",
            "title": "Order",
            "type": "object",
            "x-kind": "class",
            "properties": {"customer": {"$ref": "customer.schema.yaml"}},
        }
    )
    forge_edit(sp, files)
    r = await sp.client.get(
        f"{sp.base}/raw-explorer/app/descendants", params={"file": "order.schema.yaml"}
    )
    assert r.status_code == 200, r.text
    assert [(d["file"], d["retained"]) for d in r.json()["descendants"]] == [
        ("customer.schema.yaml", False)
    ]
    url = f"{sp.base}/sources/app/curation/retain-many"
    r = await sp.client.post(url, json={"paths": ["order.schema.yaml", "customer.schema.yaml"]})
    assert r.status_code == 200, r.text
    assert len(r.json()["retained"]) == 2
    # Déjà retenus : laissés tels quels, rien de réécrit.
    r = await sp.client.post(url, json={"paths": ["order.schema.yaml"]})
    assert r.json()["retained"] == []
    r = await sp.client.get(
        f"{sp.base}/raw-explorer/app/descendants", params={"file": "order.schema.yaml"}
    )
    assert r.json()["descendants"][0]["retained"] is True
    assert (await sp.client.post(url, json={"paths": []})).status_code == 400
    assert (await sp.client.post(url, json={"paths": ["absent.schema.yaml"]})).status_code == 404
