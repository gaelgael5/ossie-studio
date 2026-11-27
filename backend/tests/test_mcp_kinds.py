"""Registre des genres d'écriture (epic « Primitives MCP — écriture commune par genre »,
enabler 7b4b8f3a) : chaque genre calcule le chemin qu'écrit l'écran, refuse une
identité invalide en nommant le champ, et applique sa règle sur le chemin."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from ossie_studio.cartographie.diagram import (
    Diagram,
    Graphic,
    Rules,
    View,
    save_diagram,
    save_rules,
)
from ossie_studio.cartographie.model import CartoObject, CartoType
from ossie_studio.cartographie.process import new_process_xml, save_process
from ossie_studio.cartographie.space import save_object, save_type
from ossie_studio.governance.actions import create_certification, create_scheme, create_tag
from ossie_studio.mcp_surface import kind_stores, kinds
from ossie_studio.mcp_surface.kind_base import IdentityField, KindError
from ossie_studio.mcp_surface.kinds import KINDS, WriteKind, write_kind
from ossie_studio.referentiels.repo import write_referentiel

MODEL = {
    "title": "Délais",
    "type": "object",
    "x-key": ["pays", "mode"],
    "required": ["pays", "mode"],
    "properties": {"pays": {"type": "string"}, "mode": {"type": "string"}},
}
PAYS = {
    "title": "Pays",
    "type": "object",
    "x-key": ["code"],
    "required": ["code"],
    "properties": {"code": {"type": "string"}, "nom": {"type": "string"}},
}


def test_le_registre_declare_les_genres_du_tableau() -> None:
    assert [k.name for k in KINDS] == [
        "carto_type",
        "carto_objet",
        "carto_modele",
        "carto_graphique",
        "carto_regles",
        "processus",
        "referentiel_modele",
        "referentiel_valeur",
        "tag",
        "classification",
        "certification",
    ]
    for kind in KINDS:
        assert kind.path_rule == "forbidden"  # aucun genre n'autorise le chemin aujourd'hui
        assert kind.standard and kind.capability.endswith(":write")
        assert kind.syntax == ("xpath" if kind.name == "processus" else "jsonpath")


# --- Critère 2 : le chemin calculé est celui qu'écrit l'écran (ses services) -------------


def _written(root: Path, path: str | None) -> bool:
    return path is not None and (root / path).is_file()


def test_carto_type_et_objet(tmp_path: Path) -> None:
    save_type(
        tmp_path, CartoType("application", "Application", "ApplicationComponent", {}), create=True
    )
    save_object(tmp_path, CartoObject("APP-CAISSE", "application", "Caisse"), create=True)
    assert _written(
        tmp_path, write_kind("carto_type").locate(tmp_path, {"slug": "application"}).path
    )
    located = write_kind("carto_objet").locate(
        tmp_path, {"type": "application", "key": "APP-CAISSE"}
    )
    assert located.path == "cartographie/objets/application/app-caisse.yaml"
    assert _written(tmp_path, located.path)


def test_modele_graphique_regles(tmp_path: Path) -> None:
    save_rules(tmp_path, "standard", Rules(lang="fr"))
    diagram = Diagram(
        id="MOD-VENTE",
        rules="cartographie/regles/standard.rules.yaml",
        graphic="cartographie/modeles/vente.graphique.yaml",
        name="Vente",
        views=[View(id="VUE-1", name="Vue")],
    )
    save_diagram(tmp_path, "vente", diagram, Graphic(), create=True)
    for name, slug in (
        ("carto_modele", "vente"),
        ("carto_graphique", "vente"),
        ("carto_regles", "standard"),
    ):
        assert _written(tmp_path, write_kind(name).locate(tmp_path, {"slug": slug}).path), name


def test_processus(tmp_path: Path) -> None:
    save_process(tmp_path, "PRC-VENTE", new_process_xml("PRC-VENTE", "Vente"), create=True)
    located = write_kind("processus").locate(tmp_path, {"key": "PRC-VENTE"})
    assert located.path == "cartographie/processus/prc-vente.bpmn" and _written(
        tmp_path, located.path
    )


def test_referentiel_modele_et_valeur(tmp_path: Path) -> None:
    write_referentiel(tmp_path, "delais", MODEL, [{"pays": "FR", "mode": "site"}], create=True)
    write_referentiel(tmp_path, "pays", PAYS, [{"code": "FR"}], create=True)
    model = write_kind("referentiel_modele").locate(tmp_path, {"referentiel": "delais"})
    assert _written(tmp_path, model.path)
    value = write_kind("referentiel_valeur").locate(
        tmp_path, {"referentiel": "delais", "key": {"mode": "site", "pays": "FR"}}
    )
    # Le fichier des valeurs, et la valeur visée dans l'ordre de la clé du modèle.
    assert _written(tmp_path, value.path) and value.path.endswith("delais/valeurs.json")
    assert value.entry == ("FR", "site")
    # Une clé d'un seul champ se donne seule.
    assert write_kind("referentiel_valeur").locate(
        tmp_path, {"referentiel": "pays", "key": "FR"}
    ).entry == ("FR",)


def test_tag_et_classification(tmp_path: Path) -> None:
    create_tag(tmp_path, "pii/email", {"label": "E-mail"})
    create_scheme(
        tmp_path,
        {"id": "sensibilite", "name": "Sensibilité"},
        [{"name": "Public", "short_name": "PUB"}],
    )
    assert _written(tmp_path, write_kind("tag").locate(tmp_path, {"id": "pii/email"}).path)
    located = write_kind("classification").locate(tmp_path, {"id": "sensibilite"})
    assert _written(tmp_path, located.path)


def test_certification_par_son_slug(tmp_path: Path) -> None:
    """Décision de Gael : le slug identifie ; le fichier, nommé par le service à la
    création, est retrouvé dans l'espace."""
    kind = write_kind("certification")
    assert kind.locate(tmp_path, {"slug": "or"}).path is None  # nouvelle : le service la nommera
    cert = create_certification(tmp_path, {"name": "Or", "rank": 1, "tag": "certif/or"}, slug="or")
    located = kind.locate(tmp_path, {"slug": "or"})
    assert located.path == f"governance/certifications/{cert.id}.yaml"
    assert _written(tmp_path, located.path)


# --- Critère 3 : identité invalide, le champ est nommé -----------------------------------


@pytest.mark.parametrize(
    ("name", "identity", "field"),
    [
        ("carto_type", {"slug": "Application"}, "slug"),
        ("carto_objet", {"type": "application", "key": ""}, "key"),
        ("carto_objet", {"type": "application", "key": "app caisse"}, "key"),
        ("carto_objet", {"type": "inconnu", "key": "APP-CAISSE"}, "type"),
        ("carto_objet", {"key": "APP-CAISSE"}, "type"),
        ("carto_modele", {"slug": "../vente"}, "slug"),
        ("processus", {"key": "prc-vente"}, "key"),
        ("referentiel_modele", {"referentiel": "Délais"}, "referentiel"),
        ("referentiel_valeur", {"referentiel": "absent", "key": "X"}, "referentiel"),
        ("referentiel_valeur", {"referentiel": "delais", "key": {"pays": "FR"}}, "key"),
        ("referentiel_valeur", {"referentiel": "delais", "key": "FR"}, "key"),
        ("tag", {"id": "../pii"}, "id"),
        ("classification", {"id": "a/b"}, "id"),
        ("tag", {"id": "pii", "extra": 1}, "extra"),
        ("certification", {"slug": "Or"}, "slug"),
    ],
)
def test_identite_invalide(
    tmp_path: Path, name: str, identity: dict[str, object], field: str
) -> None:
    save_type(
        tmp_path, CartoType("application", "Application", "ApplicationComponent", {}), create=True
    )
    write_referentiel(tmp_path, "delais", MODEL, [], create=True)
    with pytest.raises(KindError) as refused:
        write_kind(name).locate(tmp_path, identity)
    assert (refused.value.code, refused.value.field) == ("write_identity_invalid", field)
    assert field in refused.value.message


def test_genre_inconnu() -> None:
    with pytest.raises(KindError) as refused:
        write_kind("dataset")
    assert refused.value.code == "write_kind_unknown"
    assert "carto_objet" in refused.value.extra["expected"]


# --- Critère 4 : règle sur le chemin ------------------------------------------------------


def test_chemin_interdit(tmp_path: Path) -> None:
    with pytest.raises(KindError) as refused:
        write_kind("tag").locate(tmp_path, {"id": "pii"}, path="governance/tags/pii.yaml")
    assert (refused.value.code, refused.value.field) == ("write_path_forbidden", "path")
    assert "tag" in refused.value.message


def test_chemin_autorise_reste_sous_sa_racine(tmp_path: Path) -> None:
    allowed = WriteKind(
        name="essai",
        description="Test kind.",
        label="essai",
        identity=(IdentityField("id", "Id.", "slug"),),
        standard="essai",
        syntax="jsonpath",
        capability="essai:write",
        read_capability="essai:read",
        store=kind_stores.TagStore(),
        lister=lambda root, query: [],
        locator=lambda root, identity: kinds.Location(
            "essai", {"id": identity["id"]}, f"essais/{identity['id']}.yaml"
        ),
        path_rule="allowed",
        path_root="essais",
    )
    assert allowed.locate(tmp_path, {"id": "a"}).path == "essais/a.yaml"
    assert (
        allowed.locate(tmp_path, {"id": "a"}, path="essais/sous/a.yaml").path
        == "essais/sous/a.yaml"
    )
    for bad in ("autre/a.yaml", "essais/../secrets/a.yaml", "/essais/a.yaml"):
        with pytest.raises(KindError) as refused:
            allowed.locate(tmp_path, {"id": "a"}, path=bad)
        assert refused.value.code == "write_path_outside_root", bad


# --- Critère 5 : aucune constante d'emplacement recopiée ---------------------------------


@pytest.mark.parametrize("module", [kinds, kind_stores])
def test_aucun_emplacement_recopie(module: object) -> None:
    source = inspect.getsource(module)  # type: ignore[arg-type]
    code = re.sub(r'"""[\s\S]*?"""', "", source)  # sans les docstrings
    for literal in ("cartographie/", "governance/", "referentiels/", ".yaml", ".bpmn", ".json"):
        assert literal not in code, literal


# --- Validateurs : le service de l'écran, par genre --------------------------------------


def _roundtrip(root: Path, name: str, identity: dict[str, object], content: object) -> object:
    kind = write_kind(name)
    location = kind.locate(root, identity)
    assert kind.store.read(root, location) is None
    kind.store.write(root, location, content, create=True)
    location = kind.locate(root, identity)  # une certification a maintenant son fichier
    read = kind.store.read(root, location)
    assert read is not None and read.version and read.path == location.path
    return read.content


def test_validateurs_cartographie(tmp_path: Path) -> None:
    _roundtrip(
        tmp_path,
        "carto_type",
        {"slug": "application"},
        {"title": "Application", "x-archimate": "ApplicationComponent", "type": "object"},
    )
    content = _roundtrip(
        tmp_path,
        "carto_objet",
        {"type": "application", "key": "APP-CAISSE"},
        {"cle": "APP-CAISSE", "type": "application", "nom": "Caisse"},
    )
    assert isinstance(content, dict) and content["nom"] == "Caisse"
    kind = write_kind("carto_objet")
    location = kind.locate(tmp_path, {"type": "application", "key": "APP-CAISSE"})
    with pytest.raises(KindError) as mismatch:
        kind.store.write(
            tmp_path, location, {"cle": "APP-AUTRE", "type": "application"}, create=False
        )
    assert (mismatch.value.code, mismatch.value.field) == ("write_identity_mismatch", "$.cle")
    with pytest.raises(KindError) as unknown:
        kind.store.write(
            tmp_path, location, {"cle": "APP-CAISSE", "couleur": "rouge"}, create=False
        )
    assert (unknown.value.code, unknown.value.field) == ("write_content_invalid", "$.couleur")
    kind.store.delete(tmp_path, location)
    assert kind.store.read(tmp_path, location) is None


def test_validateurs_modele_graphique_regles(tmp_path: Path) -> None:
    _roundtrip(tmp_path, "carto_regles", {"slug": "standard"}, {"lang": "fr"})
    _roundtrip(
        tmp_path,
        "carto_modele",
        {"slug": "vente"},
        {
            "id": "MOD-VENTE",
            "nom": "Vente",
            "regles": "cartographie/regles/standard.rules.yaml",
            "graphique": "cartographie/modeles/vente.graphique.yaml",
            "vues": [{"id": "VUE-1", "nom": "Vue"}],
        },
    )
    graphic = write_kind("carto_graphique")
    location = graphic.locate(tmp_path, {"slug": "vente"})
    graphic.store.write(tmp_path, location, {"vues": {"VUE-1": {}}}, create=False)
    with pytest.raises(KindError) as refused:
        graphic.store.delete(tmp_path, location)
    assert refused.value.code == "write_delete_unsupported"


def test_validateur_processus(tmp_path: Path) -> None:
    from ossie_studio.cartographie.process import with_detail

    save_type(
        tmp_path, CartoType("processus-metier", "Processus", "BusinessProcess", {}), create=True
    )
    save_object(tmp_path, CartoObject("PRC-VENTE", "processus-metier", "Vente"), create=True)
    kind = write_kind("processus")
    location = kind.locate(tmp_path, {"key": "PRC-VENTE-DETAIL"})
    bare = new_process_xml("PRC-VENTE-DETAIL", "Détail")
    with pytest.raises(KindError) as unattached:  # décision de Gael : rattaché pour un agent
        kind.store.write(tmp_path, location, bare, create=True)
    assert unattached.value.code == "write_process_unattached"
    attached = with_detail(bare, "PRC-VENTE", "processus-metier")
    assert kind.store.write(tmp_path, location, attached, create=True) == []
    read = kind.store.read(tmp_path, location)
    assert read is not None and isinstance(read.content, str) and "PRC-VENTE" in read.content


def test_validateurs_referentiel(tmp_path: Path) -> None:
    _roundtrip(tmp_path, "referentiel_modele", {"referentiel": "delais"}, MODEL)
    kind = write_kind("referentiel_valeur")
    fr = kind.locate(tmp_path, {"referentiel": "delais", "key": {"pays": "FR", "mode": "site"}})
    be = kind.locate(tmp_path, {"referentiel": "delais", "key": {"pays": "BE", "mode": "site"}})
    kind.store.write(tmp_path, fr, {"pays": "FR", "mode": "site"}, create=True)
    before = kind.store.read(tmp_path, fr)
    # Écrire une autre valeur ne rend pas la première périmée : sa version est la sienne.
    kind.store.write(tmp_path, be, {"mode": "site"}, create=True)  # la clé se déduit de l'identité
    after = kind.store.read(tmp_path, fr)
    assert before is not None and after is not None and before.version == after.version
    assert kind.store.read(tmp_path, be) is not None
    with pytest.raises(KindError) as mismatch:
        kind.store.write(tmp_path, fr, {"pays": "DE", "mode": "site"}, create=False)
    assert mismatch.value.code == "write_identity_mismatch"
    kind.store.delete(tmp_path, be)
    assert kind.store.read(tmp_path, be) is None and kind.store.read(tmp_path, fr) is not None
    with pytest.raises(KindError) as refused:
        write_kind("referentiel_modele").store.delete(
            tmp_path, write_kind("referentiel_modele").locate(tmp_path, {"referentiel": "delais"})
        )
    assert refused.value.code == "write_delete_unsupported"


def test_validateurs_gouvernance(tmp_path: Path) -> None:
    tag = _roundtrip(
        tmp_path, "tag", {"id": "pii/email"}, {"info": {"owner": "dpo", "description": "E-mail"}}
    )
    assert isinstance(tag, dict) and tag["info"]["owner"] == "dpo"
    scheme = _roundtrip(
        tmp_path,
        "classification",
        {"id": "sensibilite"},
        {"name": "Sensibilité", "levels": [{"name": "Public", "short_name": "PUB"}]},
    )
    assert isinstance(scheme, dict)
    public = scheme["levels"][0]
    # Remplacer : un niveau modifié (par son id), un ajouté (sans id), dans cet ordre.
    kind = write_kind("classification")
    location = kind.locate(tmp_path, {"id": "sensibilite"})
    kind.store.write(
        tmp_path,
        location,
        {
            "id": "sensibilite",
            "name": "Sensibilité",
            "levels": [
                {"name": "Interne", "short_name": "INT"},
                {"id": public["id"], "name": "Public", "short_name": "PUB", "color": "green"},
            ],
        },
        create=False,
    )
    read = kind.store.read(tmp_path, location)
    assert read is not None
    assert [(lv["name"], lv.get("color", "")) for lv in read.content["levels"]] == [
        ("Interne", ""),
        ("Public", "green"),
    ]
    cert = _roundtrip(
        tmp_path, "certification", {"slug": "or"}, {"name": "Or", "rank": 1, "tag": "certif/or"}
    )
    assert isinstance(cert, dict) and cert["slug"] == "or"
    certification = write_kind("certification")
    certification.store.delete(tmp_path, certification.locate(tmp_path, {"slug": "or"}))
    assert certification.locate(tmp_path, {"slug": "or"}).path is None
