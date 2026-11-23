from __future__ import annotations

import json
from pathlib import Path

import pytest

from ossie_studio.parsers.conventions import dump_schema, load_schema
from ossie_studio.sources.asset_files import load_asset
from ossie_studio.sources.curation import (
    AssetNotRetainedError,
    AssetOriginMissingError,
    ReviewDecisions,
    apply_enrichment,
    apply_review_fn,
    build_asset,
    build_review,
    enrich_asset_fn,
    get_object_detail,
    list_curation,
    published_raw,
    read_enrichment,
    read_tri_rules_raw,
    refresh_asset,
    refresh_assets_fn,
    refresh_targets,
    remove_asset_fn,
    retain_asset_fn,
    set_tri_rules_fn,
)
from ossie_studio.sources.manifest import SourceManifest, dump_manifest
from ossie_studio.sources.tri import compile_rules

_RAW = {
    "$id": "app-user.schema.yaml",
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
    asset = build_asset(_RAW, None, None, "raw/app-user.schema.yaml")
    assert asset["x-origin"] == "raw/app-user.schema.yaml"
    assert asset["$id"] == _RAW["$id"]  # même $id que le brut
    assert set(asset["properties"]) == {"a", "b", "c"}
    assert asset["required"] == ["a", "b"]


def test_build_asset_champs_choisis() -> None:
    asset = build_asset(_RAW, ["a", "c"], None, "raw/app-user.schema.yaml")
    assert set(asset["properties"]) == {"a", "c"}
    # required réduit aux champs gardés (b est écarté).
    assert asset["required"] == ["a"]


def test_build_asset_preserve_enrichissement_x() -> None:
    existing = {
        "x-business-name": "Utilisateur",
        "x-origin": "raw/app-user.schema.yaml",
        "properties": {
            "a": {"type": "string", "x-visibility": "public", "x-description": "le nom"},
        },
    }
    asset = build_asset(_RAW, ["a", "b"], existing, "raw/app-user.schema.yaml")
    # Enrichissement d'objet conservé.
    assert asset["x-business-name"] == "Utilisateur"
    # Enrichissement de champ conservé, structure reprise du brut.
    assert asset["properties"]["a"]["x-description"] == "le nom"
    assert asset["properties"]["a"]["type"] == "string"


def test_build_asset_enum_sans_proprietes() -> None:
    raw = {"$id": "app-s.schema.yaml", "title": "S", "x-kind": "enum", "enum": ["A", "B"]}
    asset = build_asset(raw, None, None, "raw/app-s.schema.yaml")
    assert asset["enum"] == ["A", "B"]
    assert asset["x-origin"] == "raw/app-s.schema.yaml"


def _seed_raw(tmp_path: Path, name: str, data: dict) -> None:
    raw = tmp_path / "sources" / "api" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / name).write_text(json.dumps(data), encoding="utf-8")


def _seed_asset(tmp_path: Path, name: str, data: dict) -> None:
    assets = tmp_path / "sources" / "api" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / name).write_text(json.dumps(data), encoding="utf-8")


def test_list_curation_etats(tmp_path: Path) -> None:
    _seed_raw(
        tmp_path,
        "a.schema.yaml",
        {"title": "A", "x-kind": "table", "properties": {"x": {}, "y": {}}},
    )
    _seed_raw(tmp_path, "b.schema.yaml", {"title": "B", "properties": {"x": {}, "y": {}}})
    _seed_raw(tmp_path, "c.schema.yaml", {"title": "C", "properties": {"x": {}}})
    _seed_asset(tmp_path, "a.schema.yaml", {"title": "A", "properties": {"x": {}, "y": {}}})  # full
    _seed_asset(tmp_path, "b.schema.yaml", {"title": "B", "properties": {"x": {}}})  # partial
    states = {o.path: o.retained for o in list_curation(tmp_path, "api")}
    assert states == {"a.schema.yaml": "full", "b.schema.yaml": "partial", "c.schema.yaml": "none"}
    by_path = {o.path: o for o in list_curation(tmp_path, "api")}
    assert by_path["a.schema.yaml"].kind == "table" and by_path["a.schema.yaml"].fields == 2


def test_retain_writer_ecrit_asset(tmp_path: Path) -> None:
    _seed_raw(tmp_path, "app-user.schema.yaml", _RAW)
    retain_asset_fn("api", "app-user.schema.yaml", ["a"])(tmp_path)
    asset = load_schema(
        (tmp_path / "sources" / "api" / "assets" / "app-user.schema.yaml").read_text()
    )
    assert asset["x-origin"] == "raw/app-user.schema.yaml"
    assert set(asset["properties"]) == {"a"}


def test_retain_writer_refuse_origine_absente(tmp_path: Path) -> None:
    (tmp_path / "sources" / "api" / "raw").mkdir(parents=True)
    with pytest.raises(AssetOriginMissingError):
        retain_asset_fn("api", "absent.schema.yaml", None)(tmp_path)


def test_object_detail_champs_et_ref(tmp_path: Path) -> None:
    _seed_raw(
        tmp_path,
        "app-order.schema.yaml",
        {
            "title": "Order",
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "ligne": {"$ref": "app-ligne.schema.yaml"},
                "tags": {"type": "array", "items": {"$ref": "app-tag.schema.yaml"}},
            },
        },
    )
    _seed_asset(tmp_path, "app-order.schema.yaml", {"title": "Order", "properties": {"id": {}}})
    detail = get_object_detail(tmp_path, "api", "app-order.schema.yaml")
    assert detail is not None
    fields = {f.name: f for f in detail.fields}
    assert fields["id"].retained is True and fields["id"].ref is False
    assert fields["ligne"].ref is True and fields["ligne"].retained is False
    assert fields["tags"].ref is True  # $ref sous items
    assert get_object_detail(tmp_path, "api", "absent.schema.yaml") is None


def test_remove_writer_supprime(tmp_path: Path) -> None:
    _seed_raw(tmp_path, "app-user.schema.yaml", _RAW)
    _seed_asset(tmp_path, "app-user.schema.yaml", _RAW)
    remove_asset_fn("api", "app-user.schema.yaml")(tmp_path)
    assert not (tmp_path / "sources" / "api" / "assets" / "app-user.schema.yaml").exists()


def test_apply_enrichment_pose_et_retire() -> None:
    node = {"type": "string", "x-visibility": "public", "x-source": [{"file": "a"}]}
    apply_enrichment(
        node, {"business_name": "Nom", "description": "d", "concept": "", "tags": ["t"]}
    )
    assert node["x-businessName"] == "Nom" and node["x-description"] == "d"
    assert node["x-tags"] == ["t"] and "x-concept" not in node  # vide → non stocké
    # Clés standard et clés parseur intactes.
    assert node["type"] == "string" and node["x-source"] == [{"file": "a"}]
    # Vider une clé la retire.
    apply_enrichment(node, {"business_name": "", "description": "", "concept": "", "tags": []})
    assert "x-businessName" not in node and "x-tags" not in node


def test_enrich_writer_objet_et_champ(tmp_path: Path) -> None:
    _seed_asset(
        tmp_path,
        "app-user.schema.yaml",
        {
            "$id": "app-user.schema.yaml",
            "title": "User",
            "type": "object",
            "x-origin": "raw/app-user.schema.yaml",
            "properties": {"a": {"type": "string", "x-visibility": "public"}},
        },
    )
    enrich_asset_fn(
        "api",
        "app-user.schema.yaml",
        {"business_name": "Utilisateur", "description": "", "concept": "", "tags": []},
        {"a": {"business_name": "", "description": "le nom", "concept": "", "tags": []}},
    )(tmp_path)
    assets = tmp_path / "sources" / "api" / "assets"
    # Ticket 7a284e55 : le travail humain est dans le `.extend.yaml`, le schéma
    # reste standard ; `load_asset` rend la vue réunie.
    asset = load_asset(assets / "app-user.schema.yaml")
    assert asset["x-businessName"] == "Utilisateur"
    assert asset["properties"]["a"]["x-description"] == "le nom"
    schema = load_schema((assets / "app-user.schema.yaml").read_text())
    assert "x-businessName" not in schema
    assert "x-description" not in schema["properties"]["a"]
    assert load_schema((assets / "app-user.extend.yaml").read_text()) == {
        "object": {"businessName": "Utilisateur"},
        "fields": {"a": {"description": "le nom"}},
    }


def test_enrich_writer_refuse_non_retenu(tmp_path: Path) -> None:
    (tmp_path / "sources" / "api" / "assets").mkdir(parents=True)
    with pytest.raises(AssetNotRetainedError):
        enrich_asset_fn("api", "absent.schema.yaml", {"business_name": "x"}, {})(tmp_path)


def test_read_enrichment(tmp_path: Path) -> None:
    _seed_asset(
        tmp_path,
        "app-user.schema.yaml",
        {
            "$id": "app-user.schema.yaml",
            "title": "User",
            "x-businessName": "Utilisateur",
            "x-source": [{"file": "a.cs"}],
            "description": "doc du code",
            "properties": {"a": {"type": "string", "x-description": "le nom"}},
        },
    )
    view = read_enrichment(tmp_path, "api", "app-user.schema.yaml")
    assert view is not None
    assert view.object.business_name == "Utilisateur"
    # Contexte lecture seule : description standard + x- du parseur.
    assert "x-source" in view.object.readonly
    assert view.object.readonly.get("description") == "doc du code"
    field = next(f for f in view.fields if f.name == "a")
    assert field.description == "le nom"
    assert read_enrichment(tmp_path, "api", "absent.schema.yaml") is None


# --- Rafraîchir depuis le brut -----------------------------------------------


def test_refresh_type_modifie_garde_enrichissement() -> None:
    raw = {
        "$id": "u.schema.yaml",
        "title": "U",
        "type": "object",
        "x-source": [{"line": 9}],
        "properties": {"a": {"type": "integer", "x-visibility": "public"}},
    }
    asset = {
        "$id": "u.schema.yaml",
        "title": "U",
        "type": "object",
        "x-origin": "raw/u.schema.yaml",
        "x-businessName": "Utilisateur",
        "x-source": [{"line": 3}],
        "properties": {
            "a": {"type": "string", "x-visibility": "public", "x-businessName": "identifiant"}
        },
    }
    out = refresh_asset(raw, asset)
    # Structure (standard) reprise du brut ; enrichissement x- conservé.
    assert out["properties"]["a"]["type"] == "integer"
    assert out["properties"]["a"]["x-businessName"] == "identifiant"
    assert out["x-businessName"] == "Utilisateur"
    assert out["x-origin"] == "raw/u.schema.yaml"
    # x- jamais touché : la métadonnée parseur de l'asset reste (pas celle du brut).
    assert out["x-source"] == [{"line": 3}]


def test_refresh_champ_disparu_garde() -> None:
    raw = {"$id": "u.schema.yaml", "type": "object", "properties": {"a": {"type": "string"}}}
    asset = {
        "$id": "u.schema.yaml",
        "x-origin": "raw/u.schema.yaml",
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "integer", "x-businessName": "ancien"},  # disparu du brut
        },
    }
    out = refresh_asset(raw, asset)
    assert "b" in out["properties"]  # gardé, rien n'est perdu
    assert out["properties"]["b"]["x-businessName"] == "ancien"


def test_refresh_champ_apparu_ignore() -> None:
    raw = {
        "$id": "u.schema.yaml",
        "type": "object",
        "required": ["a", "c"],
        "properties": {"a": {"type": "string"}, "c": {"type": "boolean"}},
    }
    asset = {
        "$id": "u.schema.yaml",
        "x-origin": "raw/u.schema.yaml",
        "properties": {"a": {"type": "string"}},
    }
    out = refresh_asset(raw, asset)
    assert set(out["properties"]) == {"a"}  # c (apparu) n'est pas ajouté
    assert out.get("required") == ["a"]  # required réduit aux champs retenus


def test_refresh_idempotent() -> None:
    raw = {"$id": "u.schema.yaml", "type": "object", "properties": {"a": {"type": "integer"}}}
    asset = {
        "$id": "u.schema.yaml",
        "x-origin": "raw/u.schema.yaml",
        "properties": {"a": {"type": "string", "x-businessName": "id"}},
    }
    once = refresh_asset(raw, asset)
    twice = refresh_asset(raw, once)
    assert dump_schema(once) == dump_schema(twice)


def test_refresh_targets_deja_a_jour(tmp_path: Path) -> None:
    # Un asset construit depuis son brut est déjà à jour : rien à rafraîchir.
    _seed_raw(tmp_path, "u.schema.yaml", _RAW)
    retain_asset_fn("api", "u.schema.yaml", None)(tmp_path)
    assert refresh_targets(tmp_path, "api", None) == []


def test_refresh_lot_un_commit_liste(tmp_path: Path) -> None:
    _seed_raw(
        tmp_path,
        "u.schema.yaml",
        {"$id": "u.schema.yaml", "type": "object", "properties": {"a": {"type": "integer"}}},
    )
    _seed_asset(
        tmp_path,
        "u.schema.yaml",
        {
            "$id": "u.schema.yaml",
            "x-origin": "raw/u.schema.yaml",
            "properties": {"a": {"type": "string", "x-businessName": "id"}},
        },
    )
    _seed_raw(
        tmp_path,
        "v.schema.yaml",
        {"$id": "v.schema.yaml", "type": "object", "properties": {"x": {"type": "string"}}},
    )
    _seed_asset(
        tmp_path,
        "v.schema.yaml",
        {
            "$id": "v.schema.yaml",
            "type": "object",
            "x-origin": "raw/v.schema.yaml",
            "properties": {"x": {"type": "string"}},
        },
    )  # déjà à jour
    assert refresh_targets(tmp_path, "api", None) == ["u.schema.yaml"]
    refresh_assets_fn("api", ["u.schema.yaml"])(tmp_path)
    out = load_asset(tmp_path / "sources" / "api" / "assets" / "u.schema.yaml")
    assert out["properties"]["a"]["type"] == "integer"
    assert out["properties"]["a"]["x-businessName"] == "id"


def test_refresh_orphelin_non_touche(tmp_path: Path) -> None:
    # Asset sans brut : orphelin, jamais rafraîchi ni supprimé.
    _seed_asset(
        tmp_path,
        "gone.schema.yaml",
        {
            "$id": "gone.schema.yaml",
            "x-origin": "raw/gone.schema.yaml",
            "properties": {"a": {"type": "string"}},
        },
    )
    assert refresh_targets(tmp_path, "api", None) == []
    orphans = {o.path: o.orphan for o in list_curation(tmp_path, "api")}
    assert orphans == {"gone.schema.yaml": True}


def test_object_detail_champ_disparu(tmp_path: Path) -> None:
    _seed_raw(
        tmp_path,
        "u.schema.yaml",
        {"title": "U", "type": "object", "properties": {"a": {"type": "string"}}},
    )
    _seed_asset(
        tmp_path,
        "u.schema.yaml",
        {
            "title": "U",
            "x-origin": "raw/u.schema.yaml",
            "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
        },
    )  # b disparu du brut
    detail = get_object_detail(tmp_path, "api", "u.schema.yaml")
    assert detail is not None
    fields = {f.name: f for f in detail.fields}
    assert fields["b"].missing is True and fields["b"].retained is True
    assert fields["a"].missing is False


# --- Revue des changements ----------------------------------------------------


def test_review_tout_nouveau_par_rapport_au_publie() -> None:
    # Base = brut publié (ticket aad1d3c6) : vide → tout le brut de ma vue est nouveau.
    head = {
        "a.schema.yaml": {"title": "A", "type": "object", "properties": {"x": {"type": "string"}}},
        "b.schema.yaml": {"title": "B", "type": "object", "properties": {}},
    }
    result = build_review({}, head, {})
    assert {o.path: o.status for o in result.objects} == {
        "a.schema.yaml": "new",
        "b.schema.yaml": "new",
    }


def test_review_montre_retenu_pas_bruit() -> None:
    # Critère 2 : champ retenu changé + champ apparu montrés ; objet non retenu = bruit.
    base = {
        "u.schema.yaml": {"type": "object", "properties": {"a": {"type": "string"}}},
        "n.schema.yaml": {"type": "object", "properties": {"k": {"type": "string"}}},
    }
    head = {
        "u.schema.yaml": {
            "type": "object",
            "properties": {
                "a": {"type": "integer"},  # changé
                "b": {"type": "string"},  # apparu
            },
        },
        "n.schema.yaml": {"type": "object", "properties": {"k": {"type": "integer"}}},  # bruit
    }
    assets = {
        "u.schema.yaml": {
            "type": "object",
            "x-origin": "raw/u.schema.yaml",
            "properties": {"a": {"type": "string", "x-businessName": "id"}},
        }
    }
    result = build_review(base, head, assets)
    by_path = {o.path: o for o in result.objects}
    assert set(by_path) == {"u.schema.yaml"}
    assert {f.name: f.change for f in by_path["u.schema.yaml"].fields} == {
        "a": "changed",
        "b": "appeared",
    }


def test_review_champ_disparu_enrichissement_perdu() -> None:
    # Critère 5 (données) : champ disparu + enrichissements qui seraient perdus.
    base = {
        "u.schema.yaml": {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
        }
    }
    head = {"u.schema.yaml": {"type": "object", "properties": {"a": {"type": "string"}}}}
    assets = {
        "u.schema.yaml": {
            "type": "object",
            "x-origin": "raw/u.schema.yaml",
            "properties": {
                "a": {"type": "string"},
                "b": {"type": "string", "x-businessName": "ancien", "x-description": "doc"},
            },
        }
    }
    field = next(f for o in build_review(base, head, assets).objects for f in o.fields)
    assert field.name == "b" and field.change == "disappeared"
    assert field.lost_enrichment == ["x-businessName", "x-description"]


def test_review_ecart_publie_reste_signale() -> None:
    """Disparition déjà publiée (absente de la base comme de ma vue) : l'asset ne
    correspond plus au brut, le champ et l'objet restent signalés."""
    head = {"u.schema.yaml": {"type": "object", "properties": {"a": {"type": "string"}}}}
    assets = {
        "u.schema.yaml": {
            "type": "object",
            "x-origin": "raw/u.schema.yaml",
            "properties": {
                "a": {"type": "string"},
                "b": {"type": "string", "x-description": "doc"},
            },
        },
        "gone.schema.yaml": {"title": "G", "type": "object", "properties": {}},
    }
    result = build_review(head, head, assets)
    by_path = {o.path: o for o in result.objects}
    assert by_path["gone.schema.yaml"].status == "object-disappeared"
    assert [(f.name, f.change) for f in by_path["u.schema.yaml"].fields] == [("b", "disappeared")]


def test_published_raw_reconstruit_la_base() -> None:
    view = {"a.schema.yaml": {"title": "A2"}, "new.schema.yaml": {"title": "N"}}
    # a.schema.yaml modifié par le brouillon, new.schema.yaml créé, del.schema.yaml supprimé.
    texts = {
        "a.schema.yaml": '{"title": "A1"}',
        "new.schema.yaml": None,
        "del.schema.yaml": '{"title": "D"}',
    }
    assert published_raw(view, texts) == {
        "a.schema.yaml": {"title": "A1"},
        "del.schema.yaml": {"title": "D"},
    }


def test_review_objet_disparu() -> None:
    base = {"u.schema.yaml": {"type": "object", "properties": {"a": {"type": "string"}}}}
    assets = {
        "u.schema.yaml": {
            "title": "U",
            "type": "object",
            "x-origin": "raw/u.schema.yaml",
            "properties": {"a": {"type": "string"}},
        }
    }
    result = build_review(base, {}, assets)
    assert [(o.path, o.status) for o in result.objects] == [("u.schema.yaml", "object-disappeared")]


def test_review_deja_revu_rien() -> None:
    same = {"u.schema.yaml": {"type": "object", "properties": {"a": {"type": "string"}}}}
    assets = {
        "u.schema.yaml": {
            "type": "object",
            "x-origin": "raw/u.schema.yaml",
            "properties": {"a": {"type": "string"}},
        }
    }
    assert build_review(same, same, assets).objects == []


def test_apply_review_applique_les_decisions(tmp_path: Path) -> None:
    _seed_raw(
        tmp_path,
        "u.schema.yaml",
        {
            "$id": "u.schema.yaml",
            "type": "object",
            "required": ["a"],
            "properties": {"a": {"type": "integer"}, "b": {"type": "string"}},
        },
    )
    _seed_asset(
        tmp_path,
        "u.schema.yaml",
        {
            "$id": "u.schema.yaml",
            "type": "object",
            "x-origin": "raw/u.schema.yaml",
            "properties": {"a": {"type": "string", "x-businessName": "id"}},
        },
    )
    decisions = ReviewDecisions(
        apply_fields={"u.schema.yaml": ["a"]},
        take_fields={"u.schema.yaml": ["b"]},
        remove_fields={},
        remove_objects=[],
        retain_objects=[],
    )
    apply_review_fn("api", decisions)(tmp_path)
    asset = load_asset(tmp_path / "sources" / "api" / "assets" / "u.schema.yaml")
    assert asset["properties"]["a"]["type"] == "integer"  # champ changé appliqué
    assert asset["properties"]["a"]["x-businessName"] == "id"  # enrichissement conservé
    assert asset["properties"]["b"]["type"] == "string"  # champ apparu repris
    assert asset["required"] == ["a"]
    assert not (tmp_path / "sources" / "api" / "review.yaml").exists()  # plus de point de reprise


def test_apply_review_supprime_champ_objet_et_retient_nouveau(tmp_path: Path) -> None:
    _seed_raw(
        tmp_path,
        "u.schema.yaml",
        {"$id": "u.schema.yaml", "type": "object", "properties": {"a": {"type": "string"}}},
    )
    _seed_asset(
        tmp_path,
        "u.schema.yaml",
        {
            "$id": "u.schema.yaml",
            "type": "object",
            "x-origin": "raw/u.schema.yaml",
            "properties": {"a": {"type": "string"}, "b": {"type": "string", "x-businessName": "x"}},
        },
    )
    _seed_asset(
        tmp_path,
        "gone.schema.yaml",
        {
            "$id": "gone.schema.yaml",
            "x-origin": "raw/gone.schema.yaml",
            "properties": {"a": {"type": "string"}},
        },
    )
    _seed_raw(
        tmp_path,
        "new.schema.yaml",
        {"$id": "new.schema.yaml", "type": "object", "properties": {"k": {"type": "string"}}},
    )
    decisions = ReviewDecisions(
        apply_fields={},
        take_fields={},
        remove_fields={"u.schema.yaml": ["b"]},
        remove_objects=["gone.schema.yaml"],
        retain_objects=["new.schema.yaml"],
    )
    apply_review_fn("api", decisions)(tmp_path)
    u = load_schema((tmp_path / "sources" / "api" / "assets" / "u.schema.yaml").read_text())
    assert "b" not in u["properties"]  # champ disparu retiré
    assert not (
        tmp_path / "sources" / "api" / "assets" / "gone.schema.yaml"
    ).exists()  # objet retiré
    new_asset = load_schema(
        (tmp_path / "sources" / "api" / "assets" / "new.schema.yaml").read_text()
    )
    assert new_asset["x-origin"] == "raw/new.schema.yaml" and "k" in new_asset["properties"]


# --- Règles de tri -----------------------------------------------------------

_DTO_RULE = [{"target": "object-name", "pattern": "*Dto", "kind": "glob"}]


def test_list_curation_regle_cache_non_retenu(tmp_path: Path) -> None:
    _seed_raw(tmp_path, "a.schema.yaml", {"title": "UserDto", "properties": {"x": {}}})
    _seed_raw(tmp_path, "b.schema.yaml", {"title": "User", "properties": {"x": {}}})
    rules = compile_rules(_DTO_RULE)
    # Caché par défaut.
    paths = {o.path for o in list_curation(tmp_path, "api", rules)}
    assert paths == {"b.schema.yaml"}
    # Filtre « afficher les écartés » : montré, avec la règle.
    shown = {o.path: o for o in list_curation(tmp_path, "api", rules, show_excluded=True)}
    assert set(shown) == {"a.schema.yaml", "b.schema.yaml"}
    assert shown["a.schema.yaml"].excluded is True and shown["a.schema.yaml"].excluded_by


def test_list_curation_retenu_reste_visible_en_conflit(tmp_path: Path) -> None:
    _seed_raw(tmp_path, "a.schema.yaml", {"title": "UserDto", "properties": {"x": {}}})
    _seed_asset(tmp_path, "a.schema.yaml", {"title": "UserDto", "properties": {"x": {}}})  # retenu
    rules = compile_rules(_DTO_RULE)
    by_path = {o.path: o for o in list_curation(tmp_path, "api", rules)}
    assert "a.schema.yaml" in by_path  # jamais caché
    assert (
        by_path["a.schema.yaml"].excluded is False and by_path["a.schema.yaml"].excluded_by
    )  # conflit signalé


def test_build_review_regle_ecarte_nouvel_objet() -> None:
    head = {
        "a.schema.yaml": {"title": "UserDto", "type": "object", "properties": {}},
        "b.schema.yaml": {"title": "User", "type": "object", "properties": {}},
    }
    rules = compile_rules(_DTO_RULE)
    result = build_review({}, head, {}, rules)
    assert [o.path for o in result.objects] == ["b.schema.yaml"]  # a.schema.yaml écarté


def test_build_review_regle_ecarte_champ_apparu() -> None:
    base = {"u.schema.yaml": {"type": "object", "properties": {"a": {"type": "string"}}}}
    head = {
        "u.schema.yaml": {
            "type": "object",
            "properties": {"a": {"type": "string"}, "ETL_TS": {"type": "string"}},
        }
    }
    assets = {
        "u.schema.yaml": {
            "type": "object",
            "x-origin": "raw/u.schema.yaml",
            "properties": {"a": {"type": "string"}},
        }
    }
    rules = compile_rules([{"target": "field-name", "pattern": "ETL_*", "kind": "glob"}])
    result = build_review(base, head, assets, rules)
    assert result.objects == []  # le seul changement (ETL_TS apparu) est écarté


def _seed_manifest(tmp_path: Path) -> None:
    manifest = SourceManifest(
        slug="api",
        label="API",
        type="git",
        connection="forge",
        parameters={"repository": "o/r", "branch": "main", "parsers": ["csharp"]},
    )
    (tmp_path / "sources" / "api").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sources" / "api" / "manifest.yaml").write_text(
        dump_manifest(manifest), encoding="utf-8"
    )


def test_tri_rules_roundtrip(tmp_path: Path) -> None:
    _seed_manifest(tmp_path)
    assert read_tri_rules_raw(tmp_path, "api") == []
    rules = [{"target": "object-name", "pattern": "*Dto", "kind": "glob", "label": "DTO"}]
    set_tri_rules_fn("api", rules)(tmp_path)
    assert read_tri_rules_raw(tmp_path, "api") == rules
    # Le reste du manifeste est préservé.
    text = (tmp_path / "sources" / "api" / "manifest.yaml").read_text()
    assert "repository: o/r" in text and "parsers:" in text and "triage:" in text
