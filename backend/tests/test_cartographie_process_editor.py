"""Ouvrir et enregistrer un processus dans l'éditeur urban-bpmn (epic « Cartographie —
processus BPMN », ticket 48335b93) : boîte à outils (« Liés au processus » d'abord),
ce que devient un objet posé (genre, membres d'une collaboration, avertissement de la
relation qui manque), objets rattachables, enregistrement contrôlé, droits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.cartographie.process import new_process_xml, with_detail
from ossie_studio.cartographie.process_tools import (
    attach_choices,
    members,
    process_drop,
    process_toolbox,
)
from ossie_studio.cartographie.space import Cartographie, read_cartographie
from tests.conftest import create_plain_user, login
from tests.space_fixture import ApiSpace, forge_edit, make_api_space

PASSWORD = "motdepasse-solide"
FIXTURES = Path(__file__).parent / "fixtures" / "bpmn"
RETOURS = (FIXTURES / "bpmn-io-retours.bpmn").read_text(encoding="utf-8")


def _type(slug: str, title: str, archimate: str) -> tuple[str, str]:
    return (
        f"cartographie/types/{slug}.schema.yaml",
        f"title: {title}\nx-archimate: {archimate}\ntype: object\n",
    )


def _object(
    type_slug: str, key: str, name: str, source: str = "", target: str = ""
) -> tuple[str, str]:
    text = f"cle: {key}\ntype: {type_slug}\nnom: {name}\n"
    if source:
        text += f"source: {source}\ncible: {target}\n"
    return f"cartographie/objets/{type_slug}/{key.lower()}.yaml", text


# L'espace des critères : Vendeur affecté à Prise de commande, Client non ; la
# collaboration Vente en ligne agrège Client et Boutique ; Prise de commande accède à
# Commande ; l'application Boutique en ligne la sert.
FILES: dict[str, str] = dict(
    [
        _type("processus-metier", "Processus métier", "BusinessProcess"),
        _type("acteur", "Acteur", "BusinessActor"),
        _type("role", "Rôle", "BusinessRole"),
        _type("collaboration", "Collaboration", "BusinessCollaboration"),
        _type("objet-metier", "Objet métier", "BusinessObject"),
        _type("application", "Application", "ApplicationComponent"),
        _type("evenement", "Événement", "BusinessEvent"),
        _type("affectation", "Affectation", "Assignment"),
        _type("acces", "Accès", "Access"),
        _type("service", "Service", "Serving"),
        _type("agregation", "Agrégation", "Aggregation"),
        _object("processus-metier", "PRC-PRISE-COMMANDE", "Prise de commande"),
        _object("role", "ROL-VENDEUR", "Vendeur"),
        _object("role", "ROL-LIVREUR", "Livreur"),
        _object("acteur", "ACT-CLIENT", "Client"),
        _object("acteur", "ORG-BOUTIQUE", "Boutique"),
        _object("collaboration", "COL-VENTE-EN-LIGNE", "Vente en ligne"),
        _object("collaboration", "COL-SAV", "Service après-vente"),
        _object("objet-metier", "OBJ-COMMANDE", "Commande"),
        _object("objet-metier", "OBJ-FACTURE", "Facture"),
        _object("application", "APP-BOUTIQUE", "Boutique en ligne"),
        _object("evenement", "EVT-COMMANDE", "Commande reçue"),
        _object("affectation", "REL-VENDEUR-PRISE", "Prend", "ROL-VENDEUR", "PRC-PRISE-COMMANDE"),
        _object("acces", "REL-PRISE-COMMANDE", "Crée", "PRC-PRISE-COMMANDE", "OBJ-COMMANDE"),
        _object("service", "REL-APP-PRISE", "Sert", "APP-BOUTIQUE", "PRC-PRISE-COMMANDE"),
        _object("agregation", "REL-COL-CLIENT", "Regroupe", "COL-VENTE-EN-LIGNE", "ACT-CLIENT"),
        _object("agregation", "REL-COL-BOUTIQUE", "Regroupe", "COL-VENTE-EN-LIGNE", "ORG-BOUTIQUE"),
    ]
)


@pytest.fixture
def carto(tmp_path: Path) -> Cartographie:
    for path, text in FILES.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
    space = read_cartographie(tmp_path)
    assert not space.problems, space.problems
    return space


def _keys(group: dict[str, Any]) -> list[str]:
    return [tool["cle"] for tool in group["outils"]]


# --- Boîte à outils ------------------------------------------------------------------------


def test_boite_a_outils_lies_au_processus_d_abord(carto: Cartographie) -> None:
    linked, others = process_toolbox(carto, "PRC-PRISE-COMMANDE")
    assert linked["id"] == "lies" and others["id"] == "autres"
    # Affectés, accédés, le servant : rôles, objets métier, applications, dans cet ordre.
    assert _keys(linked) == ["ROL-VENDEUR", "OBJ-COMMANDE", "APP-BOUTIQUE"]
    assert _keys(others) == [
        "ORG-BOUTIQUE", "ACT-CLIENT", "ROL-LIVREUR", "COL-SAV", "COL-VENTE-EN-LIGNE", "OBJ-FACTURE",
    ]  # fmt: skip
    vendeur = linked["outils"][0]
    assert vendeur["libelle"] == "Vendeur" and vendeur["description"] == "ROL-VENDEUR · Rôle"
    assert vendeur["icone"].startswith("<svg")
    # Ni processus métier, ni événement, ni relation : seuls les genres que l'on pose.
    every = _keys(linked) + _keys(others)
    assert "PRC-PRISE-COMMANDE" not in every and "EVT-COMMANDE" not in every


def test_boite_a_outils_sans_processus_metier_montre_tout_l_espace(carto: Cartographie) -> None:
    [group] = process_toolbox(carto, None)
    assert group["id"] == "objets" and len(group["outils"]) == 9
    # Un processus métier disparu : tout l'espace aussi.
    [same] = process_toolbox(carto, "PRC-DISPARU")
    assert same == group


# --- Pose ----------------------------------------------------------------------------------


def test_poser_un_role_affecte_cree_un_couloir_sans_avertissement(carto: Cartographie) -> None:
    """Critère 1."""
    assert process_drop(carto, "ROL-VENDEUR", "PRC-PRISE-COMMANDE") == {
        "nom": "Vendeur", "type": "role", "genre": "couloir",
    }  # fmt: skip


def test_poser_un_acteur_non_affecte_avertit(carto: Cartographie) -> None:
    """Critère 2 : une piscine, et la relation qui manque (Affectation vers le processus)."""
    dropped = process_drop(carto, "ACT-CLIENT", "PRC-PRISE-COMMANDE")
    assert dropped["genre"] == "participant" and dropped["nom"] == "Client"
    assert dropped["avertissement"] == {
        "code": "process_drop_not_linked",
        "relation": "Assignment",
        "source": "ACT-CLIENT",
        "source_name": "Client",
        "target": "PRC-PRISE-COMMANDE",
        "target_name": "Prise de commande",
    }
    # Membre de la collaboration rattachée au dessin : relié, pas d'avertissement.
    member = process_drop(carto, "ACT-CLIENT", "PRC-PRISE-COMMANDE", "COL-VENTE-EN-LIGNE")
    assert "avertissement" not in member
    # Sans processus métier mais avec une collaboration : il manque l'agrégation.
    outside = process_drop(carto, "ACT-CLIENT", None, "COL-SAV")
    assert outside["avertissement"]["relation"] == "Aggregation"
    assert (outside["avertissement"]["source"], outside["avertissement"]["target"]) == (
        "COL-SAV", "ACT-CLIENT",
    )  # fmt: skip
    # Ni processus métier ni collaboration : rien à comparer.
    assert "avertissement" not in process_drop(carto, "ACT-CLIENT", None)


def test_poser_une_collaboration_rend_ses_membres(carto: Cartographie) -> None:
    """Critère 3 : membres acteurs → participants (rôles → couloirs)."""
    dropped = process_drop(carto, "COL-VENTE-EN-LIGNE", "PRC-PRISE-COMMANDE")
    assert dropped["genre"] == "collaboration"
    assert dropped["membres"] == [
        {"cle": "ORG-BOUTIQUE", "nom": "Boutique", "type": "acteur", "genre": "participant"},
        {"cle": "ACT-CLIENT", "nom": "Client", "type": "acteur", "genre": "participant"},
    ]
    # Non affectée au processus métier : avertissement d'Affectation.
    assert dropped["avertissement"]["relation"] == "Assignment"
    assert members(carto, "COL-SAV") == []


def test_poser_objet_metier_et_application(carto: Cartographie) -> None:
    """Critère 5 : objet de données ; une application devient un stockage de données."""
    commande = process_drop(carto, "OBJ-COMMANDE", "PRC-PRISE-COMMANDE")
    assert commande == {"nom": "Commande", "type": "objet-metier", "genre": "objet-donnees"}
    facture = process_drop(carto, "OBJ-FACTURE", "PRC-PRISE-COMMANDE")
    warning = facture["avertissement"]
    # Le processus accède à l'objet : c'est lui la source.
    assert (warning["relation"], warning["source"], warning["target"]) == (
        "Access", "PRC-PRISE-COMMANDE", "OBJ-FACTURE",
    )  # fmt: skip
    app = process_drop(carto, "APP-BOUTIQUE", "PRC-PRISE-COMMANDE")
    assert app == {"nom": "Boutique en ligne", "type": "application", "genre": "stockage-donnees"}


def test_poser_un_autre_type_ou_un_objet_absent_est_refuse(carto: Cartographie) -> None:
    from ossie_studio.cartographie.errors import CartographieError

    with pytest.raises(CartographieError) as unsupported:
        process_drop(carto, "EVT-COMMANDE", "PRC-PRISE-COMMANDE")
    assert unsupported.value.code == "process_drop_unsupported"
    assert unsupported.value.detail["archimate"] == "BusinessEvent"
    with pytest.raises(CartographieError) as absent:
        process_drop(carto, "ACT-ABSENT", "PRC-PRISE-COMMANDE")
    assert (absent.value.code, absent.value.status) == ("object_not_found", 404)


def test_objets_rattachables_par_element(carto: Cartographie) -> None:
    lanes = attach_choices(carto, "bpmn:Lane")
    assert [o["cle"] for o in lanes] == ["ROL-LIVREUR", "ROL-VENDEUR"]
    assert lanes[1] == {"cle": "ROL-VENDEUR", "nom": "Vendeur", "type": "role"}
    participants = attach_choices(carto, "bpmn:Participant")
    assert [o["cle"] for o in participants] == ["ORG-BOUTIQUE", "ACT-CLIENT"]
    assert [o["cle"] for o in attach_choices(carto, "bpmn:Process")] == ["PRC-PRISE-COMMANDE"]
    assert attach_choices(carto, "bpmn:Task") == []


# --- Par l'API -----------------------------------------------------------------------------


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    space = await make_api_space(client, app, tmp_path)
    forge_edit(space, FILES)
    return space


def _url(space: ApiSpace, rest: str = "") -> str:
    return f"{space.base}/cartographie/processus{rest}"


async def _create(space: ApiSpace, key: str, detail: str | None) -> None:
    body = {"key": key, "name": "Détail", "detail_of": detail}
    answer = await space.client.post(_url(space), json=body)
    assert answer.status_code == 201, answer.text


async def test_api_outils_et_pose(space: ApiSpace) -> None:
    await _create(space, "PRC-VENTE", "PRC-PRISE-COMMANDE")
    tools = (await space.client.get(_url(space, "/PRC-VENTE/outils"))).json()
    assert tools["detail_of"] == "PRC-PRISE-COMMANDE"
    assert [g["id"] for g in tools["groupes"]] == ["lies", "autres"]
    dropped = (await space.client.get(_url(space, "/PRC-VENTE/pose/ACT-CLIENT"))).json()
    assert dropped["genre"] == "participant"
    assert dropped["avertissement"]["relation"] == "Assignment"
    # La collaboration que le dessin désigne en ce moment compte.
    member = await space.client.get(
        _url(space, "/PRC-VENTE/pose/ACT-CLIENT"), params={"collaboration": "COL-VENTE-EN-LIGNE"}
    )
    assert "avertissement" not in member.json()
    refused = await space.client.get(_url(space, "/PRC-VENTE/pose/EVT-COMMANDE"))
    assert refused.json()["error"] == "carto_process_drop_unsupported"
    absent = await space.client.get(_url(space, "/PRC-ABSENT/outils"))
    assert (absent.status_code, absent.json()["error"]) == (404, "carto_process_not_found")


async def test_api_processus_non_rattache_et_rattachables(space: ApiSpace) -> None:
    """Critère 6 : sans processus métier, la boîte montre tout l'espace."""
    await space.client.post(
        _url(space, "/importer"), json={"key": "PRC-RETOURS", "name": "", "content": RETOURS}
    )
    tools = (await space.client.get(_url(space, "/PRC-RETOURS/outils"))).json()
    assert tools["detail_of"] is None and [g["id"] for g in tools["groupes"]] == ["objets"]
    lanes = await space.client.get(
        _url(space, "/PRC-RETOURS/rattachables"), params={"element": "bpmn:Lane"}
    )
    assert [o["cle"] for o in lanes.json()["objets"]] == ["ROL-LIVREUR", "ROL-VENDEUR"]


async def test_api_enregistrer_le_xml_du_composant(space: ApiSpace) -> None:
    await _create(space, "PRC-VENTE", None)
    xml = new_process_xml("PRC-VENTE", "Vente")
    xml = with_detail(xml, "PRC-PRISE-COMMANDE", "processus-metier")
    saved = await space.client.put(_url(space, "/PRC-VENTE"), json={"content": xml})
    assert saved.status_code == 200, saved.text
    assert saved.json()["detail_of"]["key"] == "PRC-PRISE-COMMANDE"
    content = (await space.client.get(_url(space, "/PRC-VENTE/contenu"))).json()
    assert content["content"] == xml
    assert content["references"] == [
        {"cle": "PRC-PRISE-COMMANDE", "nom": "Prise de commande", "type": "processus-metier"}
    ]


async def test_api_enregistrer_refuse_une_reference_inconnue(space: ApiSpace) -> None:
    await _create(space, "PRC-VENTE", None)
    before = (await space.client.get(_url(space, "/PRC-VENTE/contenu"))).json()["content"]
    xml = with_detail(new_process_xml("PRC-VENTE", "Vente"), "PRC-ABSENT", "processus-metier")
    refused = await space.client.put(_url(space, "/PRC-VENTE"), json={"content": xml})
    assert refused.json()["error"] == "carto_process_invalid"
    assert {p["code"] for p in refused.json()["error_args"]["problems"]} == {
        "process_reference_unknown"
    }
    unreadable = await space.client.put(_url(space, "/PRC-VENTE"), json={"content": "<pas du bpmn"})
    assert unreadable.json()["error"] == "carto_process_unreadable"
    # Rien n'est écrit.
    after = (await space.client.get(_url(space, "/PRC-VENTE/contenu"))).json()["content"]
    assert after == before
    absent = await space.client.put(_url(space, "/PRC-ABSENT"), json={"content": xml})
    assert absent.json()["error"] == "carto_process_not_found"


async def test_api_droits(app: FastAPI, space: ApiSpace) -> None:
    """Critère 8 (règle des droits, comme les modèles) : sans « Cartographie :
    modifier », lecture (boîte à outils, pose) mais pas d'enregistrement."""
    forge_edit(space, {"cartographie/processus/prc-lu.bpmn": new_process_xml("PRC-LU", "Lu")})
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
        assert (await ana.get(_url(space, "/PRC-LU/outils"))).status_code == 200
        assert (await ana.get(_url(space, "/PRC-LU/pose/ROL-VENDEUR"))).status_code == 200
        refused = await ana.put(
            _url(space, "/PRC-LU"), json={"content": new_process_xml("PRC-LU", "Lu")}
        )
        assert refused.status_code == 403


async def test_api_enregistrer_le_xml_reel_du_composant(space: ApiSpace) -> None:
    """Le XML rendu par urban-bpmn 0.1.1 après deux poses (un couloir, un objet de
    données) s'enregistre : références contrôlées, rattachements retrouvés."""
    saved = (FIXTURES / "urban-bpmn-enregistre.bpmn").read_text(encoding="utf-8")
    await _create(space, "PRC-PRISE-COMMANDE-DETAIL", "PRC-PRISE-COMMANDE")
    answer = await space.client.put(
        _url(space, "/PRC-PRISE-COMMANDE-DETAIL"), json={"content": saved}
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["attached"] == 5 and answer.json()["errors"] == 0
    content = (await space.client.get(_url(space, "/PRC-PRISE-COMMANDE-DETAIL/contenu"))).json()
    assert content["content"] == saved
