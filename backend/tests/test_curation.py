from __future__ import annotations

import json
from pathlib import Path

import pytest

from ossie_studio.sources.curation import (
    AssetNotRetainedError,
    AssetOriginMissingError,
    apply_enrichment,
    build_asset,
    enrich_asset_fn,
    get_object_detail,
    list_curation,
    read_enrichment,
    remove_asset_fn,
    retain_asset_fn,
)

_RAW = {
    "$id": "app-user.json",
    "title": "User",
    "type": "object",
    "x-language": "csharp",
    "x-kind": "class",
    "x-visibility": "public",
    "properties": {
        "a": {"type": "string", "x-visibility": "public"},
        "b": {"type": "integer", "x-visibility": "public"},
        "c": {"type": "boolean", "x-visibility": "public"},
    },
    "required": ["a", "b"],
}


def test_build_asset_entier() -> None:
    asset = build_asset(_RAW, None, None, "raw/app-user.json")
    assert asset["x-origin"] == "raw/app-user.json"
    assert asset["$id"] == _RAW["$id"]  # même $id que le brut
    assert set(asset["properties"]) == {"a", "b", "c"}
    assert asset["required"] == ["a", "b"]


def test_build_asset_champs_choisis() -> None:
    asset = build_asset(_RAW, ["a", "c"], None, "raw/app-user.json")
    assert set(asset["properties"]) == {"a", "c"}
    # required réduit aux champs gardés (b est écarté).
    assert asset["required"] == ["a"]


def test_build_asset_preserve_enrichissement_x() -> None:
    existing = {
        "x-business-name": "Utilisateur",
        "x-origin": "raw/app-user.json",
        "properties": {
            "a": {"type": "string", "x-visibility": "public", "x-description": "le nom"},
        },
    }
    asset = build_asset(_RAW, ["a", "b"], existing, "raw/app-user.json")
    # Enrichissement d'objet conservé.
    assert asset["x-business-name"] == "Utilisateur"
    # Enrichissement de champ conservé, structure reprise du brut.
    assert asset["properties"]["a"]["x-description"] == "le nom"
    assert asset["properties"]["a"]["type"] == "string"


def test_build_asset_enum_sans_proprietes() -> None:
    raw = {"$id": "app-s.json", "title": "S", "x-kind": "enum", "enum": ["A", "B"]}
    asset = build_asset(raw, None, None, "raw/app-s.json")
    assert asset["enum"] == ["A", "B"]
    assert asset["x-origin"] == "raw/app-s.json"


def _seed_raw(tmp_path: Path, name: str, data: dict) -> None:
    raw = tmp_path / "sources" / "api" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / name).write_text(json.dumps(data), encoding="utf-8")


def _seed_asset(tmp_path: Path, name: str, data: dict) -> None:
    assets = tmp_path / "sources" / "api" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / name).write_text(json.dumps(data), encoding="utf-8")


def test_list_curation_etats(tmp_path: Path) -> None:
    _seed_raw(tmp_path, "a.json", {"title": "A", "x-kind": "table",
                                   "properties": {"x": {}, "y": {}}})
    _seed_raw(tmp_path, "b.json", {"title": "B", "properties": {"x": {}, "y": {}}})
    _seed_raw(tmp_path, "c.json", {"title": "C", "properties": {"x": {}}})
    _seed_asset(tmp_path, "a.json", {"title": "A", "properties": {"x": {}, "y": {}}})  # full
    _seed_asset(tmp_path, "b.json", {"title": "B", "properties": {"x": {}}})            # partial
    states = {o.path: o.retained for o in list_curation(tmp_path, "api")}
    assert states == {"a.json": "full", "b.json": "partial", "c.json": "none"}
    by_path = {o.path: o for o in list_curation(tmp_path, "api")}
    assert by_path["a.json"].kind == "table" and by_path["a.json"].fields == 2


def test_retain_writer_ecrit_asset(tmp_path: Path) -> None:
    _seed_raw(tmp_path, "app-user.json", _RAW)
    retain_asset_fn("api", "app-user.json", ["a"])(tmp_path)
    asset = json.loads((tmp_path / "sources" / "api" / "assets" / "app-user.json").read_text())
    assert asset["x-origin"] == "raw/app-user.json"
    assert set(asset["properties"]) == {"a"}


def test_retain_writer_refuse_origine_absente(tmp_path: Path) -> None:
    (tmp_path / "sources" / "api" / "raw").mkdir(parents=True)
    with pytest.raises(AssetOriginMissingError):
        retain_asset_fn("api", "absent.json", None)(tmp_path)


def test_object_detail_champs_et_ref(tmp_path: Path) -> None:
    _seed_raw(tmp_path, "app-order.json", {
        "title": "Order", "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "ligne": {"$ref": "app-ligne.json"},
            "tags": {"type": "array", "items": {"$ref": "app-tag.json"}},
        },
    })
    _seed_asset(tmp_path, "app-order.json", {"title": "Order", "properties": {"id": {}}})
    detail = get_object_detail(tmp_path, "api", "app-order.json")
    assert detail is not None
    fields = {f.name: f for f in detail.fields}
    assert fields["id"].retained is True and fields["id"].ref is False
    assert fields["ligne"].ref is True and fields["ligne"].retained is False
    assert fields["tags"].ref is True  # $ref sous items
    assert get_object_detail(tmp_path, "api", "absent.json") is None


def test_remove_writer_supprime(tmp_path: Path) -> None:
    _seed_raw(tmp_path, "app-user.json", _RAW)
    _seed_asset(tmp_path, "app-user.json", _RAW)
    remove_asset_fn("api", "app-user.json")(tmp_path)
    assert not (tmp_path / "sources" / "api" / "assets" / "app-user.json").exists()


def test_apply_enrichment_pose_et_retire() -> None:
    node = {"type": "string", "x-visibility": "public", "x-source": [{"fichier": "a"}]}
    apply_enrichment(
        node, {"business_name": "Nom", "description": "d", "concept": "", "tags": ["t"]}
    )
    assert node["x-businessName"] == "Nom" and node["x-description"] == "d"
    assert node["x-tags"] == ["t"] and "x-concept" not in node  # vide → non stocké
    # Clés standard et clés parseur intactes.
    assert node["type"] == "string" and node["x-source"] == [{"fichier": "a"}]
    # Vider une clé la retire.
    apply_enrichment(node, {"business_name": "", "description": "", "concept": "", "tags": []})
    assert "x-businessName" not in node and "x-tags" not in node


def test_enrich_writer_objet_et_champ(tmp_path: Path) -> None:
    _seed_asset(tmp_path, "app-user.json", {
        "$id": "app-user.json", "title": "User", "type": "object",
        "x-origin": "raw/app-user.json",
        "properties": {"a": {"type": "string", "x-visibility": "public"}},
    })
    enrich_asset_fn(
        "api", "app-user.json",
        {"business_name": "Utilisateur", "description": "", "concept": "", "tags": []},
        {"a": {"business_name": "", "description": "le nom", "concept": "", "tags": []}},
    )(tmp_path)
    asset = json.loads((tmp_path / "sources" / "api" / "assets" / "app-user.json").read_text())
    assert asset["x-businessName"] == "Utilisateur"
    assert asset["properties"]["a"]["x-description"] == "le nom"


def test_enrich_writer_refuse_non_retenu(tmp_path: Path) -> None:
    (tmp_path / "sources" / "api" / "assets").mkdir(parents=True)
    with pytest.raises(AssetNotRetainedError):
        enrich_asset_fn("api", "absent.json", {"business_name": "x"}, {})(tmp_path)


def test_read_enrichment(tmp_path: Path) -> None:
    _seed_asset(tmp_path, "app-user.json", {
        "$id": "app-user.json", "title": "User", "x-businessName": "Utilisateur",
        "x-source": [{"fichier": "a.cs"}], "description": "doc du code",
        "properties": {"a": {"type": "string", "x-description": "le nom"}},
    })
    view = read_enrichment(tmp_path, "api", "app-user.json")
    assert view is not None
    assert view.object.business_name == "Utilisateur"
    # Contexte lecture seule : description standard + x- du parseur.
    assert "x-source" in view.object.readonly
    assert view.object.readonly.get("description") == "doc du code"
    field = next(f for f in view.fields if f.name == "a")
    assert field.description == "le nom"
    assert read_enrichment(tmp_path, "api", "absent.json") is None
