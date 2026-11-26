"""Onglet « Modèles » côté serveur (epic « Cartographie — modèles et vues », ticket
5a05c7bb) : liste avec aperçu, dates et état du brouillon ; créer (modèle, graphique,
règles vides au besoin) ; supprimer sans toucher aux objets ; un objet retenu par un
modèle ne se supprime pas ; la fiche d'un objet nomme ses modèles ; droits."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jsonschema import Draft202012Validator

from ossie_studio.cartographie.diagram import contract_schema
from ossie_studio.spaces.branch_service import KIND_CHILD, db_create_branch
from tests.conftest import create_plain_user, login
from tests.space_fixture import ApiSpace, forge_edit, make_api_space

PASSWORD = "motdepasse-solide"
MODEL = """\
id: MOD-BOUTIQUE-APPLICATIF
nom: Boutique — vue applicative
description: Ce que la boutique expose et ce qu'elle manipule.
regles: cartographie/regles/standard.rules.yaml
graphique: cartographie/modeles/boutique-applicatif.graphique.yaml
documents:
- cartographie/objets/application/app-boutique.yaml
- cartographie/objets/document-metier/obj-commande.yaml
- cartographie/objets/acces/rel-ecrit-commande.yaml
elements: [APP-BOUTIQUE, OBJ-COMMANDE]
relationships: [REL-ECRIT-COMMANDE]
vues:
- id: VUE-COOPERATION
  nom: Coopération applicative
  noeuds:
  - {id: N1, element: APP-BOUTIQUE}
  - {id: N2, element: OBJ-COMMANDE}
  connexions:
  - {id: C1, relationship: REL-ECRIT-COMMANDE, de: N1, vers: N2}
- id: VUE-2
"""
GRAPHIC = """\
vues:
  VUE-COOPERATION:
    noeuds:
      N1: {x: 40, y: 60, l: 200, h: 80}
      N2: {relatif: N1, dx: 300, dy: 0}
"""


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    space = await make_api_space(client, app, tmp_path)
    forge_edit(
        space,
        {
            "cartographie/types/application.schema.yaml": (
                "title: Application\nx-archimate: ApplicationComponent\ntype: object\n"
            ),
            "cartographie/types/document-metier.schema.yaml": (
                "title: Document métier\nx-archimate: BusinessObject\ntype: object\n"
            ),
            "cartographie/types/acces.schema.yaml": (
                "title: Accès\nx-archimate: Access\ntype: object\n"
            ),
            "cartographie/objets/application/app-boutique.yaml": (
                "cle: APP-BOUTIQUE\ntype: application\nnom: Boutique en ligne\n"
            ),
            "cartographie/objets/document-metier/obj-commande.yaml": (
                "cle: OBJ-COMMANDE\ntype: document-metier\nnom: Commande\n"
            ),
            "cartographie/objets/document-metier/obj-facture.yaml": (
                "cle: OBJ-FACTURE\ntype: document-metier\nnom: Facture\n"
            ),
            "cartographie/objets/acces/rel-ecrit-commande.yaml": (
                "cle: REL-ECRIT-COMMANDE\ntype: acces\nsource: APP-BOUTIQUE\n"
                "cible: OBJ-COMMANDE\nnom: Écrit la commande\nproprietes:\n  acces: Write\n"
            ),
            "cartographie/regles/standard.rules.yaml": "lang: fr\n",
            "cartographie/regles/metier.rules.yaml": "lang: fr\n",
            "cartographie/modeles/boutique-applicatif.modele.yaml": MODEL,
            "cartographie/modeles/boutique-applicatif.graphique.yaml": GRAPHIC,
        },
    )
    return space


def _url(space: ApiSpace, rest: str = "") -> str:
    return f"{space.base}/cartographie/modeles{rest}"


async def _list(space: ApiSpace) -> dict[str, Any]:
    answer = await space.client.get(_url(space))
    assert answer.status_code == 200, answer.text
    data: dict[str, Any] = answer.json()
    return data


async def test_liste_des_modeles_en_vignettes(space: ApiSpace) -> None:
    data = await _list(space)
    assert data["rules"] == ["metier", "standard"]
    [model] = data["models"]
    assert model["slug"] == "boutique-applicatif" and model["id"] == "MOD-BOUTIQUE-APPLICATIF"
    assert model["name"] == "Boutique — vue applicative"
    assert [v["name"] for v in model["views"]] == ["Coopération applicative", ""]
    assert (model["elements"], model["relationships"], model["rules_name"]) == (2, 1, "standard")
    assert model["keys"] == ["APP-BOUTIQUE", "OBJ-COMMANDE", "REL-ECRIT-COMMANDE"]
    assert model["unused_documents"] == 0 and model["errors"] == 0
    assert model["dates"]["created_by"] and model["state"] is None
    # Aperçu de la première vue : positions absolues (relatif résolu), noms des objets.
    assert model["preview"]["boxes"] == [
        {"x": 40, "y": 60, "l": 200, "h": 80, "label": "Boutique en ligne", "kind": "element"},
        {"x": 340, "y": 60, "l": 120, "h": 55, "label": "Commande", "kind": "element"},
    ]
    assert model["preview"]["links"] == [{"x1": 140, "y1": 100, "x2": 400, "y2": 87.5}]


async def test_modele_a_nettoyer_signale(space: ApiSpace) -> None:
    forge_edit(
        space,
        {
            "cartographie/modeles/boutique-applicatif.modele.yaml": MODEL.replace(
                "- cartographie/objets/acces/",
                "- cartographie/objets/document-metier/obj-facture.yaml\n"
                "- cartographie/objets/acces/",
            )
        },
    )
    [model] = (await _list(space))["models"]
    assert model["unused_documents"] == 1 and model["errors"] == 1
    detail = (await space.client.get(_url(space, "/boutique-applicatif"))).json()
    assert [(p["code"], p["detail"]) for p in detail["problems"]] == [
        ("model_document_unused", "cartographie/objets/document-metier/obj-facture.yaml")
    ]
    # Il s'ouvre quand même, sans le document en trop (ticket f5421cc7).
    opened = await space.client.get(_url(space, "/boutique-applicatif/hydrate"))
    assert opened.status_code == 200, opened.text
    assert "OBJ-FACTURE" not in {d["data"]["cle"] for d in opened.json()["documents"]}


# --- Critère 1 : créer un modèle -------------------------------------------------------------


async def test_creer_un_modele(space: ApiSpace) -> None:
    answer = await space.client.post(
        _url(space),
        json={
            "label": "Vente en magasin",
            "slug": "vente-en-magasin",
            "description": "Le passage en caisse.",
            "rules": "nouvelles",
            "view": "Vue principale",
            "viewpoint": "business-process-cooperation",
        },
    )
    assert answer.status_code == 201, answer.text
    created = answer.json()
    assert created["revision"] and created["state"] == "created" and created["dates"] is None
    assert created["id"] == "MOD-VENTE-EN-MAGASIN"
    assert created["path"] == "cartographie/modeles/vente-en-magasin.modele.yaml"
    assert created["views"] == [
        {
            "id": "VUE-PRINCIPALE",
            "name": "Vue principale",
            "viewpoint": "business-process-cooperation",
        }
    ]
    data = await _list(space)
    assert [m["slug"] for m in data["models"]] == ["boutique-applicatif", "vente-en-magasin"]
    assert "nouvelles" in data["rules"]  # fichier de règles absent : créé vide


async def test_creer_un_modele_refus_nommes(space: ApiSpace) -> None:
    body = {"label": "Boutique", "slug": "boutique-applicatif"}
    taken = await space.client.post(_url(space), json=body)
    assert (taken.status_code, taken.json()["error"]) == (409, "carto_model_exists")
    nameless = await space.client.post(_url(space), json={"label": " ", "slug": "x"})
    assert nameless.json()["error"] == "carto_model_name_missing"
    bad = await space.client.post(_url(space), json={"label": "X", "slug": "Pas Bon"})
    assert bad.json()["error"] == "carto_model_slug_invalid"
    viewpoint = await space.client.post(
        _url(space), json={"label": "X", "slug": "x", "viewpoint": "inconnu"}
    )
    assert viewpoint.json()["error"] == "carto_model_invalid"
    codes = {p["code"] for p in viewpoint.json()["error_args"]["problems"]}
    assert codes == {"model_viewpoint_unknown"}


# --- Critères 3 et 4 : supprimer ---------------------------------------------------------


async def test_supprimer_un_modele_garde_les_objets(space: ApiSpace) -> None:
    objects = f"{space.base}/cartographie/objets"
    before = len((await space.client.get(objects)).json()["objects"])
    answer = await space.client.delete(_url(space, "/boutique-applicatif"))
    assert answer.status_code == 200, answer.text
    assert (await _list(space))["models"] == []
    assert len((await space.client.get(objects)).json()["objects"]) == before
    assert "standard" in (await _list(space))["rules"]  # le fichier de règles reste
    gone = await space.client.get(_url(space, "/boutique-applicatif"))
    assert (gone.status_code, gone.json()["error"]) == (404, "carto_model_not_found")


async def test_objet_retenu_par_un_modele(space: ApiSpace) -> None:
    objects = f"{space.base}/cartographie/objets"
    detail = (await space.client.get(f"{objects}/OBJ-COMMANDE")).json()
    assert detail["models"] == [
        {"slug": "boutique-applicatif", "name": "Boutique — vue applicative"}
    ]
    assert (await space.client.get(f"{objects}/OBJ-FACTURE")).json()["models"] == []
    refused = await space.client.delete(f"{objects}/REL-ECRIT-COMMANDE")
    assert (refused.status_code, refused.json()["error"]) == (409, "carto_object_in_model")
    assert refused.json()["error_args"]["models"] == "Boutique — vue applicative"
    assert refused.json()["error_args"]["slugs"] == ["boutique-applicatif"]
    assert (await space.client.delete(f"{objects}/OBJ-FACTURE")).status_code == 200


# --- Critère 5 et droits ---------------------------------------------------------------------


async def test_racine_dans_le_brouillon(app: FastAPI, space: ApiSpace) -> None:
    async with app.state.pool.acquire() as conn:
        root = await db_create_branch(
            conn, uuid.UUID(space.space_id), "main", "main", KIND_CHILD, git_ref="main"
        )
    url = f"/api/spaces/{space.space_id}/branches/{root.id}/cartographie/modeles"
    created = await space.client.post(url, json={"label": "Atelier", "slug": "atelier"})
    assert created.status_code == 201, created.text


async def test_droits(app: FastAPI, space: ApiSpace) -> None:
    await create_plain_user(app, "ana@example.org", PASSWORD)
    async with app.state.pool.acquire() as conn:
        user_id = await conn.fetchval("SELECT id FROM users WHERE email = 'ana@example.org'")
        reader = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) "
            "VALUES ('lecteur-carto', '{spaces:read,cartographie:read}') RETURNING id"
        )
        await conn.execute("UPDATE users SET profile_id = $2 WHERE id = $1", user_id, reader)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ana:
        await login(ana, "ana@example.org", PASSWORD)
        assert (await ana.get(_url(space))).status_code == 200
        assert (await ana.get(_url(space, "/boutique-applicatif"))).status_code == 200
        assert (await ana.post(_url(space), json={"label": "X", "slug": "x"})).status_code == 403
        assert (await ana.delete(_url(space, "/boutique-applicatif"))).status_code == 403


# --- Ouvrir un modèle (ticket f5421cc7) : hydratation, boîte à outils, pose ---------------


async def test_modele_hydrate_pour_le_composant(space: ApiSpace) -> None:
    answer = await space.client.get(_url(space, "/boutique-applicatif/hydrate"))
    assert answer.status_code == 200, answer.text
    hydrated = answer.json()
    validator = Draft202012Validator(contract_schema())
    assert [e.message for e in validator.iter_errors(hydrated)] == []
    assert [d["data"]["cle"] for d in hydrated["documents"]] == [
        "APP-BOUTIQUE",
        "OBJ-COMMANDE",
        "REL-ECRIT-COMMANDE",
    ]
    assert hydrated["graphique"]["data"]["vues"]["VUE-COOPERATION"]["noeuds"]["N1"]["x"] == 40
    assert hydrated["regles"]["data"]["defaut"]["name"] == "$.nom"
    # Critère 6 : rien n'est recopié, un objet modifié se voit à la prochaine ouverture.
    objects = f"{space.base}/cartographie/objets"
    renamed = await space.client.put(
        f"{objects}/APP-BOUTIQUE", json={"name": "Boutique web", "documentation": ""}
    )
    assert renamed.status_code == 200, renamed.text
    again = (await space.client.get(_url(space, "/boutique-applicatif/hydrate"))).json()
    assert again["documents"][0]["data"]["nom"] == "Boutique web"


async def test_modele_incoherent_refuse_a_l_ouverture(space: ApiSpace) -> None:
    forge_edit(
        space,
        {
            "cartographie/modeles/boutique-applicatif.modele.yaml": MODEL.replace(
                "elements: [APP-BOUTIQUE,", "elements: [APP-ABSENT, APP-BOUTIQUE,"
            )
        },
    )
    answer = await space.client.get(_url(space, "/boutique-applicatif/hydrate"))
    assert (answer.status_code, answer.json()["error"]) == (409, "carto_model_invalid")
    [problem] = answer.json()["error_args"]["problems"]
    assert (problem["code"], problem["detail"]) == ("model_key_without_document", "APP-ABSENT")


async def test_boite_a_outils_objets_existants_par_type(space: ApiSpace) -> None:
    answer = await space.client.get(_url(space, "/boutique-applicatif/outils"))
    assert answer.status_code == 200, answer.text
    groups = answer.json()["groupes"]
    assert [(g["id"], g["libelle"]) for g in groups] == [
        ("application", "Application"),
        ("document-metier", "Document métier"),
        ("relations", ""),
    ]
    assert [t["cle"] for t in groups[1]["outils"]] == ["OBJ-COMMANDE", "OBJ-FACTURE"]
    assert groups[1]["outils"][1]["libelle"] == "Facture"
    assert groups[1]["outils"][1]["icone"].startswith("<svg")  # pictogramme du type ArchiMate
    assert groups[2]["outils"] == [
        {
            "cle": "REL-ECRIT-COMMANDE",
            "libelle": "Écrit la commande (Boutique en ligne → Commande)",
            "icone": groups[2]["outils"][0]["icone"],
        }
    ]
    # Le point de vue de la vue filtre : « organisation » n'a ni application ni document.
    organization = await space.client.get(
        _url(space, "/boutique-applicatif/outils?point_de_vue=organization")
    )
    assert [g["id"] for g in organization.json()["groupes"]] == ["relations"]


async def test_delegue_de_pose(space: ApiSpace) -> None:
    answer = await space.client.get(_url(space, "/boutique-applicatif/pose/OBJ-FACTURE"))
    assert answer.status_code == 200, answer.text
    dropped = answer.json()
    assert dropped["objet"] == {
        "chemin": "cartographie/objets/document-metier/obj-facture.yaml",
        "data": {"cle": "OBJ-FACTURE", "type": "document-metier", "nom": "Facture"},
    }
    assert dropped["schemaType"]["data"]["x-archimate"] == "BusinessObject"
    assert dropped["regles"]["name"] == "$.nom"  # pas de règle propre : la règle par défaut
    missing = await space.client.get(_url(space, "/boutique-applicatif/pose/OBJ-ABSENT"))
    assert (missing.status_code, missing.json()["error"]) == (404, "carto_object_not_found")


async def test_relations_permises_avec_le_schema_du_type(space: ApiSpace) -> None:
    objects = f"{space.base}/cartographie/objets"
    answer = await space.client.get(f"{objects}/APP-BOUTIQUE/relations-permises?cible=OBJ-FACTURE")
    [access] = answer.json()["types"]
    assert access["slug"] == "acces"
    assert access["type_schema"] == {"title": "Accès", "x-archimate": "Access", "type": "object"}
