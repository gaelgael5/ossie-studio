"""Fichier de règles par défaut (epic « Cartographie — modèles et vues », enabler
00d7cd6d) : un premier modèle s'affiche sans rien écrire, une règle propre à un type
l'emporte sur la règle par défaut, et le composant ne reçoit jamais de modèle sans
bloc `defaut`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ossie_studio.cartographie.catalog import catalog
from ossie_studio.cartographie.diagram import (
    VALUE_PATH,
    Connection,
    Diagram,
    Graphic,
    Node,
    Rules,
    View,
    default_rule,
    dump_rules,
    graphic_path,
    hydrate,
    read_diagram,
    rules_path,
    save_diagram,
    save_rules,
    with_default,
)
from ossie_studio.cartographie.model import CartoObject, CartoType, object_path
from ossie_studio.cartographie.space import read_cartographie, save_object, save_type

SLUG = "boutique"
CRITICITE = {"type": "string", "enum": ["basse", "moyenne", "haute"]}
APP_PROPERTIES = {"criticite": CRITICITE, "responsable": {"type": "string"}}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Des types avec des propriétés ajoutées, des objets, et un modèle créé sans
    fichier de règles : il est créé vide, la règle par défaut suffit."""
    for slug, title, archimate, properties in (
        ("application", "Application", "ApplicationComponent", APP_PROPERTIES),
        ("processus", "Processus", "BusinessProcess", {"criticite": CRITICITE}),
        ("document-metier", "Document métier", "BusinessObject", {}),
        ("acces", "Accès", "Access", {}),
        ("association", "Association", "Association", {}),
    ):  # fmt: skip
        save_type(tmp_path, CartoType(slug, title, archimate, properties), create=True)
    objects = (
        CartoObject(
            "APP-BOUTIQUE",
            "application",
            "Boutique en ligne",
            "Le front de vente.\n",
            {"criticite": "haute", "responsable": "Équipe vente"},
        ),
        CartoObject("PRC-VENTE", "processus", "Vente", properties={"criticite": "moyenne"}),
        CartoObject("OBJ-COMMANDE", "document-metier", "Commande"),
        CartoObject(
            "REL-ECRIT",
            "acces",
            "Écrit la commande",
            properties={"acces": "Write"},
            source="APP-BOUTIQUE",
            target="OBJ-COMMANDE",
        ),
        CartoObject(
            "REL-SERT",
            "association",
            "Sert la vente",
            properties={"dirigee": True},
            source="APP-BOUTIQUE",
            target="PRC-VENTE",
        ),
    )
    for obj in objects:
        save_object(tmp_path, obj, create=True)
    keys = [o.key for o in objects]
    diagram = Diagram(
        id="MOD-BOUTIQUE",
        rules=rules_path("standard"),
        graphic=graphic_path(SLUG),
        name="Boutique",
        documents=[object_path(o.type, o.key) for o in objects],
        elements=keys[:3],
        relationships=keys[3:],
        views=[
            View(
                "VUE-1",
                nodes=[Node(f"N{i}", element=k) for i, k in enumerate(keys[:3])],
                connections=[
                    Connection("C1", "REL-ECRIT", "N0", "N2"),
                    Connection("C2", "REL-SERT", "N0", "N1"),
                ],
            )
        ],
    )
    save_diagram(tmp_path, SLUG, diagram, Graphic(), create=True)
    return tmp_path


def _resolve(obj: dict[str, Any], value: Any) -> Any:
    if not (isinstance(value, str) and VALUE_PATH.fullmatch(value)):
        return value  # un littéral
    current: Any = obj
    for segment in value[2:].split("."):
        current = current.get(segment) if isinstance(current, dict) else None
    return current


def _text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value).lower() if isinstance(value, bool) else str(value)


def _shown(hydrated: dict[str, Any], key: str) -> dict[str, Any]:
    """Ce que le composant affiche d'un objet : la même recherche de bloc (type, puis
    type ArchiMate, puis `defaut`) et la même résolution que `translateObject`
    (`src/rules/engine.ts` d'urban-archimate)."""
    rules = hydrated["regles"]["data"]
    obj = next(d["data"] for d in hydrated["documents"] if d["data"]["cle"] == key)
    schemas = {
        t["chemin"].rsplit("/", 1)[1].removesuffix(".schema.yaml"): t["data"]
        for t in hydrated["types"]
    }
    relation = key in hydrated.get("relationships", [])
    by_type = {b["type"]: b for b in rules.get("relationships" if relation else "elements", [])}
    archimate = schemas[obj["type"]]["x-archimate"]
    block = by_type.get(obj["type"]) or by_type.get(archimate) or rules["defaut"]
    shown: dict[str, Any] = {
        k: _text(_resolve(obj, block.get(k))) for k in ("name", "documentation")
    }
    resolved = {
        p["propertyDefinitionRef"]: _resolve(obj, p["value"]) for p in block.get("properties", [])
    }
    shown["properties"] = {k: _text(v) for k, v in resolved.items() if _text(v) is not None}
    if relation:
        meta = ("type", "name", "documentation", "properties")
        attributes = {k: _text(_resolve(obj, v)) for k, v in block.items() if k not in meta}
        shown["attributes"] = {k: v for k, v in attributes.items() if v is not None}
    return shown


def _hydrated(root: Path) -> dict[str, Any]:
    return hydrate(read_diagram(root, SLUG), read_cartographie(root))


def test_la_creation_d_un_modele_cree_le_fichier_de_regles_vide(root: Path) -> None:
    assert (root / rules_path("standard")).read_text(encoding="utf-8") == "lang: fr\n"
    assert read_diagram(root, SLUG).problems == []


# --- Critère 1 : un fichier de règles vide affiche tout -------------------------------------


def test_fichier_de_regles_vide_tout_s_affiche(root: Path) -> None:
    hydrated = _hydrated(root)
    assert _shown(hydrated, "APP-BOUTIQUE") == {
        "name": "Boutique en ligne",
        "documentation": "Le front de vente.\n",
        "properties": {"criticite": "haute", "responsable": "Équipe vente"},
    }
    assert _shown(hydrated, "PRC-VENTE")["properties"] == {"criticite": "moyenne"}
    assert _shown(hydrated, "OBJ-COMMANDE") == {
        "name": "Commande",
        "documentation": None,
        "properties": {},
    }
    assert _shown(hydrated, "REL-ECRIT")["attributes"] == {"accessType": "Write"}
    assert _shown(hydrated, "REL-SERT")["attributes"] == {"isDirected": "true"}


def test_regle_par_defaut_tiree_du_catalogue_et_des_types() -> None:
    access = catalog().get("Access")
    assert access and access.attributes[0].exchange == "accessType"
    space_types = {"b": CartoType("b", "B", "BusinessRole", {"zeta": {}, "alpha": {}}),
                   "a": CartoType("a", "A", "BusinessActor", {"alpha": {}})}  # fmt: skip
    space = read_cartographie(Path("/nonexistent"))
    space.types = space_types
    assert default_rule(space) == {
        "name": "$.nom",
        "documentation": "$.documentation",
        "accessType": "$.proprietes.acces",
        "modifier": "$.proprietes.modificateur",
        "isDirected": "$.proprietes.dirigee",
        "properties": [
            {"propertyDefinitionRef": "alpha", "value": "$.proprietes.alpha"},
            {"propertyDefinitionRef": "zeta", "value": "$.proprietes.zeta"},
        ],
    }


# --- Critère 2 : une règle propre l'emporte ------------------------------------------------


def test_regle_propre_sans_criticite(root: Path) -> None:
    own = {"type": "application", "name": "$.nom", "documentation": "$.documentation"}
    save_rules(root, "standard", Rules(lang="fr", elements=[own]))
    hydrated = _hydrated(root)
    app = _shown(hydrated, "APP-BOUTIQUE")
    assert app["name"] == "Boutique en ligne" and app["properties"] == {}
    assert _shown(hydrated, "PRC-VENTE")["properties"] == {"criticite": "moyenne"}


# --- Critère 3 : jamais sans bloc `defaut` -------------------------------------------------


def test_le_composant_recoit_toujours_un_bloc_defaut(root: Path) -> None:
    space = read_cartographie(root)
    assert _hydrated(root)["regles"]["data"]["defaut"] == default_rule(space)
    assert "defaut" not in dump_rules(read_diagram(root, SLUG).rules or Rules())  # rien d'écrit
    # Un `defaut` écrit dans le fichier de règles est celui que reçoit le composant.
    save_rules(root, "standard", Rules(lang="fr", default={"name": "$.cle"}))
    assert _hydrated(root)["regles"]["data"]["defaut"] == {"name": "$.cle"}
    rules = Rules()
    assert with_default(rules, space).default == default_rule(space) and rules.default is None
