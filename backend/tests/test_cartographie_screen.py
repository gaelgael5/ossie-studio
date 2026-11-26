"""Écran « Cartographie » côté serveur (ticket e7521e80) : catalogue ArchiMate,
liste des types de l'espace, aperçu et ajout d'un type, refus nommés, racine
écrite dans le brouillon, droits et journal d'audit."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.spaces.branch_service import KIND_CHILD, db_create_branch
from tests.conftest import create_plain_user, login
from tests.space_fixture import ApiSpace, forge_edit, forge_read, make_api_space

PASSWORD = "motdepasse-solide"


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    return await make_api_space(client, app, tmp_path)


def _url(space: ApiSpace, rest: str = "") -> str:
    return f"{space.base}/cartographie{rest}"


async def _types(space: ApiSpace) -> list[dict[str, object]]:
    answer = await space.client.get(_url(space, "/types"))
    assert answer.status_code == 200, answer.text
    types: list[dict[str, object]] = answer.json()["types"]
    return types


async def _add(space: ApiSpace, label: str, slug: str, archimate: str) -> dict[str, object]:
    answer = await space.client.post(
        _url(space, "/types"), json={"label": label, "slug": slug, "archimate": archimate}
    )
    assert answer.status_code == 201, answer.text
    created: dict[str, object] = answer.json()
    return created


def _publish(space: ApiSpace) -> None:
    forge_edit(
        space,
        {
            "cartographie/types/client.schema.yaml": (
                "title: Client\nx-archimate: BusinessActor\ntype: object\n"
            ),
            "cartographie/objets/client/act-dupont.yaml": (
                "cle: ACT-DUPONT\ntype: client\nnom: Dupont\n"
            ),
        },
    )


# --- Le catalogue de la fenêtre « Ajouter un type » ----------------------------------------


async def test_catalogue(space: ApiSpace) -> None:
    answer = await space.client.get(_url(space, "/catalogue"))
    assert answer.status_code == 200, answer.text
    data = answer.json()
    assert data["archimate"] == "3.2"
    assert [layer["key"] for layer in data["layers"]][:3] == ["strategy", "business", "application"]
    assert [(b["name"], b["required"]) for b in data["base"]] == [
        ("cle", True),
        ("nom", True),
        ("documentation", False),
    ]
    types = {t["name"]: t for t in data["types"]}
    assert len(types) == 72
    interaction = types["BusinessInteraction"]
    assert interaction["label"] == "Interaction métier" and interaction["layer"] == "business"
    assert interaction["definition"] and interaction["icon"].startswith("<svg")
    assert interaction["attributes"] == []
    assert "Assignment" in interaction["relations"]
    # Critère 4 : Accès porte la propriété `acces` en plus des propriétés de base.
    [acces] = types["Access"]["attributes"]
    assert acces["name"] == "acces" and acces["unset"] == "non précisé"
    assert [v["value"] for v in acces["values"]] == ["Read", "Write", "ReadWrite"]
    assert types["Access"]["relations"] == ["Composition", "Aggregation", "Association"]


# --- Critères 1, 2, 5 et 6 : ajouter un type --------------------------------------------------


async def test_ajouter_atelier_sur_interaction_metier(space: ApiSpace) -> None:
    assert await _types(space) == []
    created = await _add(space, "Atelier", "atelier", "BusinessInteraction")
    assert created["revision"], "écrit dans le brouillon"
    assert created["type"] == {
        "slug": "atelier",
        "title": "Atelier",
        "archimate": "BusinessInteraction",
        "path": "cartographie/types/atelier.schema.yaml",
        "properties": 0,
        "objects": 0,
        "state": "created",
    }
    assert await _types(space) == [created["type"]]
    # Rien n'atteint la branche avant la publication.
    assert forge_read(space, "cartographie/types/atelier.schema.yaml") is None


async def test_apercu_du_fichier_cree(space: ApiSpace) -> None:
    answer = await space.client.post(
        _url(space, "/types/apercu"),
        json={"label": "Composant applicatif", "slug": "composant-applicatif",
              "archimate": "ApplicationComponent"},
    )  # fmt: skip
    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "path": "cartographie/types/composant-applicatif.schema.yaml",
        "yaml": "title: Composant applicatif\nx-archimate: ApplicationComponent\ntype: object\n",
        "problems": [],
    }
    await _add(space, "Composant applicatif", "composant-applicatif", "ApplicationComponent")


async def test_deux_types_sur_acteur_metier_et_slug_deja_pris(space: ApiSpace) -> None:
    await _add(space, "Client", "client", "BusinessActor")
    await _add(space, "Service interne", "service-interne", "BusinessActor")
    listing = await _types(space)
    assert [(t["title"], t["archimate"]) for t in listing] == [
        ("Client", "BusinessActor"),
        ("Service interne", "BusinessActor"),
    ]
    taken = await space.client.post(
        _url(space, "/types"),
        json={"label": "Client bis", "slug": "client", "archimate": "BusinessRole"},
    )
    assert taken.status_code == 409
    assert taken.json()["error"] == "carto_type_exists"
    assert taken.json()["error_args"]["slug"] == "client"


async def test_demande_invalide(space: ApiSpace) -> None:
    answer = await space.client.post(
        _url(space, "/types"), json={"label": " ", "slug": "Pas Bon", "archimate": "Inconnu"}
    )
    assert answer.status_code == 400
    assert answer.json()["error_args"]["fields"] == [
        "label_required",
        "slug_invalid",
        "archimate_unknown",
    ]
    preview = await space.client.post(
        _url(space, "/types/apercu"), json={"label": "X", "slug": "x", "archimate": ""}
    )
    assert preview.status_code == 400


async def test_types_publies_avec_leurs_objets(space: ApiSpace) -> None:
    _publish(space)
    await _add(space, "Atelier", "atelier", "BusinessInteraction")
    listing = await _types(space)
    assert [(t["slug"], t["objects"], t["state"]) for t in listing] == [
        ("atelier", 0, "created"),
        ("client", 1, None),
    ]


# --- Critère 8 : la racine s'écrit dans le brouillon, comme les Référentiels -------------------


async def test_racine_ecrite_dans_le_brouillon(app: FastAPI, space: ApiSpace) -> None:
    async with app.state.pool.acquire() as conn:
        root = await db_create_branch(
            conn, uuid.UUID(space.space_id), "main", "main", KIND_CHILD, git_ref="main"
        )
    base = f"/api/spaces/{space.space_id}/branches/{root.id}/cartographie"
    assert (await space.client.get(f"{base}/types")).json()["readonly"] is False
    answer = await space.client.post(
        f"{base}/types", json={"label": "Atelier", "slug": "atelier", "archimate": "Grouping"}
    )
    assert answer.status_code == 201, answer.text
    assert forge_read(space, "cartographie/types/atelier.schema.yaml", ref="main") is None


# --- Critère 9 : droits et journal ------------------------------------------------------------


async def test_droits_consulter_et_modifier(app: FastAPI, space: ApiSpace) -> None:
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
        assert (await ana.get(_url(space, "/types"))).status_code == 200
        assert (await ana.get(_url(space, "/catalogue"))).status_code == 200
        denied = await ana.post(
            _url(space, "/types"), json={"label": "X", "slug": "x", "archimate": "Goal"}
        )
        assert denied.status_code == 403
        async with app.state.pool.acquire() as conn:
            await conn.execute(
                "UPDATE profiles SET capabilities = '{spaces:read}' WHERE id = $1", reader
            )
        assert (await ana.get(_url(space, "/types"))).status_code == 403


async def test_journal_d_audit(space: ApiSpace) -> None:
    _publish(space)
    params = {"space_id": space.space_id, "branch_id": space.branch_id}
    answer = await space.client.get("/api/audit", params={**params, "type": "carto_type"})
    assert answer.status_code == 200, answer.text
    events = answer.json()["events"]
    assert [(e["object_type"], e["object_id"], e["action"]) for e in events] == [
        ("carto_type", "client", "created")
    ]
    assert {"carto_type", "carto_object"} <= set(answer.json()["types"])
    objects = await space.client.get("/api/audit", params={**params, "type": "carto_object"})
    assert [(e["object_id"], e["action"]) for e in objects.json()["events"]] == [
        ("ACT-DUPONT", "created")
    ]
