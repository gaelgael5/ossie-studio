"""Objets de la cartographie côté serveur (ticket 6f71bcba) : créer, lire,
modifier, supprimer à la main ; relations lues sur la fiche ; dates tirées de Git ;
refus nommés ; racine écrite dans le brouillon ; droits. Un objet est désigné par
son type et sa clé (ticket 421832ee) : la relation du jeu d'essai est écrite en clés
seules (forme d'avant), elle se lit comme avant."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from ossie_studio.spaces.branch_service import KIND_CHILD, db_create_branch
from tests.conftest import create_plain_user, login
from tests.space_fixture import ApiSpace, forge_edit, forge_read, make_api_space

PASSWORD = "motdepasse-solide"


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    space = await make_api_space(client, app, tmp_path)
    forge_edit(
        space,
        {
            "cartographie/types/application.schema.yaml": (
                "title: Application\nx-archimate: ApplicationComponent\ntype: object\n"
                "properties:\n  responsable:\n    type: string\n  criticite:\n    type: string\n"
                "    enum:\n    - haute\n    - basse\nrequired:\n- criticite\n"
            ),
            "cartographie/types/processus-de-vente.schema.yaml": (
                "title: Processus de vente\nx-archimate: BusinessProcess\ntype: object\n"
            ),
            "cartographie/types/document-metier.schema.yaml": (
                "title: Document métier\nx-archimate: BusinessObject\ntype: object\n"
            ),
            "cartographie/types/acces.schema.yaml": (
                "title: Accès\nx-archimate: Access\ntype: object\n"
            ),
            "cartographie/objets/processus-de-vente/prc-prise-commande.yaml": (
                "cle: PRC-PRISE-COMMANDE\ntype: processus-de-vente\nnom: Prise de commande\n"
                "documentation: |\n  Du panier **validé** à la commande.\n\n"
                "  - stock ;\n  - courriel.\n"
            ),
            "cartographie/objets/document-metier/obj-commande.yaml": (
                "cle: OBJ-COMMANDE\ntype: document-metier\nnom: Commande\n"
            ),
            "cartographie/objets/acces/rel-ecrit-commande.yaml": (
                "cle: REL-ECRIT-COMMANDE\ntype: acces\nsource: PRC-PRISE-COMMANDE\n"
                "cible: OBJ-COMMANDE\nnom: Écrit la commande\nproprietes:\n  acces: Write\n"
            ),
        },
    )
    return space


def _url(space: ApiSpace, rest: str = "") -> str:
    return f"{space.base}/cartographie/objets{rest}"


async def _objects(space: ApiSpace) -> list[dict[str, object]]:
    answer = await space.client.get(_url(space))
    assert answer.status_code == 200, answer.text
    objects: list[dict[str, object]] = answer.json()["objects"]
    return objects


async def _create(space: ApiSpace, **body: object) -> Response:
    return await space.client.post(_url(space), json=body)


# --- Critère 1 : créer APP-BOUTIQUE -------------------------------------------------------


async def test_creer_app_boutique(space: ApiSpace) -> None:
    answer = await _create(
        space,
        key="APP-BOUTIQUE",
        type="application",
        name="Boutique en ligne",
        documentation="Site de vente.\n",
        properties={"criticite": "haute", "responsable": ""},
    )
    assert answer.status_code == 201, answer.text
    data = answer.json()
    assert data["revision"] and data["state"] == "created" and data["dates"] is None
    assert data["path"] == "cartographie/objets/application/app-boutique.yaml"
    listing = {o["key"]: o for o in await _objects(space)}
    assert listing["APP-BOUTIQUE"]["state"] == "created"
    assert listing["APP-BOUTIQUE"]["properties"] == [{"name": "criticite", "value": "haute"}]
    # Rien n'atteint la branche avant la publication.
    assert forge_read(space, "cartographie/objets/application/app-boutique.yaml") is None


async def test_liste_relations_et_dates(space: ApiSpace) -> None:
    listing = {o["key"]: o for o in await _objects(space)}
    assert set(listing) == {"PRC-PRISE-COMMANDE", "OBJ-COMMANDE", "REL-ECRIT-COMMANDE"}
    prise = listing["PRC-PRISE-COMMANDE"]
    assert prise["type_title"] == "Processus de vente" and prise["archimate"] == "BusinessProcess"
    assert prise["relations"] == 1 and prise["is_relation"] is False
    assert prise["excerpt"] == "Du panier validé à la commande. stock ; courriel."
    assert prise["dates"]["created_at"] and prise["dates"]["updated_by"]  # type: ignore[index]
    assert listing["REL-ECRIT-COMMANDE"]["is_relation"] is True


# --- Critères 4 et 5 : refus nommés ---------------------------------------------------------


async def test_cle_prise_ou_mal_formee_et_propriete_requise(space: ApiSpace) -> None:
    taken = await _create(space, key="OBJ-COMMANDE", type="document-metier", name="X")
    assert taken.status_code == 409 and taken.json()["error"] == "carto_object_key_taken"
    malformed = await _create(space, key="app boutique", type="application", name="X",
                              properties={"criticite": "haute"})  # fmt: skip
    assert malformed.status_code == 400 and malformed.json()["error"] == "carto_object_invalid"
    codes = {p["code"] for p in malformed.json()["error_args"]["problems"]}
    assert codes == {"object_key_invalid"}
    missing = await _create(space, key="APP-X", type="application", name="X")
    assert missing.status_code == 400
    [problem] = missing.json()["error_args"]["problems"]
    assert (problem["code"], problem["field"]) == ("property_required", "criticite")


# --- Fiche : relations en lecture seule, schéma, modification --------------------------------


async def test_fiche_avec_relations(space: ApiSpace) -> None:
    answer = await space.client.get(_url(space, "/processus-de-vente/PRC-PRISE-COMMANDE"))
    assert answer.status_code == 200, answer.text
    data = answer.json()
    assert data["documentation"].startswith("Du panier **validé**")
    assert data["relations"]["incoming"] == []
    [out] = data["relations"]["outgoing"]
    assert (out["key"], out["archimate"], out["properties"]) == (
        "REL-ECRIT-COMMANDE",
        "Access",
        {"acces": "Write"},
    )
    assert out["other"] == {
        "key": "OBJ-COMMANDE",
        "ref": "document-metier/OBJ-COMMANDE",
        "name": "Commande",
        "type_title": "Document métier",
        "exists": True,
    }
    target = (await space.client.get(_url(space, "/document-metier/OBJ-COMMANDE"))).json()
    assert [r["key"] for r in target["relations"]["incoming"]] == ["REL-ECRIT-COMMANDE"]
    relation = (await space.client.get(_url(space, "/acces/REL-ECRIT-COMMANDE"))).json()
    acces = relation["schema"]["properties"]["acces"]
    assert acces["x-labels"]["Write"] == "écriture" and acces["x-unset"] == "non précisé"
    # Écrite en clés seules, la relation désigne ses extrémités par `type/CLÉ`.
    assert (relation["source"], relation["target"]) == (
        "processus-de-vente/PRC-PRISE-COMMANDE",
        "document-metier/OBJ-COMMANDE",
    )


async def test_modifier_garde_cle_type_et_extremites(space: ApiSpace) -> None:
    doc = "Mise à jour :\n\n- un ;\n- deux.\n"
    answer = await space.client.put(
        _url(space, "/acces/REL-ECRIT-COMMANDE"),
        json={"name": "Lit la commande", "documentation": doc, "properties": {"acces": "Read"}},
    )
    assert answer.status_code == 200, answer.text
    data = answer.json()
    assert (data["name"], data["documentation"], data["state"]) == (
        "Lit la commande",
        doc,
        "modified",
    )
    assert (data["source"], data["target"], data["type"]) == (
        "processus-de-vente/PRC-PRISE-COMMANDE",
        "document-metier/OBJ-COMMANDE",
        "acces",
    )
    reread = (await space.client.get(_url(space, "/acces/REL-ECRIT-COMMANDE"))).json()
    assert reread["documentation"] == doc
    # Réécrite à sa prochaine écriture : ses extrémités passent en `type/CLÉ`.
    assert "source: processus-de-vente/PRC-PRISE-COMMANDE\n" in reread["yaml"]
    assert "cible: document-metier/OBJ-COMMANDE\n" in reread["yaml"]
    invalid = await space.client.put(
        _url(space, "/acces/REL-ECRIT-COMMANDE"),
        json={"name": "X", "properties": {"acces": "écriture"}},
    )
    assert invalid.status_code == 400 and invalid.json()["error"] == "carto_object_invalid"
    missing = await space.client.put(_url(space, "/application/ABSENT"), json={"name": "X"})
    assert missing.status_code == 404 and missing.json()["error"] == "carto_object_not_found"


# --- Critère 9 : supprimer -------------------------------------------------------------------


async def test_supprimer(space: ApiSpace) -> None:
    refused = await space.client.delete(_url(space, "/processus-de-vente/PRC-PRISE-COMMANDE"))
    assert refused.status_code == 409 and refused.json()["error"] == "carto_object_in_use"
    assert refused.json()["error_args"]["relations"] == ["acces/REL-ECRIT-COMMANDE"]
    assert (await space.client.delete(_url(space, "/acces/REL-ECRIT-COMMANDE"))).status_code == 200
    deleted = await space.client.delete(_url(space, "/processus-de-vente/PRC-PRISE-COMMANDE"))
    assert deleted.status_code == 200
    assert {o["key"] for o in await _objects(space)} == {"OBJ-COMMANDE"}


# --- Critère 10 et droits ---------------------------------------------------------------------


async def test_racine_ecrite_dans_le_brouillon(app: FastAPI, space: ApiSpace) -> None:
    async with app.state.pool.acquire() as conn:
        root = await db_create_branch(
            conn, uuid.UUID(space.space_id), "main", "main", KIND_CHILD, git_ref="main"
        )
    base = f"/api/spaces/{space.space_id}/branches/{root.id}/cartographie"
    await space.client.post(
        f"{base}/types", json={"label": "Atelier", "slug": "atelier", "archimate": "Grouping"}
    )
    created = await space.client.post(
        f"{base}/objets", json={"key": "ATL-1", "type": "atelier", "name": "Atelier 1"}
    )
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
        assert (await ana.get(_url(space, "/document-metier/OBJ-COMMANDE"))).status_code == 200
        denied = await ana.post(_url(space), json={"key": "X", "type": "acces", "name": "X"})
        assert denied.status_code == 403
        refused = await ana.delete(_url(space, "/document-metier/OBJ-COMMANDE"))
        assert refused.status_code == 403


async def test_fiche_porte_le_yaml_stocke(space: ApiSpace) -> None:
    """Ticket 7dc220a4 : la fiche rend le fichier de l'objet tel qu'il est stocké."""
    created = await _create(
        space, key="APP-YAML", type="application", name="Yaml", properties={"criticite": "haute"}
    )
    assert created.status_code == 201, created.text
    detail = await space.client.get(f"{_url(space)}/application/APP-YAML")
    assert detail.status_code == 200, detail.text
    text = detail.json()["yaml"]
    assert "APP-YAML" in text and "nom: Yaml" in text


# --- Ticket 421832ee : une clé unique par type ------------------------------------------------


async def test_meme_cle_dans_deux_types(space: ApiSpace) -> None:
    """Deux types portent la même clé ; une relation désigne son extrémité par
    `type/CLÉ` ; une clé seule ambiguë est refusée ; la suppression vise le bon objet."""
    first = await _create(
        space, key="SMO", type="application", name="SMO", properties={"criticite": "haute"}
    )
    second = await _create(space, key="SMO", type="document-metier", name="Fiche SMO")
    assert (first.status_code, second.status_code) == (201, 201), second.text
    assert first.json()["ref"] == "application/SMO"
    both = sorted((o["type"], o["ref"]) for o in await _objects(space) if o["key"] == "SMO")
    assert both == [("application", "application/SMO"), ("document-metier", "document-metier/SMO")]
    relation = await _create(
        space,
        key="REL-LIT-SMO",
        type="acces",
        name="Lit la fiche",
        source="processus-de-vente/PRC-PRISE-COMMANDE",
        target="document-metier/SMO",
    )
    assert relation.status_code == 201, relation.text
    assert relation.json()["target"] == "document-metier/SMO"
    assert relation.json()["ends"]["target"]["name"] == "Fiche SMO"
    for target, code in (
        ("SMO", "relation_endpoint_ambiguous"),
        ("document-metier/INCONNU", "relation_endpoint_unknown"),
    ):
        refused = await _create(
            space, key="REL-X", type="acces", name="X",
            source="processus-de-vente/PRC-PRISE-COMMANDE", target=target,
        )  # fmt: skip
        assert refused.status_code == 400, refused.text
        [problem] = refused.json()["error_args"]["problems"]
        assert (problem["code"], problem["field"], problem["detail"]) == (code, "cible", target)
    in_use = await space.client.delete(_url(space, "/document-metier/SMO"))
    assert in_use.status_code == 409 and in_use.json()["error"] == "carto_object_in_use"
    assert (await space.client.delete(_url(space, "/application/SMO"))).status_code == 200
    assert [o["ref"] for o in await _objects(space) if o["key"] == "SMO"] == ["document-metier/SMO"]
