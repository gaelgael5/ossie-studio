from __future__ import annotations

import json
from pathlib import Path

import pytest

from ossie_studio.parsers.conventions import dump_schema
from ossie_studio.sources.curation import (
    AssetNotRetainedError,
    AssetOriginMissingError,
    apply_enrichment,
    build_asset,
    enrich_asset_fn,
    get_object_detail,
    list_curation,
    read_enrichment,
    refresh_asset,
    refresh_assets_fn,
    refresh_targets,
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


# --- Rafraîchir depuis le brut -----------------------------------------------


def test_refresh_type_modifie_garde_enrichissement() -> None:
    raw = {"$id": "u.json", "title": "U", "type": "object", "x-source": [{"ligne": 9}],
           "properties": {"a": {"type": "integer", "x-visibility": "public"}}}
    asset = {"$id": "u.json", "title": "U", "type": "object", "x-origin": "raw/u.json",
             "x-businessName": "Utilisateur", "x-source": [{"ligne": 3}],
             "properties": {"a": {"type": "string", "x-visibility": "public",
                                  "x-businessName": "identifiant"}}}
    out = refresh_asset(raw, asset)
    # Structure (standard) reprise du brut ; enrichissement x- conservé.
    assert out["properties"]["a"]["type"] == "integer"
    assert out["properties"]["a"]["x-businessName"] == "identifiant"
    assert out["x-businessName"] == "Utilisateur"
    assert out["x-origin"] == "raw/u.json"
    # x- jamais touché : la métadonnée parseur de l'asset reste (pas celle du brut).
    assert out["x-source"] == [{"ligne": 3}]


def test_refresh_champ_disparu_garde() -> None:
    raw = {"$id": "u.json", "type": "object", "properties": {"a": {"type": "string"}}}
    asset = {"$id": "u.json", "x-origin": "raw/u.json", "properties": {
        "a": {"type": "string"},
        "b": {"type": "integer", "x-businessName": "ancien"},  # disparu du brut
    }}
    out = refresh_asset(raw, asset)
    assert "b" in out["properties"]  # gardé, rien n'est perdu
    assert out["properties"]["b"]["x-businessName"] == "ancien"


def test_refresh_champ_apparu_ignore() -> None:
    raw = {"$id": "u.json", "type": "object", "required": ["a", "c"],
           "properties": {"a": {"type": "string"}, "c": {"type": "boolean"}}}
    asset = {"$id": "u.json", "x-origin": "raw/u.json", "properties": {"a": {"type": "string"}}}
    out = refresh_asset(raw, asset)
    assert set(out["properties"]) == {"a"}  # c (apparu) n'est pas ajouté
    assert out.get("required") == ["a"]     # required réduit aux champs retenus


def test_refresh_idempotent() -> None:
    raw = {"$id": "u.json", "type": "object", "properties": {"a": {"type": "integer"}}}
    asset = {"$id": "u.json", "x-origin": "raw/u.json",
             "properties": {"a": {"type": "string", "x-businessName": "id"}}}
    once = refresh_asset(raw, asset)
    twice = refresh_asset(raw, once)
    assert dump_schema(once) == dump_schema(twice)


def test_refresh_targets_deja_a_jour(tmp_path: Path) -> None:
    # Un asset construit depuis son brut est déjà à jour : rien à rafraîchir.
    _seed_raw(tmp_path, "u.json", _RAW)
    retain_asset_fn("api", "u.json", None)(tmp_path)
    assert refresh_targets(tmp_path, "api", None) == []


def test_refresh_lot_un_commit_liste(tmp_path: Path) -> None:
    _seed_raw(tmp_path, "u.json", {"$id": "u.json", "type": "object",
                                   "properties": {"a": {"type": "integer"}}})
    _seed_asset(tmp_path, "u.json", {"$id": "u.json", "x-origin": "raw/u.json",
                                     "properties": {"a": {"type": "string",
                                                          "x-businessName": "id"}}})
    _seed_raw(tmp_path, "v.json", {"$id": "v.json", "type": "object",
                                   "properties": {"x": {"type": "string"}}})
    _seed_asset(tmp_path, "v.json", {"$id": "v.json", "type": "object", "x-origin": "raw/v.json",
                                     "properties": {"x": {"type": "string"}}})  # déjà à jour
    assert refresh_targets(tmp_path, "api", None) == ["u.json"]
    refresh_assets_fn("api", ["u.json"])(tmp_path)
    out = json.loads((tmp_path / "sources" / "api" / "assets" / "u.json").read_text())
    assert out["properties"]["a"]["type"] == "integer"
    assert out["properties"]["a"]["x-businessName"] == "id"


def test_refresh_orphelin_non_touche(tmp_path: Path) -> None:
    # Asset sans brut : orphelin, jamais rafraîchi ni supprimé.
    _seed_asset(tmp_path, "gone.json", {"$id": "gone.json", "x-origin": "raw/gone.json",
                                        "properties": {"a": {"type": "string"}}})
    assert refresh_targets(tmp_path, "api", None) == []
    orphans = {o.path: o.orphan for o in list_curation(tmp_path, "api")}
    assert orphans == {"gone.json": True}


def test_object_detail_champ_disparu(tmp_path: Path) -> None:
    _seed_raw(tmp_path, "u.json", {"title": "U", "type": "object",
                                   "properties": {"a": {"type": "string"}}})
    _seed_asset(tmp_path, "u.json", {"title": "U", "x-origin": "raw/u.json", "properties": {
        "a": {"type": "string"}, "b": {"type": "integer"}}})  # b disparu du brut
    detail = get_object_detail(tmp_path, "api", "u.json")
    assert detail is not None
    fields = {f.name: f for f in detail.fields}
    assert fields["b"].missing is True and fields["b"].retained is True
    assert fields["a"].missing is False
