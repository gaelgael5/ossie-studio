"""Travail humain d'un asset dans `<clé>.extend.yaml` (ticket docflow 7a284e55).

Gael, 23/11/2026 : « La priorité est bien de garder les standards non altérés. »
Le `<clé>.schema.yaml` d'un asset ne porte plus aucune clé humaine.
"""

from __future__ import annotations

from pathlib import Path

from ossie_studio.parsers.conventions import dump_schema, load_schema
from ossie_studio.sources.asset_files import (
    asset_name_of,
    extend_name,
    load_asset,
    read_asset_dir,
    remove_asset,
    split_asset,
    write_asset,
)
from ossie_studio.sources.curation import (
    enrich_asset_fn,
    read_enrichment,
    refresh_assets_fn,
    remove_asset_fn,
    retain_asset_fn,
)
from ossie_studio.sources.draft_changes import group_changes
from ossie_studio.sources.explorer_graph import ReferenceGraph

_HUMAN = ("x-businessName", "x-description", "x-concept", "x-tags")

_RAW = {
    "$id": "commande.schema.yaml",
    "title": "Commande",
    "type": "object",
    "x-kind": "class",
    "properties": {
        "montant": {"type": "number", "description": "doc du code"},
        "client": {"type": "string"},
    },
}


def _source(tmp_path: Path) -> Path:
    raw = tmp_path / "sources" / "api" / "raw"
    raw.mkdir(parents=True)
    (raw / "commande.schema.yaml").write_text(dump_schema(_RAW), encoding="utf-8")
    retain_asset_fn("api", "commande.schema.yaml", None)(tmp_path)
    return tmp_path / "sources" / "api" / "assets"


def _no_human_key(node: object) -> bool:
    if isinstance(node, dict):
        return not (set(node) & set(_HUMAN)) and all(_no_human_key(v) for v in node.values())
    if isinstance(node, list):
        return all(_no_human_key(v) for v in node)
    return True


def test_noms_de_fichiers() -> None:
    assert extend_name("commande.schema.yaml") == "commande.extend.yaml"
    assert asset_name_of("commande.extend.yaml") == "commande.schema.yaml"


def test_enrichir_ecrit_dans_extend_et_laisse_le_schema_standard(tmp_path: Path) -> None:
    assets = _source(tmp_path)
    assert not (assets / "commande.extend.yaml").exists()  # rien d'humain encore
    enrich_asset_fn(
        "api",
        "commande.schema.yaml",
        {"business_name": "Commande client", "tags": ["rgpd"]},
        {"montant": {"description": "Montant TTC"}},
    )(tmp_path)
    schema = load_schema((assets / "commande.schema.yaml").read_text(encoding="utf-8"))
    assert _no_human_key(schema)
    assert schema["properties"]["montant"]["description"] == "doc du code"  # standard intact
    extend = load_schema((assets / "commande.extend.yaml").read_text(encoding="utf-8"))
    assert extend == {
        "object": {"businessName": "Commande client", "tags": ["rgpd"]},
        "fields": {"montant": {"description": "Montant TTC"}},
    }
    view = read_enrichment(tmp_path, "api", "commande.schema.yaml")
    assert view is not None
    assert view.object.business_name == "Commande client"
    assert next(f for f in view.fields if f.name == "montant").description == "Montant TTC"


def test_tout_effacer_supprime_extend(tmp_path: Path) -> None:
    assets = _source(tmp_path)
    fn = enrich_asset_fn("api", "commande.schema.yaml", {"description": "x"}, {})
    fn(tmp_path)
    assert (assets / "commande.extend.yaml").is_file()
    enrich_asset_fn("api", "commande.schema.yaml", {"description": ""}, {})(tmp_path)
    assert not (assets / "commande.extend.yaml").exists()


def test_asset_ancien_lu_puis_deplace_a_la_premiere_ecriture(tmp_path: Path) -> None:
    assets = _source(tmp_path)
    legacy = load_schema((assets / "commande.schema.yaml").read_text(encoding="utf-8"))
    legacy["x-description"] = "écrit avant 7a284e55"
    legacy["properties"]["client"]["x-businessName"] = "Client"
    (assets / "commande.schema.yaml").write_text(dump_schema(legacy), encoding="utf-8")

    view = read_enrichment(tmp_path, "api", "commande.schema.yaml")
    assert view is not None and view.object.description == "écrit avant 7a284e55"

    enrich_asset_fn("api", "commande.schema.yaml", {"concept": "Vente"}, {})(tmp_path)
    schema = load_schema((assets / "commande.schema.yaml").read_text(encoding="utf-8"))
    assert _no_human_key(schema)
    extend = load_schema((assets / "commande.extend.yaml").read_text(encoding="utf-8"))
    assert extend["object"] == {"concept": "Vente", "description": "écrit avant 7a284e55"}
    assert extend["fields"] == {"client": {"businessName": "Client"}}


def test_extend_l_emporte_sur_une_cle_heritee(tmp_path: Path) -> None:
    assets = _source(tmp_path)
    legacy = load_schema((assets / "commande.schema.yaml").read_text(encoding="utf-8"))
    legacy["x-description"] = "ancienne"
    (assets / "commande.schema.yaml").write_text(dump_schema(legacy), encoding="utf-8")
    (assets / "commande.extend.yaml").write_text(
        dump_schema({"object": {"description": "nouvelle"}}), encoding="utf-8"
    )
    assert load_asset(assets / "commande.schema.yaml")["x-description"] == "nouvelle"


def test_rafraichir_ne_touche_pas_extend(tmp_path: Path) -> None:
    assets = _source(tmp_path)
    enrich_asset_fn("api", "commande.schema.yaml", {"description": "humain"}, {})(tmp_path)
    before = (assets / "commande.extend.yaml").read_bytes()
    raw = dict(_RAW, properties={**_RAW["properties"], "montant": {"type": "integer"}})
    raw_file = tmp_path / "sources" / "api" / "raw" / "commande.schema.yaml"
    raw_file.write_text(dump_schema(raw), encoding="utf-8")
    refresh_assets_fn("api", None)(tmp_path)
    schema = load_schema((assets / "commande.schema.yaml").read_text(encoding="utf-8"))
    assert schema["properties"]["montant"]["type"] == "integer"
    assert (assets / "commande.extend.yaml").read_bytes() == before


def test_retenir_de_nouveau_garde_le_travail_humain(tmp_path: Path) -> None:
    assets = _source(tmp_path)
    enrich_asset_fn("api", "commande.schema.yaml", {}, {"client": {"tags": ["pii"]}})(tmp_path)
    retain_asset_fn("api", "commande.schema.yaml", ["client"])(tmp_path)
    extend = load_schema((assets / "commande.extend.yaml").read_text(encoding="utf-8"))
    assert extend == {"fields": {"client": {"tags": ["pii"]}}}


def test_retirer_supprime_les_deux_fichiers(tmp_path: Path) -> None:
    assets = _source(tmp_path)
    enrich_asset_fn("api", "commande.schema.yaml", {"description": "x"}, {})(tmp_path)
    remove_asset_fn("api", "commande.schema.yaml")(tmp_path)
    assert list(assets.iterdir()) == []


def test_ecriture_deterministe(tmp_path: Path) -> None:
    file = tmp_path / "a.schema.yaml"
    asset = {"type": "object", "x-tags": ["b", "a"], "properties": {"p": {"x-description": "d"}}}
    write_asset(file, asset)
    first = (file.read_bytes(), file.with_name("a.extend.yaml").read_bytes())
    write_asset(file, load_asset(file))
    assert (file.read_bytes(), file.with_name("a.extend.yaml").read_bytes()) == first
    remove_asset(file)
    assert not file.exists() and not file.with_name("a.extend.yaml").exists()


def test_split_ignore_les_valeurs_vides() -> None:
    schema, extend = split_asset({"x-description": "", "x-tags": [], "type": "object"})
    assert schema == {"type": "object"} and extend == {}


def test_read_asset_dir_ignore_les_extend(tmp_path: Path) -> None:
    assets = _source(tmp_path)
    enrich_asset_fn("api", "commande.schema.yaml", {"description": "x"}, {})(tmp_path)
    assert list(read_asset_dir(assets)) == ["commande.schema.yaml"]


def test_modifications_rattachent_extend_a_son_asset() -> None:
    sources, outside = group_changes(
        [
            ("sources/api/assets/commande.extend.yaml", "added"),
            ("sources/api/assets/client.schema.yaml", "modified"),
            ("sources/api/assets/client.extend.yaml", "modified"),
        ],
        ReferenceGraph([], "origin"),
        {},
        {},
    )
    assert outside == []
    [api] = sources
    assert sorted((o.file, o.state) for o in api.assets) == [
        ("client.schema.yaml", "modified"),
        ("commande.schema.yaml", "modified"),
    ]
    assert api.other == []


def test_tags_du_extend_comptent_dans_les_impacts_de_gouvernance(tmp_path: Path) -> None:
    from ossie_studio.governance.repo import TAGS_DIR
    from ossie_studio.governance.rules import GovernanceAssetsRule

    _source(tmp_path)
    enrich_asset_fn("api", "commande.schema.yaml", {}, {"client": {"tags": ["pii"]}})(tmp_path)
    deps = GovernanceAssetsRule().dependents(tmp_path, {f"{TAGS_DIR}/pii.yaml"})
    assert deps == {"sources/api/assets/commande.schema.yaml": {f"{TAGS_DIR}/pii.yaml"}}
