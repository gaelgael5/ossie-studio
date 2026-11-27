"""Onglet Imports côté serveur (ticket cda9c43a) : déclarer un import, déposer un
fichier d'exemple (CSV, JSON), aperçu et chemins détectés, essayer un template
JSLT (moteur urban-json repris de Miles), enregistrer le template ; aucune valeur
du référentiel n'est modifiée."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.imports.jslt_engine import function_registry
from ossie_studio.referentiels.model import dump_model
from ossie_studio.referentiels.repo import model_path, values_path
from ossie_studio.referentiels.values import dump_values
from ossie_studio.spaces.branch_service import KIND_CHILD, db_create_branch
from tests.space_fixture import ApiSpace, forge_edit, make_api_space

MODEL: dict[str, Any] = {
    "title": "Délais",
    "type": "object",
    "x-key": ["pays", "mode"],
    "required": ["pays", "mode"],
    "properties": {
        "pays": {"type": "string"},
        "mode": {"type": "string"},
        "delai": {"type": "integer"},
    },
}
VALUES = [{"pays": "FR", "mode": "site", "delai": 1}, {"pays": "BE", "mode": "site", "delai": 3}]
CSV = "Pays;Mode;Delai\nFR;site;2\nBE;site;3\nDE;domicile;x\nIT;site;5\n"
TEMPLATE = '{"pays": $.Pays, "mode": $.Mode, "delai": $.Delai#integer}\n'


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    api_space = await make_api_space(client, app, tmp_path)
    forge_edit(
        api_space,
        {
            model_path("delais"): dump_model(MODEL),
            values_path("delais"): dump_values(MODEL, VALUES),
        },
    )
    return api_space


def _url(space: ApiSpace, rest: str = "") -> str:
    return f"{space.base}/referentiels/delais/imports{rest}"


async def _csv_import(space: ApiSpace) -> None:
    created = await space.client.post(
        _url(space), json={"label": "Transporteur", "slug": "transporteur", "format": "csv"}
    )
    assert created.status_code == 201, created.text
    uploaded = await space.client.put(
        _url(space, "/transporteur/exemple"), files={"file": ("t.csv", CSV.encode(), "text/csv")}
    )
    assert uploaded.status_code == 200, uploaded.text
    reading = await space.client.put(
        _url(space, "/transporteur/lecture"),
        json={"separator": ";", "encoding": "utf-8", "header": True},
    )
    assert reading.status_code == 200, reading.text


# --- Critère 1 : déclarer un import CSV -------------------------------------------------------


async def test_ajouter_un_import_csv(space: ApiSpace) -> None:
    created = await space.client.post(
        _url(space), json={"label": "Transporteur", "slug": "transporteur", "format": "csv"}
    )
    assert created.status_code == 201, created.text
    assert created.json()["paths"] == [
        "referentiels/delais/imports/transporteur.yaml",
        "referentiels/delais/imports/transporteur.jslt",
        "referentiels/delais/imports/transporteur.exemple.csv",
    ]
    listing = (await space.client.get(_url(space))).json()["imports"]
    assert [(i["slug"], i["label"], i["format"], i["last_import"]) for i in listing] == [
        ("transporteur", "Transporteur", "csv", None)
    ]
    taken = await space.client.post(
        _url(space), json={"label": "Autre", "slug": "transporteur", "format": "json"}
    )
    assert taken.status_code == 409
    assert taken.json()["error"] == "import_slug_taken"


async def test_renommer_puis_supprimer(space: ApiSpace) -> None:
    await _csv_import(space)
    renamed = await space.client.patch(_url(space, "/transporteur"), json={"label": "Colissimo"})
    assert renamed.json()["label"] == "Colissimo"
    assert renamed.json()["slug"] == "transporteur"
    assert (await space.client.delete(_url(space, "/transporteur"))).status_code == 204
    assert (await space.client.get(_url(space))).json()["imports"] == []
    missing = await space.client.get(_url(space, "/transporteur"))
    assert missing.status_code == 404
    assert missing.json()["error"] == "import_not_found"


# --- Critère 2 : fichier d'exemple, aperçu, chemins, séparateur ----------------------------


async def test_exemple_csv_apercu_et_chemins(space: ApiSpace) -> None:
    await _csv_import(space)
    view = (await space.client.post(_url(space, "/transporteur/apercu"))).json()
    assert view["columns"] == ["Pays", "Mode", "Delai"]
    assert view["rows"][0] == {"Pays": "FR", "Mode": "site", "Delai": "2"}
    assert view["count"] == 4
    assert [p["path"] for p in view["paths"]] == ["$.Pays", "$.Mode", "$.Delai"]
    # Un autre séparateur, avant même de l'enregistrer : l'aperçu suit.
    comma = (
        await space.client.post(
            _url(space, "/transporteur/apercu"),
            json={"separator": ",", "encoding": "utf-8", "header": True},
        )
    ).json()
    assert comma["columns"] == ["Pays;Mode;Delai"]


async def test_depot_detecte_encodage_et_separateur_cp1252(space: ApiSpace) -> None:
    """Ticket 123631e5 : fichier Excel de Gael (cp1252, « ; », espace insécable) ;
    sans détection, l'import restait en utf-8 / « , » et rien ne se lisait."""
    await space.client.post(
        _url(space), json={"label": "Carriers", "slug": "carriers", "format": "csv"}
    )
    raw = "_id;Label\r\nDPD\xa0 Switzerland;DPD\xa0 Switzerland\r\nUPS;UPS\r\n".encode("cp1252")
    detail = (
        await space.client.put(
            _url(space, "/carriers/exemple"), files={"file": ("c.csv", raw, "text/csv")}
        )
    ).json()
    assert (detail["encoding"], detail["separator"]) == ("cp1252", ";")
    view = (await space.client.post(_url(space, "/carriers/apercu"))).json()
    assert [p["path"] for p in view["paths"]] == ["$._id", "$.Label"]
    assert view["rows"][0]["Label"] == "DPD\xa0 Switzerland"


async def test_depot_utf8_bom_virgule_sans_bom_dans_la_colonne(space: ApiSpace) -> None:
    await space.client.post(_url(space), json={"label": "B", "slug": "bom", "format": "csv"})
    raw = "\ufeffcode,nom\nDHL,DHL Express\n".encode()
    detail = (
        await space.client.put(
            _url(space, "/bom/exemple"), files={"file": ("b.csv", raw, "text/csv")}
        )
    ).json()
    assert (detail["encoding"], detail["separator"]) == ("utf-8", ",")
    view = (await space.client.post(_url(space, "/bom/apercu"))).json()
    assert view["columns"] == ["code", "nom"]


async def test_depot_une_colonne_garde_le_separateur(space: ApiSpace) -> None:
    await _csv_import(space)
    detail = (
        await space.client.put(
            _url(space, "/transporteur/exemple"),
            files={"file": ("u.csv", b"code\nDHL\nUPS\n", "text/csv")},
        )
    ).json()
    assert (detail["encoding"], detail["separator"]) == ("utf-8", ";")


async def test_depot_json_ne_touche_pas_la_lecture(space: ApiSpace) -> None:
    created = await space.client.post(
        _url(space), json={"label": "J", "slug": "j", "format": "json"}
    )
    before = created.json()
    detail = (
        await space.client.put(
            _url(space, "/j/exemple"), files={"file": ("j.json", b'[{"a";1}]', "application/json")}
        )
    ).json()
    assert (detail["encoding"], detail["separator"]) == (before["encoding"], before["separator"])


# --- Critères 3 et 4 : essayer le template, erreurs rattachées à la ligne -------------------


async def test_essai_du_template_compte_et_liste_les_differences(space: ApiSpace) -> None:
    await _csv_import(space)
    result = (
        await space.client.post(_url(space, "/transporteur/essai"), json={"template": TEMPLATE})
    ).json()
    assert (result["lines"], result["new"], result["modified"], result["unchanged"]) == (4, 1, 1, 1)
    assert result["errors"] == 1
    [error] = result["error_details"]
    assert error["where"] == "ligne 4"
    assert (error["code"], error["path"], error["expected"]) == (
        "template_conversion",
        "$.Delai",
        "integer",
    )
    kinds = {(d["key"], d["kind"]) for d in result["differences"]}
    assert kinds == {("FR+site", "modified"), ("IT+site", "new")}
    modified = next(d for d in result["differences"] if d["kind"] == "modified")
    assert modified["changes"] == [{"property": "delai", "before": 1, "after": 2}]
    assert modified["where"] == "ligne 2"


async def test_valeur_produite_invalide_rattachee_a_sa_ligne(space: ApiSpace) -> None:
    await _csv_import(space)
    no_mode = '{"pays": $.Pays, "delai": $.Delai#integer}\n'
    result = (
        await space.client.post(_url(space, "/transporteur/essai"), json={"template": no_mode})
    ).json()
    wheres = {(e["where"], e["code"], e["field"]) for e in result["error_details"]}
    assert ("ligne 2", "value_required", "mode") in wheres


# --- Critère 5 : template faux, ligne surlignée ------------------------------------------------


async def test_template_syntaxiquement_faux(space: ApiSpace) -> None:
    await _csv_import(space)
    broken = '{\n  "pays": $.Pays,\n  "mode" $.Mode\n}\n'
    result = (
        await space.client.post(_url(space, "/transporteur/essai"), json={"template": broken})
    ).json()
    assert result["ok"] is False
    [problem] = result["template_problems"]
    assert problem["code"] == "template_syntax"
    assert problem["line"] == 3
    assert problem["message"]


# --- Critère 6 : enregistrer le template, aucune valeur modifiée ------------------------------


async def test_enregistrer_le_template_sans_toucher_aux_valeurs(space: ApiSpace) -> None:
    await _csv_import(space)
    saved = await space.client.put(
        _url(space, "/transporteur/template"), json={"template": TEMPLATE}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["template_problems"] == []
    assert (await space.client.get(_url(space, "/transporteur"))).json()["template"] == TEMPLATE
    values = (await space.client.get(f"{space.base}/referentiels/delais/valeurs")).json()
    assert values["count"] == 2


# --- Critère 7 : la même chose en JSON ---------------------------------------------------------


async def test_import_json(space: ApiSpace) -> None:
    created = await space.client.post(
        _url(space), json={"label": "API", "slug": "api", "format": "json"}
    )
    assert created.status_code == 201
    data = [
        {"code": {"pays": "FR", "mode": "site"}, "jours": 2},
        {"code": {"pays": "ES", "mode": "site"}, "jours": 4},
    ]
    await space.client.put(
        _url(space, "/api/exemple"),
        files={"file": ("a.json", json.dumps(data).encode(), "application/json")},
    )
    view = (await space.client.post(_url(space, "/api/apercu"))).json()
    assert {"$.code", "$.code.pays", "$.code.mode", "$.jours"} <= {p["path"] for p in view["paths"]}
    template = '{"pays": $.code.pays, "mode": $.code.mode, "delai": $.jours}\n'
    result = (
        await space.client.post(_url(space, "/api/essai"), json={"template": template})
    ).json()
    assert (result["new"], result["modified"], result["errors"]) == (1, 1, 0)
    assert {d["where"] for d in result["differences"]} == {"élément [0]", "élément [1]"}


# --- Critères 8 et 9 : lecture seule, droits, éditeur servi ------------------------------------


async def test_racine_en_lecture_seule(app: FastAPI, space: ApiSpace) -> None:
    async with app.state.pool.acquire() as conn:
        root = await db_create_branch(
            conn, uuid.UUID(space.space_id), "main", "main", KIND_CHILD, git_ref="main"
        )
    url = f"/api/spaces/{space.space_id}/branches/{root.id}/referentiels/delais/imports"
    # Bug e7deb2b9 : plus de refus « branche racine » (brouillon de la personne).
    answer = await space.client.post(url, json={"label": "X", "slug": "x", "format": "csv"})
    assert answer.json().get("error") != "branch_root_readonly", answer.text


async def test_editeur_et_catalogue_servis_par_l_application(space: ApiSpace) -> None:
    script = await space.client.get("/api/jslt-editor/jslt-editor.js")
    assert script.status_code == 200
    catalog = (await space.client.get("/api/jslt-functions")).json()
    assert len(catalog["functions"]) > 10
    # Exactement ce qu'accepte le moteur des imports : ni plugin ni fonction écartée.
    names = {f["name"] for f in catalog["functions"]}
    assert names == set(function_registry().names()) and "today" not in names
