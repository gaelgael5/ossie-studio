"""Format du modèle, du fichier de règles et du fichier graphique (epic
« Cartographie — modèles et vues », enabler e338fe14) : critères 1 à 4, et le
contrat partagé avec `urban-archimate` (le modèle hydraté passe son JSON Schema)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from httpx import AsyncClient
from jsonschema import Draft202012Validator

from ossie_studio.cartographie.catalog import catalog
from ossie_studio.cartographie.diagram import (
    Graphic,
    Rules,
    contract_schema,
    diagram_path,
    dump_diagram,
    dump_graphic,
    dump_rules,
    graphic_path,
    hydrate,
    list_diagrams,
    parse_diagram,
    parse_graphic,
    parse_rules,
    read_diagram,
    rule_block,
    rules_path,
    save_diagram,
    save_rules,
)
from ossie_studio.cartographie.errors import CartographieError
from ossie_studio.cartographie.model import CartoObject, CartoType
from ossie_studio.cartographie.space import read_cartographie, save_object, save_type

# Les trois exemples du ticket, tels qu'écrits à la main (commentaires, style en ligne).
MODEL = """\
id: MOD-BOUTIQUE-APPLICATIF
nom: Boutique — vue applicative
description: Ce que la boutique expose et ce qu'elle manipule.
regles: cartographie/regles/standard.rules.yaml
graphique: cartographie/modeles/boutique-applicatif.graphique.yaml
documents:
  - cartographie/objets/application/app-boutique.yaml
  - cartographie/objets/interface-applicative/api-catalogue.yaml
  - cartographie/objets/document-metier/obj-commande.yaml
  - cartographie/objets/acces/rel-boutique-ecrit-commande.yaml
elements: [APP-BOUTIQUE, API-CATALOGUE, OBJ-COMMANDE]
relationships: [REL-BOUTIQUE-ECRIT-COMMANDE]
vues:
  - id: VUE-COOPERATION
    nom: Coopération applicative
    point_de_vue: application-cooperation      # facultatif, point de vue ArchiMate
    noeuds:
      - id: N1
        element: APP-BOUTIQUE
        enfants:                               # imbrication
          - { id: N2, element: API-CATALOGUE }
      - { id: N3, element: OBJ-COMMANDE }
      - { id: N4, note: "Source : atelier du 2 décembre" }
      - { id: N5, cadre: "Front", enfants: [] } # cadre de regroupement, propre à la vue
    connexions:
      - { id: C1, relationship: REL-BOUTIQUE-ECRIT-COMMANDE, de: N1, vers: N3 }
"""
RULES = """\
lang: fr
elements:
  - type: application              # slug d'un type de l'espace ; à défaut, un type ArchiMate
    name: $.nom
    documentation: $.documentation
    properties:
      - propertyDefinitionRef: responsable
        value: $.proprietes.responsable
relationships:
  - type: acces
    name: $.nom
    accessType: $.proprietes.acces   # déjà une valeur ArchiMate (Write…), rien à convertir
"""
GRAPHIC = """\
vues:
  VUE-COOPERATION:
    noeuds:
      N1: { x: 40, y: 60, l: 320, h: 180 }
      N2: { relatif: N1, dx: 20, dy: 50, l: 140, h: 60 }
      N3: { relatif: N1, dx: 420, dy: 40, l: 160, h: 70 }
    connexions:
      C1:
        ancre_source: droite
        ancre_cible: gauche
        points: [{ x: 400, y: 95 }]
        libelle: { position: 0.5, dx: 0, dy: -12 }
"""
# Leur écriture par Ossie : déterministe, une géométrie par ligne.
MODEL_WRITTEN = """\
id: MOD-BOUTIQUE-APPLICATIF
nom: Boutique — vue applicative
description: Ce que la boutique expose et ce qu'elle manipule.
regles: cartographie/regles/standard.rules.yaml
graphique: cartographie/modeles/boutique-applicatif.graphique.yaml
documents:
- cartographie/objets/application/app-boutique.yaml
- cartographie/objets/interface-applicative/api-catalogue.yaml
- cartographie/objets/document-metier/obj-commande.yaml
- cartographie/objets/acces/rel-boutique-ecrit-commande.yaml
elements:
- APP-BOUTIQUE
- API-CATALOGUE
- OBJ-COMMANDE
relationships:
- REL-BOUTIQUE-ECRIT-COMMANDE
vues:
- id: VUE-COOPERATION
  nom: Coopération applicative
  point_de_vue: application-cooperation
  noeuds:
  - id: N1
    element: APP-BOUTIQUE
    enfants:
    - id: N2
      element: API-CATALOGUE
  - id: N3
    element: OBJ-COMMANDE
  - id: N4
    note: 'Source : atelier du 2 décembre'
  - id: N5
    cadre: Front
  connexions:
  - id: C1
    relationship: REL-BOUTIQUE-ECRIT-COMMANDE
    de: N1
    vers: N3
"""
GRAPHIC_WRITTEN = """\
vues:
  VUE-COOPERATION:
    noeuds:
      N1: {x: 40, y: 60, l: 320, h: 180}
      N2: {relatif: N1, dx: 20, dy: 50, l: 140, h: 60}
      N3: {relatif: N1, dx: 420, dy: 40, l: 160, h: 70}
    connexions:
      C1:
        ancre_source: droite
        ancre_cible: gauche
        points:
        - {x: 400, y: 95}
        libelle: {position: 0.5, dx: 0, dy: -12}
"""
SLUG = "boutique-applicatif"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """L'espace de l'exemple : quatre types, trois objets et une relation, le modèle,
    ses règles et son graphique."""
    for slug, title, archimate, properties in (
        ("application", "Application", "ApplicationComponent", {"responsable": {"type": "string"}}),
        ("interface-applicative", "Interface applicative", "ApplicationInterface", {}),
        ("document-metier", "Document métier", "BusinessObject", {}),
        ("acces", "Accès", "Access", {}),
    ):
        save_type(tmp_path, CartoType(slug, title, archimate, properties), create=True)
    for obj in (
        CartoObject(
            "APP-BOUTIQUE",
            "application",
            "Boutique en ligne",
            "Le front de vente.\n",
            {"responsable": "Équipe vente"},
        ),
        CartoObject("API-CATALOGUE", "interface-applicative", "API Catalogue"),
        CartoObject("OBJ-COMMANDE", "document-metier", "Commande"),
        CartoObject(
            "REL-BOUTIQUE-ECRIT-COMMANDE",
            "acces",
            "Écrit la commande",
            properties={"acces": "Write"},
            source="APP-BOUTIQUE",
            target="OBJ-COMMANDE",
        ),
    ):
        save_object(tmp_path, obj, create=True)
    for path, text in (
        (diagram_path(SLUG), MODEL),
        (rules_path("standard"), RULES),
        (graphic_path(SLUG), GRAPHIC),
    ):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
    return tmp_path


def _found(root: Path) -> set[tuple[str, str, str]]:
    """(code, identifiant en cause, place dans le fichier) de chaque problème."""
    return {(p.code, p.detail, p.field) for p in read_diagram(root, SLUG).problems}


# --- Critère 1 : les trois exemples se lisent et se réécrivent octet pour octet ---------


def test_les_trois_exemples_se_lisent_sans_erreur(root: Path) -> None:
    read = read_diagram(root, SLUG)
    assert read.problems == []
    assert read.diagram is not None and read.rules is not None
    assert read.diagram.elements == ["APP-BOUTIQUE", "API-CATALOGUE", "OBJ-COMMANDE"]
    [view] = read.diagram.views
    assert [n.id for _w, n in view.walk()] == ["N1", "N2", "N3", "N4", "N5"]
    assert read.rules.relationships[0]["accessType"] == "$.proprietes.acces"
    assert read.graphic.views["VUE-COOPERATION"].nodes["N2"]["relatif"] == "N1"
    assert list_diagrams(root) == [SLUG]


def test_les_trois_exemples_se_reecrivent_octet_pour_octet() -> None:
    model = dump_diagram(parse_diagram(MODEL))
    graphic = dump_graphic(parse_graphic(GRAPHIC))
    rules = dump_rules(parse_rules(RULES))
    assert model == MODEL_WRITTEN and graphic == GRAPHIC_WRITTEN
    assert rules.startswith("lang: fr\nelements:\n- type: application\n  name: $.nom\n")
    assert dump_diagram(parse_diagram(model)) == model
    assert dump_graphic(parse_graphic(graphic)) == graphic
    assert dump_rules(parse_rules(rules)) == rules


def test_fichiers_vides_illisibles_ou_hors_forme(root: Path) -> None:
    # Un fichier de règles ou un graphique vide : rien de propre, pas d'erreur.
    assert parse_rules("") == Rules() and parse_graphic("") == Graphic()
    assert dump_graphic(Graphic()) == "vues: {}\n"
    # Un nombre entier écrit en flottant (le dessin le rend ainsi) reste entier.
    moved = parse_graphic("vues: {V: {noeuds: {N1: {x: 40.0, y: 60.5}}}}")
    assert dump_graphic(moved) == "vues:\n  V:\n    noeuds:\n      N1: {x: 40, y: 60.5}\n"
    with pytest.raises(CartographieError) as shape:
        parse_graphic("vues: {V: {noeuds: {N1: {relatif: N0, x: 3}}}}")
    assert shape.value.code == "graphic_invalid"
    with pytest.raises(CartographieError) as invalid:
        parse_diagram("id: mod-minuscule\nregles: r\ngraphique: g\ncoordonnees: {x: 1}\n")
    assert invalid.value.code == "model_invalid"
    assert {p.field for p in invalid.value.problems} == {"", "id"}
    (root / graphic_path(SLUG)).write_text("vues: [\n", encoding="utf-8")
    assert ("graphic_unreadable", graphic_path(SLUG)) in {
        (p.code, p.path) for p in read_diagram(root, SLUG).problems
    }
    with pytest.raises(CartographieError) as missing:
        read_diagram(root, "absent")
    assert missing.value.code == "model_not_found" and missing.value.status == 404


# --- Critère 2 : chacun des cinq contrôles, avec l'identifiant en cause ------------------


def _rewrite(root: Path, old: str, new: str) -> None:
    path = root / diagram_path(SLUG)
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_controle_1_cle_retenue_sans_document(root: Path) -> None:
    _rewrite(root, "elements: [APP-BOUTIQUE,", "elements: [APP-ABSENT, APP-BOUTIQUE,")
    assert _found(root) == {("model_key_without_document", "APP-ABSENT", "elements[0]")}


def test_controle_2_extremite_de_relation_non_retenue(root: Path) -> None:
    _rewrite(root, ", OBJ-COMMANDE]", "]")
    _rewrite(root, "      - { id: N3, element: OBJ-COMMANDE }\n", "")
    _rewrite(root, "  - cartographie/objets/document-metier/obj-commande.yaml\n", "")
    _rewrite(root, "de: N1, vers: N3", "de: N1, vers: N1")
    (root / graphic_path(SLUG)).write_text("vues: {}\n", encoding="utf-8")
    assert _found(root) == {
        ("model_relation_end_not_retained", "REL-BOUTIQUE-ECRIT-COMMANDE", "relationships[0]")
    }


def test_controle_3_noeud_ou_connexion_hors_du_modele(root: Path) -> None:
    _rewrite(root, "{ id: N3, element: OBJ-COMMANDE }", "{ id: N3, element: OBJ-AUTRE }")
    _rewrite(root, "de: N1, vers: N3 }", "de: N1, vers: N9 }")
    _rewrite(
        root,
        "    connexions:\n",
        "    connexions:\n      - { id: C2, relationship: REL-AUTRE, de: N1, vers: N2 }\n",
    )
    assert _found(root) == {
        ("model_node_not_retained", "N3", "vues[0].noeuds[1].element"),
        ("model_connection_node_unknown", "C1", "vues[0].connexions[1].vers"),
        ("model_connection_not_retained", "C2", "vues[0].connexions[0].relationship"),
    }


def test_controle_4_document_sans_objet_retenu(root: Path) -> None:
    _rewrite(root, "elements: [APP-BOUTIQUE, API-CATALOGUE,", "elements: [APP-BOUTIQUE,")
    _rewrite(root, "          - { id: N2, element: API-CATALOGUE }\n", "")
    _rewrite(root, "        enfants:                               # imbrication\n", "")
    graphic = (root / graphic_path(SLUG)).read_text(encoding="utf-8")
    (root / graphic_path(SLUG)).write_text(
        "\n".join(line for line in graphic.splitlines() if "N2:" not in line) + "\n",
        encoding="utf-8",
    )
    path = "cartographie/objets/interface-applicative/api-catalogue.yaml"
    assert _found(root) == {("model_document_unused", path, "documents[1]")}


def test_controle_5_entree_du_graphique_ignoree_et_signalee(root: Path) -> None:
    graphic = root / graphic_path(SLUG)
    graphic.write_text(
        graphic.read_text(encoding="utf-8").replace(
            "    connexions:\n", "      N9: { x: 1, y: 2 }\n    connexions:\n"
        )
        + "  VUE-ABSENTE: {}\n",
        encoding="utf-8",
    )
    read = read_diagram(root, SLUG)
    assert {(p.code, p.detail, p.field) for p in read.problems} == {
        ("graphic_entry_unknown", "N9", "vues.VUE-COOPERATION.noeuds.N9"),
        ("graphic_entry_unknown", "VUE-ABSENTE", "vues.VUE-ABSENTE"),
    }
    assert read.errors == []  # un avertissement ne refuse rien
    assert read.diagram is not None
    warnings = save_diagram(root, SLUG, read.diagram, read.graphic, create=False)
    assert {p.detail for p in warnings} == {"N9", "VUE-ABSENTE"}
    # Entrées inconnues retirées ; le reste garde la forme du fichier (écriture en
    # aller-retour, enabler 14bb8eb8) : la liste de points reste sur une ligne.
    assert graphic.read_text(encoding="utf-8") == GRAPHIC_WRITTEN.replace(
        "        points:\n        - {x: 400, y: 95}\n", "        points: [{x: 400, y: 95}]\n"
    )


def test_autres_incoherences_signalees(root: Path) -> None:
    _rewrite(
        root, "elements: [APP-BOUTIQUE,", "elements: [REL-BOUTIQUE-ECRIT-COMMANDE, APP-BOUTIQUE,"
    )
    _rewrite(root, "point_de_vue: application-cooperation", "point_de_vue: inconnu")
    _rewrite(root, "{ id: N4, note:", "{ id: N3, note:")
    _rewrite(root, "regles: cartographie/regles/standard.rules.yaml", "regles: ailleurs.yaml")
    _rewrite(
        root,
        "  - cartographie/objets/acces/",
        "  - cartographie/objets/absent.yaml\n  - cartographie/objets/acces/",
    )
    assert {code for code, _d, _f in _found(root)} == {
        "model_element_is_relation",
        "model_viewpoint_unknown",
        "model_node_duplicate",
        "model_rules_path_invalid",
        "rules_missing",
        "model_document_missing",
    }


# --- Critère 3 : la règle d'un objet ------------------------------------------------------


def test_regle_du_type_puis_du_type_archimate_puis_par_defaut(root: Path) -> None:
    space = read_cartographie(root)
    rules = parse_rules(RULES + "  - type: Access\n    name: $.documentation\n")
    rules.elements.append({"type": "BusinessObject", "name": "$.cle"})

    def block(key: str) -> dict[str, object] | None:
        return rule_block(rules, space.objects[key][1], space)

    assert block("APP-BOUTIQUE") == rules.elements[0]  # sa règle propre : `application`
    assert block("OBJ-COMMANDE") == {"type": "BusinessObject", "name": "$.cle"}
    assert block("REL-BOUTIQUE-ECRIT-COMMANDE") == rules.relationships[0]  # `acces` d'abord
    assert block("API-CATALOGUE") is None  # ni règle propre ni règle ArchiMate
    rules.default = {"name": "$.nom"}
    assert block("API-CATALOGUE") == {"name": "$.nom"}


def test_fichier_de_regles_controle(root: Path) -> None:
    rules = Rules(
        lang="fr",
        elements=[
            {"type": "application", "name": "$.nom"},
            {"type": "application", "name": "$nom"},
            {"type": "acces"},
            {"type": "inconnu"},
        ],
    )
    with pytest.raises(CartographieError) as refused:
        save_rules(root, "standard", rules)
    assert refused.value.code == "rules_invalid"
    assert {(p.code, p.detail, p.field) for p in refused.value.problems} == {
        ("rules_type_duplicate", "application", "elements[1]"),
        ("rules_path_invalid", "$nom", "elements[1].name"),
        ("rules_type_wrong_section", "acces", "elements[2]"),
        ("rules_type_unknown", "inconnu", "elements[3]"),
    }
    assert (root / rules_path("standard")).read_text(encoding="utf-8") == RULES  # intact
    save_rules(root, "vide", Rules())
    assert parse_rules((root / rules_path("vide")).read_text(encoding="utf-8")) == Rules()


# --- Critère 4 : déplacer un nœud ne modifie que le fichier graphique --------------------


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_deplacer_un_noeud_ne_touche_que_le_graphique(root: Path) -> None:
    read = read_diagram(root, SLUG)
    assert read.diagram is not None
    # Point de départ : les fichiers sous la forme écrite par Ossie.
    (root / diagram_path(SLUG)).write_text(dump_diagram(read.diagram), encoding="utf-8")
    (root / graphic_path(SLUG)).write_text(dump_graphic(read.graphic), encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "avant")
    moved = parse_graphic(dump_graphic(read.graphic))
    moved.views["VUE-COOPERATION"].nodes["N3"]["dx"] = 460.0
    save_diagram(root, SLUG, read.diagram, moved, create=False)
    assert _git(root, "status", "--porcelain").split() == ["M", graphic_path(SLUG)]
    diff = _git(root, "diff", "--unified=0").splitlines()
    assert [line for line in diff if line[:1] in "+-" and line[:3] not in ("+++", "---")] == [
        "-      N3: {relatif: N1, dx: 420, dy: 40, l: 160, h: 70}",
        "+      N3: {relatif: N1, dx: 460, dy: 40, l: 160, h: 70}",
    ]


# --- Écriture : refus nommés ---------------------------------------------------------------


def test_un_fichier_ecrit_a_la_main_garde_sa_forme_tant_qu_il_ne_change_pas(root: Path) -> None:
    read = read_diagram(root, SLUG)
    assert read.diagram is not None
    save_diagram(root, SLUG, read.diagram, read.graphic, create=False)
    assert (root / diagram_path(SLUG)).read_text(encoding="utf-8") == MODEL  # commentaires gardés
    assert (root / graphic_path(SLUG)).read_text(encoding="utf-8") == GRAPHIC
    read.diagram.name = "Boutique"
    save_diagram(root, SLUG, read.diagram, read.graphic, create=False)
    assert (
        (root / diagram_path(SLUG))
        .read_text(encoding="utf-8")
        .startswith("id: MOD-BOUTIQUE-APPLICATIF\nnom: Boutique\n")
    )
    assert (root / graphic_path(SLUG)).read_text(encoding="utf-8") == GRAPHIC


def test_enregistrer_un_modele_refuse_ce_qui_ne_passe_pas(root: Path) -> None:
    read = read_diagram(root, SLUG)
    diagram = read.diagram
    assert diagram is not None
    with pytest.raises(CartographieError) as exists:
        save_diagram(root, SLUG, diagram, read.graphic, create=True)
    assert exists.value.code == "model_exists" and exists.value.status == 409
    with pytest.raises(CartographieError) as taken:
        diagram.graphic = graphic_path("copie")
        save_diagram(root, "copie", diagram, Graphic(), create=True)
    assert taken.value.code == "model_id_taken"
    diagram.id, diagram.elements = "MOD-COPIE", ["APP-ABSENT"]
    with pytest.raises(CartographieError) as invalid:
        save_diagram(root, "copie", diagram, Graphic(), create=True)
    assert invalid.value.code == "model_invalid"
    assert "model_key_without_document" in {p.code for p in invalid.value.problems}
    assert not (root / diagram_path("copie")).exists()  # rien n'est écrit


# --- Le contrat partagé : le modèle hydraté passe le JSON Schema du composant ------------


def test_modele_hydrate_valide_contre_le_schema_du_composant(root: Path) -> None:
    space = read_cartographie(root)
    hydrated = hydrate(read_diagram(root, SLUG), space)
    validator = Draft202012Validator(contract_schema())
    assert [e.message for e in validator.iter_errors(hydrated)] == []
    assert [t["chemin"] for t in hydrated["types"]] == [
        "cartographie/types/application.schema.yaml",
        "cartographie/types/interface-applicative.schema.yaml",
        "cartographie/types/document-metier.schema.yaml",
        "cartographie/types/acces.schema.yaml",
    ]
    assert hydrated["types"][0]["data"]["x-archimate"] == "ApplicationComponent"
    relation = hydrated["documents"][3]["data"]
    assert relation == {
        "cle": "REL-BOUTIQUE-ECRIT-COMMANDE",
        "type": "acces",
        "source": "APP-BOUTIQUE",
        "cible": "OBJ-COMMANDE",
        "nom": "Écrit la commande",
        "proprietes": {"acces": "Write"},
    }
    assert hydrated["regles"]["chemin"] == rules_path("standard")
    assert hydrated["graphique"]["data"]["vues"]["VUE-COOPERATION"]["noeuds"]["N1"]["x"] == 40
    assert catalog().get("Access") is not None


def test_un_modele_incoherent_ne_s_hydrate_pas(root: Path) -> None:
    _rewrite(root, "elements: [APP-BOUTIQUE,", "elements: [APP-ABSENT, APP-BOUTIQUE,")
    with pytest.raises(CartographieError) as refused:
        hydrate(read_diagram(root, SLUG), read_cartographie(root))
    assert refused.value.code == "model_invalid" and refused.value.status == 409


async def test_composant_servi_par_l_application(client: AsyncClient) -> None:
    script = await client.get("/api/archimate/urban-archimate.js")
    assert script.status_code == 200 and "javascript" in script.headers["content-type"]
    schema = await client.get("/api/archimate/hydrated-model.schema.json")
    assert schema.json()["$defs"]["noeud"] == contract_schema()["$defs"]["noeud"]
