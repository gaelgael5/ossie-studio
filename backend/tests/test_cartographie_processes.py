"""Onglet « Processus » côté serveur (epic « Cartographie — processus BPMN », ticket
7e061c3e) : vérifier et importer un fichier BPMN (gardé tel quel), créer, rattacher,
supprimer ; la fiche d'un processus métier connaît son détail ; droits."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.cartographie.process import new_process_xml
from ossie_studio.spaces.branch_service import KIND_CHILD, db_create_branch
from tests.conftest import create_plain_user, login
from tests.space_fixture import ApiSpace, forge_edit, make_api_space

PASSWORD = "motdepasse-solide"
FIXTURES = Path(__file__).parent / "fixtures" / "bpmn"
RETOURS = (FIXTURES / "bpmn-io-retours.bpmn").read_text(encoding="utf-8")


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    space = await make_api_space(client, app, tmp_path)
    forge_edit(
        space,
        {
            "cartographie/types/processus-metier.schema.yaml": (
                "title: Processus métier\nx-archimate: BusinessProcess\ntype: object\n"
            ),
            "cartographie/types/role.schema.yaml": (
                "title: Rôle\nx-archimate: BusinessRole\ntype: object\n"
            ),
            "cartographie/objets/processus-metier/prc-prise-commande.yaml": (
                "cle: PRC-PRISE-COMMANDE\ntype: processus-metier\nnom: Prise de commande\n"
            ),
            "cartographie/objets/processus-metier/prc-retours.yaml": (
                "cle: PRC-RETOURS-METIER\ntype: processus-metier\nnom: Retours\n"
            ),
            "cartographie/objets/role/rol-vendeur.yaml": (
                "cle: ROL-VENDEUR\ntype: role\nnom: Vendeur\n"
            ),
        },
    )
    return space


def _url(space: ApiSpace, rest: str = "") -> str:
    return f"{space.base}/cartographie/processus{rest}"


async def _list(space: ApiSpace) -> list[dict[str, Any]]:
    answer = await space.client.get(_url(space))
    assert answer.status_code == 200, answer.text
    processes: list[dict[str, Any]] = answer.json()["processes"]
    return processes


# --- Critères 1 et 2 : importer un fichier BPMN ---------------------------------------------


async def test_verifier_un_fichier_avant_import(space: ApiSpace) -> None:
    answer = await space.client.post(
        _url(space, "/verifier"), json={"content": RETOURS, "filename": "traitement-retours.bpmn"}
    )
    assert answer.status_code == 200, answer.text
    checked = answer.json()
    assert checked["valid"] and checked["has_diagram"]
    assert (checked["participants"], checked["lanes"], checked["elements"], checked["flows"]) == (
        1,
        2,
        6,
        6,
    )
    assert checked["attached"] == 0  # avertissement à l'écran
    assert checked["name"] == "Traitement des retours"
    assert checked["suggested_key"] == "PRC-TRAITEMENT-DES-RETOURS"
    kinds = [b["kind"] for b in checked["preview"]["boxes"]]
    assert kinds == ["pool", "lane", "lane", "event", "task", "gateway", "task", "task", "event"]
    assert checked["preview"]["links"][3]["points"] == [[460, 165], [460, 290], [560, 290]]
    broken = await space.client.post(_url(space, "/verifier"), json={"content": "<bpmn:defin"})
    assert broken.json()["valid"] is False
    assert broken.json()["error"]["code"] == "process_unreadable"


async def test_importer_un_fichier_garde_tel_quel(space: ApiSpace) -> None:
    """Critère 1 : rangé sous cartographie/processus/<clé>.bpmn, gardé tel quel, commit
    local ; seuls l'id du document et le nom (changé) sont retouchés."""
    answer = await space.client.post(
        _url(space, "/importer"),
        json={"key": "PRC-RETOURS", "name": "Retours clients", "content": RETOURS},
    )
    assert answer.status_code == 201, answer.text
    imported = answer.json()
    assert imported["revision"] and imported["state"] == "created"
    assert imported["path"] == "cartographie/processus/prc-retours.bpmn"
    assert (imported["name"], imported["detail_of"]) == ("Retours clients", None)
    assert (imported["participants"], imported["lanes"], imported["attached"]) == (1, 2, 0)
    assert imported["pools"] == ["Service après-vente"]
    # Le fichier écrit : l'original, à deux attributs près.
    hydrated = await space.client.get(_url(space, "/PRC-RETOURS/contenu"))
    assert hydrated.status_code == 200, hydrated.text
    expected = RETOURS.replace('id="Definitions_1x2y3z"', 'id="PRC-RETOURS"').replace(
        'name="Traitement des retours"', 'name="Retours clients"'
    )
    assert hydrated.json()["content"] == expected


async def test_importer_un_fichier_invalide_ou_sans_dessin(space: ApiSpace) -> None:
    """Critère 2 : refusé avec le message ; rien n'est écrit."""
    broken = await space.client.post(
        _url(space, "/importer"), json={"key": "PRC-X", "name": "X", "content": "pas du bpmn"}
    )
    assert (broken.status_code, broken.json()["error"]) == (400, "carto_process_unreadable")
    no_diagram = RETOURS.split("<bpmndi:BPMNDiagram")[0] + "</bpmn:definitions>\n"
    refused = await space.client.post(
        _url(space, "/importer"), json={"key": "PRC-X", "name": "X", "content": no_diagram}
    )
    assert refused.json()["error"] == "carto_process_invalid"
    assert [p["code"] for p in refused.json()["error_args"]["problems"]] == ["process_no_diagram"]
    assert await _list(space) == []


# --- Critère 3 : créer un processus rattaché ---------------------------------------------


async def test_creer_un_processus_detail_d_un_processus_metier(space: ApiSpace) -> None:
    answer = await space.client.post(
        _url(space),
        json={
            "key": "PRC-PRISE-COMMANDE-DETAIL",
            "name": "Prise de commande — détail",
            "detail_of": "PRC-PRISE-COMMANDE",
        },
    )
    assert answer.status_code == 201, answer.text
    assert answer.json()["detail_of"] == {
        "key": "PRC-PRISE-COMMANDE",
        "ref": "processus-metier/PRC-PRISE-COMMANDE",
        "name": "Prise de commande",
        "exists": True,
    }
    [listed] = await _list(space)
    assert listed["name"] == "Prise de commande — détail" and listed["errors"] == 0
    # La fiche du processus métier propose « Ouvrir le détail BPMN ».
    detail = await space.client.get(
        f"{space.base}/cartographie/objets/processus-metier/PRC-PRISE-COMMANDE"
    )
    assert detail.json()["processes"] == ["PRC-PRISE-COMMANDE-DETAIL"]
    other = await space.client.get(
        f"{space.base}/cartographie/objets/processus-metier/PRC-RETOURS-METIER"
    )
    assert other.json()["processes"] == []


async def test_creer_refus_nommes(space: ApiSpace) -> None:
    nameless = await space.client.post(_url(space), json={"key": "PRC-A", "name": " "})
    assert nameless.json()["error"] == "carto_process_name_missing"
    bad = await space.client.post(_url(space), json={"key": "prc a", "name": "A"})
    assert bad.json()["error"] == "carto_process_key_invalid"
    wrong = await space.client.post(
        _url(space), json={"key": "PRC-A", "name": "A", "detail_of": "ROL-VENDEUR"}
    )
    assert wrong.json()["error"] == "carto_process_invalid"
    codes = {p["code"] for p in wrong.json()["error_args"]["problems"]}
    assert codes == {"process_detail_not_business_process"}
    await space.client.post(_url(space), json={"key": "PRC-A", "name": "A"})
    taken = await space.client.post(_url(space), json={"key": "PRC-A", "name": "A"})
    assert (taken.status_code, taken.json()["error"]) == (409, "carto_process_exists")


# --- Rattacher, supprimer -------------------------------------------------------------------


async def test_rattacher_un_processus_importe(space: ApiSpace) -> None:
    await space.client.post(
        _url(space, "/importer"), json={"key": "PRC-RETOURS", "name": "", "content": RETOURS}
    )
    answer = await space.client.post(
        _url(space, "/PRC-RETOURS/rattacher"), json={"detail_of": "PRC-RETOURS-METIER"}
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["detail_of"]["name"] == "Retours"
    assert answer.json()["name"] == "Traitement des retours"  # nom du fichier gardé
    detached = await space.client.post(_url(space, "/PRC-RETOURS/rattacher"), json={})
    assert detached.json()["detail_of"] is None
    content = (await space.client.get(_url(space, "/PRC-RETOURS/contenu"))).json()["content"]
    assert content == RETOURS.replace('id="Definitions_1x2y3z"', 'id="PRC-RETOURS"')


async def test_supprimer_un_processus_garde_les_objets(space: ApiSpace) -> None:
    await space.client.post(
        _url(space), json={"key": "PRC-A", "name": "A", "detail_of": "PRC-PRISE-COMMANDE"}
    )
    answer = await space.client.delete(_url(space, "/PRC-A"))
    assert answer.status_code == 200, answer.text
    assert await _list(space) == []
    objects = (await space.client.get(f"{space.base}/cartographie/objets")).json()["objects"]
    assert "PRC-PRISE-COMMANDE" in {o["key"] for o in objects}
    gone = await space.client.get(_url(space, "/PRC-A"))
    assert (gone.status_code, gone.json()["error"]) == (404, "carto_process_not_found")


# --- Critère 5 et droits ---------------------------------------------------------------------


async def test_racine_dans_le_brouillon(app: FastAPI, space: ApiSpace) -> None:
    async with app.state.pool.acquire() as conn:
        root = await db_create_branch(
            conn, uuid.UUID(space.space_id), "main", "main", KIND_CHILD, git_ref="main"
        )
    url = f"/api/spaces/{space.space_id}/branches/{root.id}/cartographie/processus"
    created = await space.client.post(url, json={"key": "PRC-A", "name": "A"})
    assert created.status_code == 201, created.text


async def test_droits(app: FastAPI, space: ApiSpace) -> None:
    # Publié (un brouillon n'est vu que de son auteur).
    forge_edit(space, {"cartographie/processus/prc-a.bpmn": new_process_xml("PRC-A", "A")})
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
        assert (await ana.get(_url(space, "/PRC-A"))).status_code == 200
        assert (await ana.post(_url(space), json={"key": "PRC-B", "name": "B"})).status_code == 403
        importing = await ana.post(_url(space, "/importer"), json={"key": "PRC-B", "content": ""})
        assert importing.status_code == 403
        assert (await ana.delete(_url(space, "/PRC-A"))).status_code == 403
