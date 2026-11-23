"""Explorateur raw : aperçu de l'objet sélectionné (détail, références, liens)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.forges.links import forge_file_url
from ossie_studio.parsers.conventions import dump_schema, load_schema
from ossie_studio.sources.explorer import (
    ExplorerDocument,
    ExplorerObject,
    SourceEntries,
    clear_caches,
)
from ossie_studio.sources.explorer_detail import SpaceObjects, document_detail, object_detail
from tests.space_fixture import ApiSpace, forge_edit, make_api_space
from tests.test_raw_explorer import _git_files, _manifest, _schema


@pytest.fixture(autouse=True)
def _fresh_caches() -> None:
    clear_caches()


def _obj(file: str, title: str, kind: str = "class", origin: str = "") -> ExplorerObject:
    return ExplorerObject(
        file=file,
        title=title,
        kind=kind,
        fields=0,
        origin=origin,
        parser="csharp",
        badges=(),
        excluded_by="",
        schema_id=file,
        checksum="abc",
        branch="main",
    )


_ORDER = {
    "$id": "app.order.schema.yaml",
    "title": "Order",
    "description": "Une commande.",
    "x-kind": "class",
    "x-annotations": ['[Table("orders")]'],
    "allOf": [{"$ref": "app.base.schema.yaml"}],
    "required": ["id", "customer"],
    "properties": {
        "id": {"type": "integer", "x-annotations": ["[Key]"]},
        "customer": {"$ref": "app.customer.schema.yaml"},
        "lines": {"type": "array", "items": {"$ref": "app.line.schema.yaml"}},
        "money": {"x-unresolved-type": "Money"},
        "note": {"type": "string", "description": "Libre."},
    },
}


def _app_entries(docs: list[ExplorerDocument] | None = None) -> SourceEntries:
    return SourceEntries(
        slug="app",
        label="App",
        type="git",
        database="",
        collected=True,
        objects=[
            _obj("app.order.schema.yaml", "Order", origin="src/Order.cs"),
            _obj("app.customer.schema.yaml", "Customer", origin="src/Customer.cs"),
            _obj("app.base.schema.yaml", "Base", origin="src/Base.cs"),
        ],
        documents=docs or [],
    )


def _db_entries() -> SourceEntries:
    return SourceEntries(
        slug="entrepot",
        label="Entrepôt",
        type="postgresql",
        database="ventes",
        collected=True,
        objects=[_obj("public.customers.schema.yaml", "customers", "table", "public.customers")],
        documents=[],
    )


def test_detail_champs_propres_description_annotations() -> None:
    entries = _app_entries()
    space = SpaceObjects([entries], "origin")
    detail = object_detail(_ORDER, json.dumps(_ORDER), entries.objects[0], entries, space)
    assert detail["description"] == "Une commande."
    assert detail["annotations"] == ['[Table("orders")]']
    fields = {f["name"]: f for f in detail["fields"]}
    assert list(fields) == ["id", "customer", "lines", "money", "note"]  # propres, non aplatis
    assert fields["id"]["required"] and fields["id"]["annotations"] == ["[Key]"]
    assert not fields["note"]["required"] and fields["note"]["description"] == "Libre."
    assert fields["customer"]["type"]["target"]["file"] == "app.customer.schema.yaml"
    assert fields["money"]["type"] == {"label": "Money", "external": True}
    assert detail["origin"]["checksum"] == "abc" and detail["origin"]["parser"] == "csharp"


def test_references_heritage_externe_et_non_resolue() -> None:
    entries = _app_entries()
    space = SpaceObjects([entries], "origin")
    refs = object_detail(_ORDER, "{}", entries.objects[0], entries, space)["references"]
    by_field = {(r["field"], r["kind"]): r for r in refs}
    inherits = by_field[("", "inherits")]
    assert inherits["target"] == {
        "slug": "app",
        "file": "app.base.schema.yaml",
        "title": "Base",
        "folder": "src",
    }
    assert by_field[("customer", "field")]["target"]["title"] == "Customer"
    assert by_field[("lines", "list")]["target"] is None  # objet absent de l'espace
    assert by_field[("money", "field")]["external"] is True


def test_cle_etrangere_resolue_dans_une_autre_source() -> None:
    table = {
        "$id": "public.orders.schema.yaml",
        "title": "orders",
        "x-kind": "table",
        "properties": {
            "customer_id": {
                "type": "integer",
                "x-references": {"table": "public.customers", "column": "id"},
            }
        },
    }
    orders = _obj("public.orders.schema.yaml", "orders", "table", "public.orders")
    db = _db_entries()
    db.objects.append(orders)
    space = SpaceObjects([_app_entries(), db], "origin")
    (ref,) = object_detail(table, "{}", orders, db, space)["references"]
    assert ref["kind"] == "foreign_key" and ref["label"] == "public.customers.id"
    assert ref["target"] == {
        "slug": "entrepot",
        "file": "public.customers.schema.yaml",
        "title": "customers",
        "folder": "ventes/public",
    }


def test_documents_qui_citent_et_objets_cites() -> None:
    doc = ExplorerDocument("docs/guide.md", "Guide", 2, ("Order", "Inconnu"))
    entries = _app_entries([doc, ExplorerDocument("docs/autre.md", "Autre", 1, ("Truc",))])
    space = SpaceObjects([entries], "origin")
    detail = object_detail(_ORDER, "{}", entries.objects[0], entries, space)
    assert detail["documents"] == [{"path": "docs/guide.md", "title": "Guide"}]
    cited = document_detail(doc, space)["objects"]
    assert [c["file"] for c in cited] == ["app.order.schema.yaml"]


def test_liens_forge() -> None:
    assert forge_file_url("github", "https://api.github.com", "org/app", "main", "src/A.cs", 9) == (
        "https://github.com/org/app/blob/main/src/A.cs#L9"
    )
    assert forge_file_url("gitlab", "", "g/app", "dev", "a b.cs") == (
        "https://gitlab.com/g/app/-/blob/dev/a%20b.cs"
    )
    url = forge_file_url(
        "azure_devops", "https://dev.azure.com/org", "Pickup/Colis21", "master", "src/A.cs", 3
    )
    assert url == (
        "https://dev.azure.com/org/Pickup/_git/Colis21?path=/src/A.cs&version=GBmaster"
        "&line=3&lineEnd=3&lineStartColumn=1&lineEndColumn=1"
    )
    assert forge_file_url("inconnue", "x", "a/b", "m", "f") is None
    assert forge_file_url("github", "", "", "m", "f") is None


# --- API ---------------------------------------------------------------------


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    return await make_api_space(client, app, tmp_path)


async def test_api_objet_document_historique(sp: ApiSpace) -> None:
    files = _git_files()
    files["sources/app/raw/order.schema.yaml"] = dump_schema(
        {
            "$id": "order.schema.yaml",
            "title": "Order",
            "type": "object",
            "x-kind": "class",
            "description": "Commande.",
            "properties": {"customer": {"$ref": "customer.schema.yaml"}},
        }
    )
    files["sources/app/raw/docs-index.yaml"] = (
        "version: 2\ndocuments:\n"
        "- {path: docs/guide.md, crc: a, title: Guide, sections: [Un], mentions: [Order],"
        " resources: []}\n"
    )
    files["sources/app/assets/order.schema.yaml"] = dump_schema(
        {"$id": "order.schema.yaml", "title": "Order", "properties": {"customer": {}}}
    )
    forge_edit(sp, files)
    base = f"{sp.base}/raw-explorer/app"
    r = await sp.client.get(f"{base}/object", params={"file": "order.schema.yaml"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["description"] == "Commande."
    assert body["fields"][0]["type"]["target"]["file"] == "customer.schema.yaml"
    assert load_schema(body["json"])["title"] == "Order"  # le schéma exact
    assert body["documents"] == [{"path": "docs/guide.md", "title": "Guide"}]
    assert body["exposure"]["retained"] is True and body["fields"][0]["exposed"] is True
    assert body["forge_url"] is None  # connexion « c » inconnue : pas de lien deviné

    r = await sp.client.get(f"{base}/document", params={"path": "docs/guide.md"})
    assert [o["file"] for o in r.json()["objects"]] == ["order.schema.yaml"]

    r = await sp.client.get(f"{base}/object/history", params={"file": "order.schema.yaml"})
    assert r.status_code == 200 and len(r.json()["commits"]) == 1

    r = await sp.client.get(f"{base}/object", params={"file": "absent.schema.yaml"})
    assert r.status_code == 404 and r.json()["error"] == "raw_explorer_object_not_found"
    r = await sp.client.get(f"{base}/object", params={"file": "../x.schema.yaml"})
    assert r.status_code == 404


async def test_api_objet_lit_le_brouillon(sp: ApiSpace, app: FastAPI) -> None:
    forge_edit(sp, _git_files())
    base = f"{sp.base}/raw-explorer/app"
    assert (await sp.client.get(f"{base}/tree")).status_code == 200
    # Le brouillon modifie le schéma : l'aperçu montre la version du brouillon.
    async with app.state.pool.acquire() as conn:
        user_id = await conn.fetchval("SELECT id FROM users LIMIT 1")
        draft = await conn.fetchval(
            "INSERT INTO drafts (user_id, space_id, branch_id, base_commit) "
            "VALUES ($1, $2, $3, 'base') RETURNING id",
            user_id,
            uuid.UUID(sp.space_id),
            uuid.UUID(sp.branch_id),
        )
        await conn.execute(
            "INSERT INTO draft_objects (draft_id, path, base_blob, content) "
            "VALUES ($1, $2, 'x', $3)",
            draft,
            "sources/app/raw/order.schema.yaml",
            _schema("OrderDraft").encode(),
        )
    r = await sp.client.get(f"{base}/object", params={"file": "order.schema.yaml"})
    assert r.status_code == 200, r.text
    assert load_schema(r.json()["json"])["title"] == "OrderDraft"


def test_visibilite_lue_jamais_deduite() -> None:
    from ossie_studio.sources.explorer import visibility_of

    assert visibility_of({"x-language": "csharp", "x-visibility": "internal"}) == "internal"
    # Code sans visibilité (schéma antérieur aux conventions) : inconnue, sans erreur.
    assert visibility_of({"x-language": "java"}) == "unknown"
    assert visibility_of({"x-language": "java", "x-visibility": "bizarre"}) == "unknown"
    # Table SQL, contrat : pas de visibilité.
    assert visibility_of({"x-kind": "table"}) == ""


def test_visibilite_des_champs_et_mot_cle_d_origine() -> None:
    schema = {
        "title": "Conf",
        "x-language": "python",
        "x-visibility": "public",
        "properties": {
            "_version": {
                "type": "string",
                "x-visibility": "private",
                "x-visibility-source": "convention",
            },
            "name": {"type": "string"},
        },
    }
    obj = ExplorerObject(
        file="conf.schema.yaml",
        title="Conf",
        kind="class",
        fields=2,
        origin="",
        parser="python",
        badges=(),
        excluded_by="",
        visibility="public",
    )
    entries = SourceEntries("app", "App", "git", "", True, [obj], [])
    fields = {
        f["name"]: f
        for f in object_detail(schema, "{}", obj, entries, SpaceObjects([entries], "origin"))[
            "fields"
        ]
    }
    assert fields["_version"]["visibility"] == "private"
    assert fields["_version"]["visibility_source"] == "convention"
    assert fields["name"]["visibility"] == "unknown"


def test_index_porte_la_visibilite(tmp_path: Path) -> None:
    from ossie_studio.sources.explorer import collect_source_entries

    raw = tmp_path / "sources" / "app" / "raw"
    raw.mkdir(parents=True)
    (tmp_path / "sources/app/manifest.yaml").write_text(_manifest_git(), encoding="utf-8")
    (raw / "a.schema.yaml").write_text(
        dump_schema(
            {
                "$id": "a.schema.yaml",
                "title": "A",
                "x-language": "java",
                "x-kind": "class",
                "x-visibility": "internal",
                "x-visibility-source": "package-private",
            }
        )
    )
    (raw / "t.schema.yaml").write_text(
        dump_schema({"$id": "t.schema.yaml", "title": "T", "x-kind": "table"})
    )
    objs = {o.file: o for o in collect_source_entries(tmp_path, "app").objects}
    assert (objs["a.schema.yaml"].visibility, objs["a.schema.yaml"].visibility_source) == (
        "internal",
        "package-private",
    )
    assert objs["t.schema.yaml"].visibility == ""


def _manifest_git() -> str:
    return _manifest("app")


def _exposure_case(asset: dict | None) -> dict:
    schema = {"title": "O", "properties": {f"f{i}": {"type": "string"} for i in range(9)}}
    obj = ExplorerObject(
        file="o.schema.yaml",
        title="O",
        kind="class",
        fields=9,
        origin="",
        parser="",
        badges=(),
        excluded_by="",
    )
    entries = SourceEntries("app", "App", "git", "", True, [obj], [])
    return object_detail(schema, "{}", obj, entries, SpaceObjects([entries], "origin"), asset)


def test_oeil_champs_retenus_dans_l_asset() -> None:
    asset = {"properties": {f"f{i}": {} for i in range(7)}}
    detail = _exposure_case(asset)
    assert [f["exposed"] for f in detail["fields"]] == [True] * 7 + [False] * 2
    assert detail["exposure"] == {"retained": True, "exposed": 7, "hidden": 2, "missing": []}


def test_oeil_objet_non_retenu() -> None:
    detail = _exposure_case(None)
    assert not any(f["exposed"] for f in detail["fields"])
    assert detail["exposure"]["retained"] is False and detail["exposure"]["hidden"] == 9


def test_oeil_champ_expose_disparu_du_raw() -> None:
    asset = {"properties": {"f0": {}, "ancien": {}}}
    detail = _exposure_case(asset)
    assert "ancien" not in [f["name"] for f in detail["fields"]]
    assert detail["exposure"]["missing"] == ["ancien"]


def test_historique_lisible() -> None:
    from ossie_studio.sources.explorer_history import schema_changes

    before = json.dumps(
        {
            "description": "a",
            "required": ["total"],
            "properties": {"total": {"type": "number"}, "old": {"type": "string"}},
        }
    )
    after = json.dumps(
        {
            "description": "b",
            "properties": {"total": {"$ref": "money.schema.yaml"}, "new": {"type": "string"}},
        }
    )
    codes = schema_changes(before, after)
    assert {"code": "field_type", "field": "total", "before": "number", "after": "money"} in codes
    assert {"code": "field_required", "field": "total", "required": False} in codes
    assert {"code": "field_added", "field": "new"} in codes
    assert {"code": "field_removed", "field": "old"} in codes
    assert {"code": "description_changed"} in codes
    # Réordonnancement sans changement de contenu : rien.
    same = json.dumps({"properties": {"b": {"type": "string"}, "a": {"type": "string"}}})
    other = json.dumps({"properties": {"a": {"type": "string"}, "b": {"type": "string"}}})
    assert schema_changes(same, other) == []
    assert schema_changes(None, other) == [{"code": "created"}]
