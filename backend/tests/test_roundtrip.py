"""Écriture YAML et JSON en aller-retour (enabler 14bb8eb8) : une écriture ne
change que les lignes dont le contenu change. Un test par critère d'acceptation."""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

import yaml

from ossie_studio.drafts.merge import merge_object, serialize
from ossie_studio.roundtrip import render, update_json, update_yaml, write_document


def _fresh(value: Any) -> str:
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True)


def _changed(before: str, after: str) -> list[str]:
    return [
        line
        for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="")
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]


COMMENTED = """# Application de facturation
cle: APP-07   # clé métier
type: application
nom: "App 7"
documentation: >-
  {long_text}
proprietes:
  # niveau métier
  criticite: haute
  zones:
  - eu
  - us
""".format(
    long_text="Un texte assez long pour qu'un rendu à 80 colonnes le replie sur "
    "plusieurs lignes, ce qui ne doit pas arriver."
)


def test_1_seule_la_ligne_modifiee_change_commentaires_gardes() -> None:
    new = yaml.safe_load(COMMENTED)
    new["proprietes"]["criticite"] = "basse"
    out = update_yaml(COMMENTED, new, _fresh)
    assert _changed(COMMENTED, out) == ["-  criticite: haute", "+  criticite: basse"]
    assert (
        "# Application de facturation" in out and "# clé métier" in out and "# niveau métier" in out
    )


def test_2_ordre_guillemets_indentation_et_textes_longs_conserves() -> None:
    new = yaml.safe_load(COMMENTED)
    new["nom"] = "App sept"
    out = update_yaml(COMMENTED, new, _fresh)
    assert _changed(COMMENTED, out) == ['-nom: "App 7"', '+nom: "App sept"']
    four = "racine:\n    enfant:\n        feuille: 1\n    autre: 2\n"
    changed = update_yaml(four, {"racine": {"enfant": {"feuille": 3}, "autre": 2}}, _fresh)
    assert changed == "racine:\n    enfant:\n        feuille: 3\n    autre: 2\n"


def test_3_json_indentation_et_ordre_des_cles() -> None:
    old = '{\n    "z": 1,\n    "a": "é"\n}\n'
    out = update_json(old, {"a": "é", "z": 2, "b": True}, json.dumps)
    assert out == '{\n    "z": 2,\n    "a": "é",\n    "b": true\n}\n'


def test_4_ajout_dans_une_liste_d_objets_sans_reordonner() -> None:
    old = "items:\n- name: a  # premier\n  v: 1\n- name: c\n  v: 3\n"
    new = {"items": [{"name": "a", "v": 1}, {"name": "b", "v": 2}, {"name": "c", "v": 3}]}
    out = update_yaml(old, new, _fresh)
    # Les éléments existants ne bougent pas, b s'insère à sa place ; le
    # commentaire du premier reste.
    assert [i["name"] for i in yaml.safe_load(out)["items"]] == ["a", "b", "c"]
    assert "# premier" in out


def test_4b_reordonnancement_voulu_respecte_ou_ordre_du_fichier_garde() -> None:
    old = "items:\n- name: c\n  v: 3\n- name: a\n  v: 1\n"
    new = {"items": [{"name": "a", "v": 1}, {"name": "b", "v": 2}, {"name": "c", "v": 3}]}
    # Par défaut, l'ordre voulu par l'application fait foi (niveaux réordonnés…).
    assert [i["name"] for i in yaml.safe_load(update_yaml(old, new, _fresh))["items"]] == [
        "a",
        "b",
        "c",
    ]
    # keep_order (valeurs.json) : ordre du fichier, b après son prédécesseur a.
    kept = update_yaml(old, new, _fresh, keep_order=True)
    assert [i["name"] for i in yaml.safe_load(kept)["items"]] == ["c", "a", "b"]


def test_partie_nouvelle_au_style_de_l_application() -> None:
    def flow(value: Any) -> str:
        return yaml.safe_dump(value, sort_keys=False, default_flow_style=None)

    old = "# graphique\nnoeuds:\n  A: {x: 1, y: 2}\n"
    out = update_yaml(old, {"noeuds": {"A": {"x": 1, "y": 2}, "B": {"x": 3, "y": 4}}}, flow)
    assert out == "# graphique\nnoeuds:\n  A: {x: 1, y: 2}\n  B: {x: 3, y: 4}\n"


def test_5_ecriture_sans_changement_aucun_diff(tmp_path: Path) -> None:
    target = tmp_path / "objet.yaml"
    target.write_text(COMMENTED, encoding="utf-8")
    before = target.stat().st_mtime_ns
    write_document(target, yaml.safe_load(COMMENTED), _fresh)
    assert target.read_text(encoding="utf-8") == COMMENTED
    assert target.stat().st_mtime_ns == before  # pas même réécrit


def test_6_cas_limites() -> None:
    # Sans saut de ligne final, CRLF.
    assert update_yaml("a: 1\r\nb: 2", {"a": 1, "b": 3}, _fresh) == "a: 1\r\nb: 3"
    # Accents.
    assert update_yaml("nom: 'Événement'\n", {"nom": "Évé"}, _fresh) == "nom: 'Évé'\n"
    # Ancres et alias.
    old = "base: &b\n    x: 1\nautre: *b\nn: 1\n"
    out = update_yaml(old, {"base": {"x": 1}, "autre": {"x": 1}, "n": 2}, _fresh)
    assert out == "base: &b\n    x: 1\nautre: *b\nn: 2\n"
    # Plusieurs documents : rendu neuf, sans erreur.
    assert update_yaml("a: 1\n---\nb: 2\n", {"a": 2}, _fresh) == "a: 2\n"
    # Texte illisible : rendu neuf.
    assert update_yaml("a: [cassé\n", {"a": 1}, _fresh) == "a: 1\n"


def test_7_nouveau_fichier_rendu_stable(tmp_path: Path) -> None:
    target = tmp_path / "nouveau.yaml"
    write_document(target, {"b": 1, "a": [1, 2]}, _fresh)
    first = target.read_text(encoding="utf-8")
    write_document(target, {"b": 1, "a": [1, 2]}, _fresh)
    assert target.read_text(encoding="utf-8") == first == _fresh({"b": 1, "a": [1, 2]})


def test_cle_ajoutee_a_sa_place_dans_un_fichier_trie() -> None:
    old = "description: x\nproperties:\n  a:\n    type: string\n  c:\n    type: string\ntitle: T\n"
    new = yaml.safe_load(old)
    new["properties"]["b"] = {"type": "integer"}
    out = update_yaml(old, new, _fresh)
    assert _changed(old, out) == ["+  b:", "+    type: integer"]


def test_fusion_ecrite_sur_le_brouillon_garde_ses_commentaires() -> None:
    """Le résultat d'une fusion s'écrit sur la version du brouillon."""
    base = b"a: 1\nb: 1\n"
    theirs = b"a: 1\nb: 2\n"
    mine = b"# commentaire du brouillon\na: 5  # retouche\nb: 1\n"
    merged = merge_object("x.yaml", base, theirs, mine)
    assert merged.kind == "auto"
    assert merged.result == b"# commentaire du brouillon\na: 5  # retouche\nb: 2\n"
    assert serialize("x.yaml", {"a": 1}) == b"a: 1\n"  # sans texte existant : rendu neuf


def test_render_hors_yaml_json_rendu_neuf() -> None:
    assert render("note.md", "ancien", "x", lambda v: f"neuf {v}") == "neuf x"


def test_cartographie_objet_modifie_garde_ses_commentaires(tmp_path: Path) -> None:
    """Intégration : `save_object` sur un objet commenté ne change que sa ligne."""
    from dataclasses import replace

    from ossie_studio.cartographie.space import read_cartographie, save_object

    types = tmp_path / "cartographie/types"
    types.mkdir(parents=True)
    (types / "processus.schema.yaml").write_text(
        "title: Processus\nx-archimate: BusinessProcess\ntype: object\n"
    )
    target = tmp_path / "cartographie/objets/processus/prc-x.yaml"
    target.parent.mkdir(parents=True)
    before = (
        "# relu par l'équipe métier\ncle: PRC-X\ntype: processus\nnom: Vente  # libellé court\n"
    )
    target.write_text(before)
    # Un objet est désigné par `type/CLÉ` (ticket 421832ee).
    _path, obj = read_cartographie(tmp_path).objects["processus/PRC-X"]
    save_object(tmp_path, replace(obj, name="Vente en ligne"), create=False)
    after = target.read_text()
    assert _changed(before, after) == [
        "-nom: Vente  # libellé court",
        # Le commentaire garde sa colonne ; la valeur plus longue la dépasse.
        "+nom: Vente en ligne # libellé court",
    ]


def test_referentiel_valeurs_ordre_garde_nouvelle_valeur_a_sa_place(tmp_path: Path) -> None:
    """Gael (28/11) : l'ordre des valeurs existantes est gardé, une nouvelle valeur
    se place dans l'ordre des clés ; le diff ne montre que la valeur ajoutée."""
    from ossie_studio.referentiels.repo import write_referentiel

    model = {
        "type": "object",
        "x-key": ["code"],
        "properties": {"code": {"type": "string"}, "nom": {"type": "string"}},
        "required": ["code"],
    }
    write_referentiel(tmp_path, "pays", model, [{"code": "FR", "nom": "France"}], create=True)
    values = tmp_path / "referentiels/pays/valeurs.json"
    # Un humain a réordonné le fichier : DE après FR.
    values.write_text(
        '[\n  {\n    "code": "FR",\n    "nom": "France"\n  },\n'
        '  {\n    "code": "DE",\n    "nom": "Allemagne"\n  }\n]\n'
    )
    before = values.read_text()
    write_referentiel(
        tmp_path,
        "pays",
        model,
        [
            {"code": "FR", "nom": "France"},
            {"code": "DE", "nom": "Allemagne"},
            {"code": "ES", "nom": "Espagne"},
        ],
        create=False,
    )
    after = values.read_text()
    # Ordre gardé (FR, DE) ; ES après son prédécesseur dans l'ordre des clés (DE).
    assert [v["code"] for v in json.loads(after)] == ["FR", "DE", "ES"]
    assert all(line.startswith("+") for line in _changed(before, after))


# --- Lot b : domaines, assets, gouvernance, imports (14bb8eb8) ---------------------


def test_domaine_extend_commente_garde_ses_commentaires(tmp_path: Path) -> None:
    from dataclasses import replace

    from ossie_studio.domains.documents import Domain, read_domain, write_domain

    write_domain(tmp_path, Domain(name="pudo", label="Pudo"))
    extend = tmp_path / "domaines/pudo.extend.yaml"
    before = "# domaine relu en comité\n" + extend.read_text()
    extend.write_text(before)
    domain = read_domain(tmp_path, "pudo")
    assert domain is not None
    write_domain(tmp_path, replace(domain, label="Pudo au colis"))
    after = extend.read_text()
    assert after.startswith("# domaine relu en comité\n")
    assert [line for line in _changed(before, after) if "label" not in line] == []


def test_asset_extend_ne_change_que_la_description(tmp_path: Path) -> None:
    from ossie_studio.sources.asset_files import load_asset, write_asset

    file = tmp_path / "sources/s/assets/commande.schema.yaml"
    write_asset(file, {"$id": "commande.schema.yaml", "title": "Commande", "x-description": "A"})
    ext = file.with_name("commande.extend.yaml")
    before = "# travail de l'équipe data\n" + ext.read_text()
    ext.write_text(before)
    asset = load_asset(file)
    asset["x-description"] = "B"
    schema_before = file.read_text()
    write_asset(file, asset)
    assert file.read_text() == schema_before  # schéma inchangé : pas réécrit
    after = ext.read_text()
    assert after.startswith("# travail de l'équipe data\n")
    assert len(_changed(before, after)) == 2


def test_regle_de_tags_commentee(tmp_path: Path) -> None:
    from dataclasses import replace

    from ossie_studio.governance.tag_rules import TagRule, rule_path, write_tag_rule

    rule = TagRule("pii", "PII", ("*.cs",), {"name": ("email",)}, ("pii",))
    write_tag_rule(tmp_path, rule)
    target = tmp_path / rule_path("pii")
    before = "# validée par la gouvernance\n" + target.read_text()
    target.write_text(before)
    write_tag_rule(tmp_path, replace(rule, name="Données personnelles"))
    after = target.read_text()
    assert _changed(before, after) == ["-name: PII", "+name: Données personnelles"]


def test_declaration_d_import_commentee(tmp_path: Path) -> None:
    from dataclasses import replace

    from ossie_studio.imports.declaration import ImportDeclaration
    from ossie_studio.imports.files import write_import

    decl = ImportDeclaration("pays", "Pays", "csv", separator=";")
    write_import(tmp_path, "referentiels/r/imports", decl, "{}", b"a;b\n", create=True)
    target = tmp_path / "referentiels/r/imports/pays.yaml"
    before = "# fichier fourni par la DSI\n" + target.read_text()
    target.write_text(before)
    write_import(tmp_path, "referentiels/r/imports", replace(decl, label="Pays ISO"),
                 "{}", b"a;b\n", create=False)  # fmt: skip
    after = target.read_text()
    assert _changed(before, after) == ["-label: Pays", "+label: Pays ISO"]
