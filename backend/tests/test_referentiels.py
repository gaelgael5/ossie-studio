"""Format d'un référentiel dans l'espace Git (epic « Référentiels », enabler
e9a87d3c) : modèle YAML avec clé composée et objets imbriqués, valeurs validées
à toute profondeur, sortie déterministe, imports en trois fichiers, écriture
dans le brouillon de la personne, jamais sur la branche racine."""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.api.branches import space_credentials
from ossie_studio.drafts.validation import run_rules
from ossie_studio.referentiels.errors import Problem, ReferentielError
from ossie_studio.referentiels.imports import ImportDeclaration
from ossie_studio.referentiels.model import SEPARATOR, check_model, dump_model
from ossie_studio.referentiels.repo import (
    list_referentiels,
    model_path,
    read_imports,
    read_referentiel,
    values_path,
    write_import,
    write_referentiel,
)
from ossie_studio.referentiels.service import ReferentielTarget
from tests.space_fixture import ApiSpace, admin_workdir, forge_read, make_api_space

S = SEPARATOR

MODEL: dict[str, Any] = {
    "title": "Délais de livraison",
    "description": "Délai par pays et mode de livraison,\nselon le poids du colis.",
    "type": "object",
    "x-key": ["pays", "mode"],
    "required": ["pays", "mode", "libelle"],
    "properties": {
        "pays": {"type": "string", "description": "Code ISO du pays"},
        "mode": {"type": "string", "enum": ["site", "domicile"]},
        "libelle": {"type": "string"},
        "actif": {"type": "boolean"},
        "ouverture": {"type": "string", "format": "date"},
        "paliers": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["poids_max", "delai_jours"],
                "properties": {
                    "poids_max": {"type": "number"},
                    "delai_jours": {"type": "integer"},
                },
            },
        },
        "contact": {"type": "object", "properties": {"email": {"type": "string"}}},
    },
}

FR_SITE: dict[str, Any] = {
    "pays": "FR",
    "mode": "site",
    "libelle": "Retrait en point relais",
    "actif": True,
    "ouverture": "2026-01-15",
    "paliers": [
        {"poids_max": 1, "delai_jours": 2},
        {"poids_max": 5, "delai_jours": 3},
        {"poids_max": 30, "delai_jours": 5},
    ],
}
BE_DOMICILE: dict[str, Any] = {
    "pays": "BE",
    "mode": "domicile",
    "libelle": "Livraison à domicile",
    "contact": {"email": "be@example.org"},
}


def _codes(exc: ReferentielError) -> set[tuple[str, str]]:
    return {(p.path, p.code) for p in exc.problems}


# --- Critère 1 : clé de deux propriétés et tableau d'objets : écrit, relu, identique ----------


def test_ecrit_relu_identique(tmp_path: Path) -> None:
    written = write_referentiel(tmp_path, "delais", MODEL, [FR_SITE, BE_DOMICILE], create=True)
    assert list_referentiels(tmp_path) == ["delais"]
    read = read_referentiel(tmp_path, "delais")
    assert read == written
    assert read.model == MODEL
    # Triées par clé (pays, mode) : BE+domicile avant FR+site ; tableaux dans leur ordre.
    assert read.values == [BE_DOMICILE, FR_SITE]
    assert [p["poids_max"] for p in read.values[1]["paliers"]] == [1, 5, 30]
    # Le modèle est du YAML, propriétés dans l'ordre de déclaration.
    text = (tmp_path / model_path("delais")).read_text(encoding="utf-8")
    assert text.startswith("title: Délais de livraison\n")
    assert text.index("pays:") < text.index("mode:") < text.index("paliers:")
    assert "x-key:\n- pays\n- mode\n" in text


# --- Critère 2 : clé composée en double ---------------------------------------------------------


def test_cle_composee_en_double_refusee(tmp_path: Path) -> None:
    twin = {**FR_SITE, "libelle": "Autre libellé"}
    with pytest.raises(ReferentielError) as caught:
        write_referentiel(tmp_path, "delais", MODEL, [FR_SITE, BE_DOMICILE, twin], create=True)
    assert caught.value.code == "values_invalid"
    assert Problem("FR+site", "key_duplicate", "FR+site") in caught.value.problems
    assert "FR+site" in str(caught.value)
    assert not (tmp_path / values_path("delais")).exists()


# --- Critère 3 : erreur dans un élément de tableau, avec son chemin complet ----------------------


def test_erreur_dans_un_element_de_tableau_avec_son_chemin(tmp_path: Path) -> None:
    broken = copy.deepcopy(FR_SITE)
    del broken["paliers"][2]["delai_jours"]
    broken["paliers"][1]["poids_max"] = "cinq"
    with pytest.raises(ReferentielError) as caught:
        write_referentiel(tmp_path, "delais", MODEL, [broken], create=True)
    assert _codes(caught.value) == {
        (f"FR+site{S}paliers[2]{S}delai_jours", "value_required"),
        (f"FR+site{S}paliers[1]{S}poids_max", "value_type"),
    }
    assert f"FR+site{S}paliers[2]{S}delai_jours : value_required" in str(caught.value)


def test_autres_ecarts_des_valeurs(tmp_path: Path) -> None:
    odd = {
        **BE_DOMICILE,
        "mode": "drone",
        "ouverture": "15/01/2026",
        "couleur": "bleu",
        "contact": {"email": "x@example.org", "tel": "0102"},
    }
    no_key = {"libelle": "Sans clé"}
    with pytest.raises(ReferentielError) as caught:
        write_referentiel(tmp_path, "delais", MODEL, [odd, no_key], create=True)
    codes = _codes(caught.value)
    # Clé illisible (« drone » n'est pas permis mais la clé reste lisible).
    assert (f"BE+drone{S}mode", "value_enum") in codes
    assert (f"BE+drone{S}ouverture", "value_format") in codes
    assert (f"BE+drone{S}couleur", "value_unknown_property") in codes
    assert (f"BE+drone{S}contact{S}tel", "value_unknown_property") in codes
    # Valeur sans clé : désignée par son rang.
    assert (f"#2{S}pays", "value_required") in codes
    assert (f"#2{S}mode", "value_required") in codes


# --- Critère 4 : clé sur une propriété inexistante, facultative, objet ou tableau -----------------


@pytest.mark.parametrize(
    ("key", "code"),
    [
        (["pays", "fantome"], "key_property_unknown"),
        (["pays", "actif"], "key_property_optional"),
        (["contact"], "key_property_not_simple"),
        (["paliers"], "key_property_not_simple"),
        ([], "key_invalid"),
        (["pays", "pays"], "key_invalid"),
    ],
)
def test_cle_invalide_modele_refuse(tmp_path: Path, key: list[str], code: str) -> None:
    model = {**MODEL, "x-key": key}
    with pytest.raises(ReferentielError) as caught:
        write_referentiel(tmp_path, "delais", model, [], create=True)
    assert caught.value.code == "model_invalid"
    assert code in {p.code for p in caught.value.problems}
    assert not (tmp_path / model_path("delais")).exists()


def test_types_et_structure_du_modele() -> None:
    model = copy.deepcopy(MODEL)
    model["properties"]["poids"] = {"type": "null"}
    model["properties"]["horaire"] = {"type": "string", "format": "time"}
    model["properties"]["paliers"]["items"]["properties"]["unite"] = {
        "type": "string",
        "enum": [],
    }
    model["properties"]["sites"] = {"type": "array"}
    model["required"] = [*model["required"], "absente"]
    assert set((p.path, p.code) for p in check_model(model)) == {
        ("poids", "property_type_unsupported"),
        ("horaire", "property_format_unsupported"),
        (f"paliers[]{S}unite", "property_enum_invalid"),
        ("sites", "property_items_missing"),
        ("absente", "required_unknown"),
    }
    assert check_model({"type": "array"}) == [Problem("", "model_not_object")]
    assert check_model(MODEL) == []


# --- Critère 5 : mêmes octets ---------------------------------------------------------------------


def test_deux_ecritures_memes_octets(tmp_path: Path) -> None:
    write_referentiel(tmp_path, "delais", MODEL, [FR_SITE, BE_DOMICILE], create=True)
    first = (
        (tmp_path / model_path("delais")).read_bytes(),
        (tmp_path / values_path("delais")).read_bytes(),
    )
    # Même contenu, autre ordre des valeurs et des clés dans chaque objet.
    shuffled = [dict(reversed(list(v.items()))) for v in (BE_DOMICILE, FR_SITE)]
    write_referentiel(tmp_path, "delais", copy.deepcopy(MODEL), shuffled, create=False)
    second = (
        (tmp_path / model_path("delais")).read_bytes(),
        (tmp_path / values_path("delais")).read_bytes(),
    )
    assert first == second
    assert first[0] == dump_model(MODEL).encode()
    # Clés JSON triées dans chaque objet.
    values = json.loads(first[1])
    assert list(values[0]) == sorted(values[0])


def test_creer_un_nom_pris_ou_modifier_un_absent(tmp_path: Path) -> None:
    write_referentiel(tmp_path, "delais", MODEL, [], create=True)
    with pytest.raises(ReferentielError) as taken:
        write_referentiel(tmp_path, "delais", MODEL, [], create=True)
    assert taken.value.code == "referentiel_name_taken"
    with pytest.raises(ReferentielError) as missing:
        write_referentiel(tmp_path, "absent", MODEL, [], create=False)
    assert missing.value.code == "referentiel_not_found"
    with pytest.raises(ReferentielError) as bad_name:
        write_referentiel(tmp_path, "Délais/../x", MODEL, [], create=True)
    assert bad_name.value.code == "referentiel_name_invalid"


# --- Critère 6 : un import, ses trois fichiers ; slug déjà pris ----------------------------------


def test_import_trois_fichiers_slug_pris(tmp_path: Path) -> None:
    write_referentiel(tmp_path, "delais", MODEL, [], create=True)
    decl = ImportDeclaration("transporteur", "Fichier du transporteur", "csv", ";", "latin-1")
    template = '{"pays": .Pays, "mode": .Mode}\n'
    example = "Pays;Mode\nFR;site\n".encode("latin-1")
    write_import(tmp_path, "delais", decl, template, example, create=True)

    folder = tmp_path / "referentiels" / "delais" / "imports"
    assert sorted(p.name for p in folder.iterdir()) == [
        "transporteur.exemple.csv",
        "transporteur.jslt",
        "transporteur.yaml",
    ]
    [read] = read_imports(tmp_path, "delais")
    assert (read.declaration, read.template, read.example) == (decl, template, example)
    assert read_referentiel(tmp_path, "delais").imports == [read]

    with pytest.raises(ReferentielError) as taken:
        write_import(tmp_path, "delais", decl, template, example, create=True)
    assert taken.value.code == "import_slug_taken"

    # Passé en JSON : l'ancien exemple CSV disparaît.
    as_json = ImportDeclaration("transporteur", "Fichier du transporteur", "json")
    write_import(tmp_path, "delais", as_json, template, b"[]", create=False)
    assert sorted(p.name for p in folder.iterdir()) == [
        "transporteur.exemple.json",
        "transporteur.jslt",
        "transporteur.yaml",
    ]
    assert read_imports(tmp_path, "delais")[0].declaration == as_json


def test_import_invalide(tmp_path: Path) -> None:
    write_referentiel(tmp_path, "delais", MODEL, [], create=True)
    bad = ImportDeclaration("x", " ", "csv", ";;", "klingon")
    with pytest.raises(ReferentielError) as caught:
        write_import(tmp_path, "delais", bad, "", b"", create=True)
    assert {p.code for p in caught.value.problems} == {
        "import_label_required",
        "import_separator_invalid",
        "import_encoding_unknown",
    }
    with pytest.raises(ReferentielError) as orphan:
        write_import(tmp_path, "absent", bad, "", b"", create=True)
    assert orphan.value.code == "referentiel_not_found"


# --- Le validateur des conventions ne s'applique pas à referentiels/ ---


def test_les_regles_de_validation_ignorent_les_referentiels(tmp_path: Path) -> None:
    write_referentiel(tmp_path, "delais", MODEL, [FR_SITE], create=True)
    write_import(
        tmp_path,
        "delais",
        ImportDeclaration("t", "T", "json"),
        "{}",
        b"not json at all",
        create=True,
    )
    assert run_rules(tmp_path) == []


# --- Critère 7 et écriture : brouillon de la personne, jamais la branche racine ---


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    return await make_api_space(client, app, tmp_path)


@pytest.fixture
async def target(app: FastAPI, space: ApiSpace) -> ReferentielTarget:
    async with app.state.pool.acquire() as conn:
        git_space, username, token = await space_credentials(
            conn, app.state.crypto, uuid.UUID(space.space_id)
        )
    workdir = await admin_workdir(app, space)
    return ReferentielTarget(workdir, git_space, "work/principale", username, token, "admin@x.org")


async def test_ecriture_dans_le_brouillon_jamais_publiee(
    space: ApiSpace, target: ReferentielTarget
) -> None:
    written, revision = await target.write(
        "delais",
        lambda root: write_referentiel(root, "delais", MODEL, [FR_SITE], create=True),
    )
    assert revision is not None and revision.startswith("draft:")
    assert written.values == [FR_SITE]
    assert await target.read(lambda root: read_referentiel(root, "delais")) == written
    # Rien n'est publié : la branche sur la forge n'a pas le référentiel.
    assert forge_read(space, model_path("delais")) is None
    # Un refus de validation n'écrit rien.
    with pytest.raises(ReferentielError):
        await target.write(
            "delais",
            lambda root: write_referentiel(root, "delais", MODEL, [{"pays": "FR"}], create=False),
        )
    assert await target.read(lambda root: read_referentiel(root, "delais").values) == [FR_SITE]


async def test_branche_racine_en_lecture_seule(target: ReferentielTarget) -> None:
    on_root = ReferentielTarget(
        target.workdir, target.space, "main", target.username, target.token, target.author
    )
    with pytest.raises(ReferentielError) as caught:
        await on_root.write(
            "delais",
            lambda root: write_referentiel(root, "delais", MODEL, [], create=True),
        )
    assert (caught.value.code, caught.value.status, caught.value.detail) == (
        "branch_root_readonly",
        409,
        {"ref": "main"},
    )
