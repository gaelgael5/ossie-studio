"""Enabler 0e088eaa — les types ArchiMate 3.2 en version de base, les règles de
relation et les points de vue (critères 1 à 6, et la fidélité à la table d'Archi)."""

from __future__ import annotations

from pathlib import Path

import pytest
from defusedxml import ElementTree as ET  # type: ignore[import-untyped]

from ossie_studio.cartographie.catalog import ICONS_DIR, catalog
from ossie_studio.cartographie.errors import CartographieError
from ossie_studio.cartographie.model import CartoObject, CartoType
from ossie_studio.cartographie.rules import (
    ALWAYS_ALLOWED,
    ARCHI_DIR,
    RELATIONSHIP,
    RulesError,
    allowed_relations,
    incoming,
    outgoing,
    parse_relationships,
    parse_viewpoints,
    relation_table,
    viewpoints,
)
from ossie_studio.cartographie.space import (
    allowed_between,
    read_cartographie,
    relation_types_between,
    save_object,
    save_type,
)

# Les éléments d'ArchiMate 3.2 par couche (spécification, chapitres 7 à 13).
LAYERS = {
    "strategy": 4,
    "business": 13,
    "application": 9,
    "technology": 13,
    "physical": 4,
    "motivation": 10,
    "implementation": 5,
    "other": 3,  # regroupement, lieu, jonction
}
RELATIONS = [
    "Composition",
    "Aggregation",
    "Assignment",
    "Realization",
    "Serving",
    "Access",
    "Influence",
    "Triggering",
    "Flow",
    "Specialization",
    "Association",
]


# --- Critère 1 : tous les éléments et toutes les relations -------------------------------


def test_catalogue_complet() -> None:
    cat = catalog()
    assert cat.archimate == "3.2"
    assert [t.name for t in cat.relations()] == RELATIONS
    assert {layer: len(cat.elements(layer)) for layer in cat.layers} == LAYERS
    assert len(cat.elements()) == 61
    # Exactement les concepts de la table d'Archi (`Relationship` y désigne une relation).
    assert set(cat.types) == relation_table().concepts() - {RELATIONSHIP} | set(RELATIONS)
    for t in cat.types.values():
        assert t.label and t.definition.endswith("."), t.name
        assert t.definition.count(". ") == 0, f"{t.name} : une seule phrase"
        assert (t.layer in cat.layers) is not t.is_relation, t.name


def test_icones_d_un_trait_sans_couleur() -> None:
    cat = catalog()
    assert {p.stem for p in ICONS_DIR.glob("*.svg")} == set(cat.types)
    for t in cat.types.values():
        svg = t.icon()
        root = ET.fromstring(svg)
        assert root.tag == "{http://www.w3.org/2000/svg}svg", t.name
        assert root.get("viewBox") == "0 0 24 24" and root.get("stroke") == "currentColor"
        assert "#" not in svg and "rgb" not in svg, f"{t.name} : pas de couleur de couche"
        assert len(root) > 0, t.name


# --- Critères 2 et 3 : règles entre types ArchiMate --------------------------------------


def test_acteur_vers_processus() -> None:
    permitted = allowed_relations("BusinessActor", "BusinessProcess")
    assert "Assignment" in permitted and "Composition" not in permitted
    # Archi : « fiotv » (flux, affectation, association, déclenchement, sert).
    assert permitted == ["Assignment", "Serving", "Triggering", "Flow", "Association"]


def test_objet_metier_vers_une_relation_de_flux() -> None:
    assert allowed_relations("BusinessObject", "Flow") == ["Association"]
    assert allowed_relations("Flow", "BusinessObject") == ["Association"]
    # Une relation vers une relation : rien (Archi), un type inconnu : rien.
    assert allowed_relations("Flow", "Association") == []
    assert allowed_relations("BusinessObject", "Inconnu") == []
    # Le regroupement et le plateau peuvent contenir des relations.
    assert allowed_relations("Plateau", "Triggering") == [
        "Composition",
        "Aggregation",
        "Association",
    ]


def test_relations_sortantes_et_entrantes() -> None:
    assert "Assignment" in outgoing("BusinessActor")["BusinessProcess"]
    assert incoming("Flow")["BusinessObject"] == ["Association"]
    assert "Junction" in outgoing("Junction") and "Flow" not in outgoing("Flow")


# --- Critère 4 : entre objets de l'espace (un type suit son x-archimate) -----------------


@pytest.fixture
def root(tmp_path: Path) -> Path:
    for slug, title, archimate in (
        ("processus-de-vente", "Processus de vente", "BusinessProcess"),
        ("document-metier", "Document métier", "BusinessObject"),
        ("acteur", "Acteur", "BusinessActor"),
        ("acces", "Accès", "Access"),
        ("association", "Association", "Association"),
        ("composition", "Composition", "Composition"),
        ("lecture-ecriture", "Lecture et écriture", "Access"),
    ):
        save_type(tmp_path, CartoType(slug, title, archimate), create=True)
    for key, ctype, name in (
        ("PRC-VENTE", "processus-de-vente", "Vente"),
        ("DOC-BON", "document-metier", "Bon de commande"),
        ("ACT-CLIENT", "acteur", "Client"),
    ):
        save_object(tmp_path, CartoObject(key, ctype, name), create=True)
    return tmp_path


def test_processus_de_vente_vers_document_metier(root: Path) -> None:
    assert allowed_between(root, "PRC-VENTE", "DOC-BON") == ["Access", "Association"]
    types = relation_types_between(root, "PRC-VENTE", "DOC-BON")
    assert [t.slug for t in types] == ["acces", "association", "lecture-ecriture"]
    with pytest.raises(CartographieError) as missing:
        allowed_between(root, "PRC-VENTE", "DOC-ABSENT")
    assert missing.value.code == "object_not_found" and missing.value.status == 404


def test_relation_vers_une_relation_de_l_espace(root: Path) -> None:
    save_object(
        root,
        CartoObject("REL-LIT", "acces", "Lit", source="PRC-VENTE", target="DOC-BON"),
        create=True,
    )
    assert allowed_between(root, "DOC-BON", "REL-LIT") == ["Association"]
    save_object(
        root,
        CartoObject("REL-NOTE", "association", "Note", source="DOC-BON", target="REL-LIT"),
        create=True,
    )
    assert read_cartographie(root).problems == []


def test_relation_non_permise_refusee_et_signalee(root: Path) -> None:
    refused = CartoObject(
        "REL-COMPOSE", "composition", "Compose", source="ACT-CLIENT", target="PRC-VENTE"
    )
    with pytest.raises(CartographieError) as error:
        save_object(root, refused, create=True)
    assert error.value.code == "object_invalid"
    [problem] = error.value.problems
    assert problem.code == "relation_not_allowed"
    assert problem.detail == "BusinessActor -> BusinessProcess : Composition"
    # Écrite à la main dans l'espace : signalée à la lecture.
    path = root / "cartographie/objets/composition/rel-compose.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "cle: REL-COMPOSE\ntype: composition\nsource: ACT-CLIENT\ncible: PRC-VENTE\nnom: C\n"
    )
    codes = {p.code for p in read_cartographie(root).problems}
    assert codes == {"relation_not_allowed"}


# --- Critère 5 : attributs propres (noms du contrat d'urban-archimate) -------------------


def test_attributs_propres() -> None:
    cat = catalog()
    access = cat.get("Access")
    assert access is not None
    [acces] = access.attributes
    assert acces.name == "acces"
    # Décision du 26 novembre : pas de valeur pour « non précisé », l'attribut est absent.
    assert acces.values == {"Read": "lecture", "Write": "écriture", "ReadWrite": "lecture-écriture"}
    assert acces.unset == "non précisé"
    assert acces.schema() == {
        "type": "string",
        "title": "Sens de l'accès",
        "x-archimate": True,
        "enum": ["Read", "Write", "ReadWrite"],
        "x-labels": {"Read": "lecture", "Write": "écriture", "ReadWrite": "lecture-écriture"},
        "x-unset": "non précisé",
    }
    interaction = cat.get("BusinessInteraction")
    assert interaction is not None and interaction.attributes == ()
    influence, association, junction = (
        cat.get(n) for n in ("Influence", "Association", "Junction")
    )
    assert influence and [(a.name, a.type, a.values) for a in influence.attributes] == [
        ("modificateur", "string", {})
    ]
    assert association and [(a.name, a.type, a.unset) for a in association.attributes] == [
        ("dirigee", "boolean", "non dirigée")
    ]
    assert junction and junction.attributes[0].values == {"And": "et", "Or": "ou"}
    with_attributes = {t.name for t in cat.types.values() if t.attributes}
    assert with_attributes == {"Access", "Influence", "Association", "Junction"}


# --- Critère 6 : points de vue -----------------------------------------------------------


def test_point_de_vue_cooperation_applicative() -> None:
    vp = viewpoints()["application-cooperation"]
    assert vp.label == "Coopération applicative"
    assert vp.allows("ApplicationComponent") and vp.allows("ApplicationInterface")
    assert not vp.allows("BusinessActor")
    assert vp.allows("Flow") and vp.allows("Location")


def test_points_de_vue_d_archi() -> None:
    cat = catalog()
    vps = viewpoints()
    archi_ids = [n.get("id") for n in ET.parse(ARCHI_DIR / "viewpoints.xml").iter("viewpoint")]
    assert list(vps) == [str(i).replace("_", "-") for i in archi_ids]
    assert set(cat.viewpoints) == set(vps), "un libellé français par point de vue"
    assert len(vps) == 25
    all_elements = tuple(t.name for t in cat.elements())
    assert vps["layered"].elements == all_elements
    for vp in vps.values():
        assert vp.label == cat.viewpoints[vp.id]
        assert all(vp.allows(n) for n in ALWAYS_ALLOWED), vp.id
        assert vp.relations == tuple(RELATIONS), vp.id
    assert vps["organization"].elements == (
        "BusinessActor",
        "BusinessRole",
        "BusinessCollaboration",
        "BusinessInterface",
        "Grouping",
        "Location",
        "Junction",
    )


# --- Fidélité à la table d'Archi ---------------------------------------------------------


def test_table_d_archi_relue_a_l_identique() -> None:
    table = relation_table()
    assert table.version == "3.2"
    assert len(table.concepts()) == 62 and len(table.pairs) == 62 * 62
    letters = {v: k for k, v in {
        "a": "Access", "c": "Composition", "f": "Flow", "g": "Aggregation",
        "i": "Assignment", "n": "Influence", "o": "Association", "r": "Realization",
        "s": "Specialization", "t": "Triggering", "v": "Serving",
    }.items()}  # fmt: skip
    for source in ET.parse(ARCHI_DIR / "relationships.xml").iter("source"):
        for target in source.iter("target"):
            pair = (source.get("concept"), target.get("concept"))
            written = "".join(sorted(letters[r] for r in table.pairs[pair]))
            assert written == "".join(sorted(target.get("relations") or "")), pair
    assert (ARCHI_DIR / "LICENSE.txt").read_text().startswith("Copyright (c) 2013-2026")


def test_fichier_d_archi_inattendu() -> None:
    with pytest.raises(RulesError):
        parse_relationships(
            '<relationships version="3.2"><source concept="A">'
            '<target concept="B" relations="x"/></source></relationships>'
        )
    with pytest.raises(RulesError):
        parse_viewpoints(
            '<viewpoints><viewpoint id="v"><name>V</name>'
            "<concept>$StructuralRelationships$</concept></viewpoint></viewpoints>",
            catalog(),
        )
