"""Profils agents : les primitives MCP accessibles. Calqués sur les profils
utilisateurs ; choisis pour une clé d'API ou une connexion OAuth."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.agent_profiles.service import load_agent_primitives
from ossie_studio.mcp_surface import catalog
from ossie_studio.mcp_surface.catalog import PERSON_ONLY, READ_PRIMITIVES, Primitive
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, bootstrap_admin, create_plain_user, login
from tests.mcp_helpers import tool_names

PASSWORD = "motdepasse-solide"


@pytest.fixture
async def admin(client: AsyncClient) -> AsyncClient:
    await bootstrap_admin(client)
    await login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    return client


async def _person(app: FastAPI, email: str, caps: list[str]) -> None:
    await create_plain_user(app, email, PASSWORD)
    async with app.state.pool.acquire() as conn:
        profile_id = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) VALUES ($1, $2) RETURNING id",
            f"profil-{email}",
            caps,
        )
        await conn.execute("UPDATE users SET profile_id = $2 WHERE email = $1", email, profile_id)


@asynccontextmanager
async def _as(app: FastAPI, email: str) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as browser:
        await login(browser, email, PASSWORD)
        yield browser


async def _create(client: AsyncClient, name: str, primitives: list[str], **extra: Any) -> Any:
    response = await client.post(
        "/api/agent-profiles", json={"name": name, "primitives": primitives, **extra}
    )
    return response


async def _profiles(client: AsyncClient) -> dict[str, dict[str, Any]]:
    body = (await client.get("/api/agent-profiles")).json()
    return {p["name"]: p for p in body["profiles"]}


async def _issue_key(client: AsyncClient, profile_id: str, label: str = "Claude Code") -> str:
    response = await client.post(
        "/api/api-keys", json={"label": label, "agent_profile_id": profile_id}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["secret"])


# --- Critère 1 : le profil livré ---------------------------------------------------------


async def test_profil_lecture_livre_toutes_les_lectures(admin: AsyncClient) -> None:
    body = (await admin.get("/api/agent-profiles")).json()
    lecture = next(p for p in body["profiles"] if p["slug"] == "lecture")
    assert lecture["is_builtin"] and lecture["all_read"]
    assert set(lecture["effective"]) == READ_PRIMITIVES
    # Le catalogue est exposé pour l'écran : lecture/écriture et capacité supposée.
    entry = next(p for p in body["catalog"] if p["id"] == "get_review")
    assert entry == {
        "id": "get_review",
        "domain": "curation",
        "access": "read",
        "capability": "curation:review",
        "person_only": False,
    }


async def test_profil_livre_se_duplique_mais_ne_se_modifie_ni_ne_se_supprime(
    admin: AsyncClient,
) -> None:
    lecture = (await _profiles(admin))["Lecture"]
    assert (
        await admin.put(
            f"/api/agent-profiles/{lecture['id']}", json={"name": "Autre", "primitives": []}
        )
    ).json()["error"] == "agent_profile_builtin_protected"
    deleted = await admin.delete(f"/api/agent-profiles/{lecture['id']}")
    assert deleted.status_code == 409

    copy = await admin.post(f"/api/agent-profiles/{lecture['id']}/duplicate")
    assert copy.status_code == 201
    assert copy.json()["name"] == "Lecture (copie)"
    assert copy.json()["all_read"] is True and copy.json()["is_builtin"] is False
    again = await admin.post(f"/api/agent-profiles/{lecture['id']}/duplicate")
    assert again.json()["name"] == "Lecture (copie) 2"


# --- Critère 2 : créer, puis choisir pour une clé ------------------------------------------


async def test_creer_un_profil_le_rend_choisissable(app: FastAPI, admin: AsyncClient) -> None:
    created = await _create(admin, "Repérage", ["list_spaces", "list_sources"])
    assert created.status_code == 201, created.text
    profile = created.json()
    assert profile["slug"] == "reperage"
    assert profile["effective"] == ["list_sources", "list_spaces"]

    options = (await admin.get("/api/api-keys/options")).json()["agent_profiles"]
    assert "Repérage" in [p["name"] for p in options]
    secret = await _issue_key(admin, profile["id"])
    assert set(await tool_names(app, secret)) == {"list_spaces", "list_sources"}


async def test_saisies_invalides_refusees(admin: AsyncClient) -> None:
    unknown = await _create(admin, "X", ["n_existe_pas"])
    assert unknown.json()["error_args"]["fields"] == ["primitive_unknown"]
    no_name = await _create(admin, " ", [])
    assert no_name.json()["error_args"]["fields"] == ["agent_profile_name_required"]
    bad_slug = await _create(admin, "X", [], slug="Pas Un Slug")
    assert bad_slug.json()["error_args"]["fields"] == ["agent_profile_slug_invalid"]
    assert (await _create(admin, "Lecture", [])).json()["error"] == "agent_profile_name_taken"
    assert (await _create(admin, "Y", [], slug="lecture")).json()["error"] == (
        "agent_profile_slug_taken"
    )


# --- Critère 3 : une primitive retirée ne sert plus dès l'appel suivant -----------------------


async def test_primitive_retiree_indisponible_des_l_appel_suivant(
    app: FastAPI, admin: AsyncClient
) -> None:
    profile = (await _create(admin, "Repérage", ["list_spaces", "list_sources"])).json()
    secret = await _issue_key(admin, profile["id"])
    assert "list_sources" in await tool_names(app, secret)

    updated = await admin.put(
        f"/api/agent-profiles/{profile['id']}",
        json={"name": "Repérage", "primitives": ["list_spaces"]},
    )
    assert updated.status_code == 200, updated.text
    assert await tool_names(app, secret) == ["list_spaces"]


# --- Critère 4 : une primitive ajoutée au catalogue n'est donnée à personne --------------------


async def test_primitive_ajoutee_au_catalogue_fermee_par_defaut(
    app: FastAPI, admin: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = (await _create(admin, "Repérage", ["list_spaces"])).json()
    lecture = (await _profiles(admin))["Lecture"]
    fake_write = Primitive("write_fake", "curation", "write", "curation:write", "Fake write.")
    fake_read = Primitive("read_fake", "curation", "read", "curation:read", "Fake read.")
    by_id = {**catalog.BY_ID, fake_write.id: fake_write, fake_read.id: fake_read}
    monkeypatch.setattr(catalog, "BY_ID", by_id)
    monkeypatch.setattr(catalog, "ALL_PRIMITIVES", frozenset(by_id))
    monkeypatch.setattr(catalog, "READ_PRIMITIVES", READ_PRIMITIVES | {fake_read.id})

    async with app.state.pool.acquire() as conn:
        custom_now = await load_agent_primitives(conn, uuid.UUID(custom["id"]))
        lecture_now = await load_agent_primitives(conn, uuid.UUID(lecture["id"]))
    assert custom_now == {"list_spaces"}
    # Le profil « toutes les lectures » reçoit la nouvelle lecture, jamais l'écriture.
    assert "read_fake" in lecture_now and "write_fake" not in lecture_now


# --- Critère 5 : supprimer un profil en service est refusé, en les nommant ---------------------


async def test_suppression_refusee_tant_qu_une_cle_ou_connexion_active_le_porte(
    app: FastAPI, admin: AsyncClient
) -> None:
    profile = (await _create(admin, "Repérage", ["list_spaces"])).json()
    await _issue_key(admin, profile["id"], label="Clé de Gael")
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO oauth_clients (client_id, client_name, metadata) "
            "VALUES ('c1', 'Claude', '{}')"
        )
        await conn.execute(
            "INSERT INTO oauth_connections (user_id, client_id, agent_profile_id) "
            "SELECT id, 'c1', $1 FROM users WHERE is_bootstrap",
            uuid.UUID(profile["id"]),
        )

    refused = await admin.delete(f"/api/agent-profiles/{profile['id']}")
    assert refused.status_code == 409
    assert refused.json()["error"] == "agent_profile_in_use"
    assert refused.json()["error_args"] == {
        "keys": "Clé de Gael",
        "connections": f"Claude ({ADMIN_EMAIL})",
    }
    listed = (await _profiles(admin))["Repérage"]
    assert listed["used_by"] == {
        "keys": ["Clé de Gael"],
        "connections": [f"Claude ({ADMIN_EMAIL})"],
    }


async def test_suppression_d_un_profil_libre(app: FastAPI, admin: AsyncClient) -> None:
    profile = (await _create(admin, "Repérage", ["list_spaces"])).json()
    key_id = (
        await admin.post(
            "/api/api-keys", json={"label": "Ancienne", "agent_profile_id": profile["id"]}
        )
    ).json()["key"]["id"]
    await admin.post(f"/api/api-keys/{key_id}/revoke")

    assert (await admin.delete(f"/api/agent-profiles/{profile['id']}")).status_code == 204
    assert "Repérage" not in await _profiles(admin)
    # Plus choisissable ; le nom redevient libre ; l'historique de la clé révoquée le garde.
    refused = await admin.post(
        "/api/api-keys", json={"label": "x", "agent_profile_id": profile["id"]}
    )
    assert refused.json()["error"] == "agent_profile_not_found"
    assert (await _create(admin, "Repérage", [])).status_code == 201
    keys = (await admin.get("/api/api-keys")).json()["keys"]
    assert keys[0]["agent_profile"]["name"] == "Repérage"


# --- Critère 6 : capacités « Profils agents » -------------------------------------------------


async def test_reserve_aux_capacites_profils_agents(app: FastAPI, admin: AsyncClient) -> None:
    lecture_id = (await _profiles(admin))["Lecture"]["id"]
    await _person(app, "lecteur@example.org", ["agent_profiles:read"])
    await _person(app, "rien@example.org", [])
    async with _as(app, "lecteur@example.org") as reader:
        assert (await reader.get("/api/agent-profiles")).status_code == 200
        assert (await _create(reader, "X", [])).status_code == 403
        assert (await reader.post(f"/api/agent-profiles/{lecture_id}/duplicate")).status_code == 403
        assert (await reader.delete(f"/api/agent-profiles/{lecture_id}")).status_code == 403
    async with _as(app, "rien@example.org") as nobody:
        assert (await nobody.get("/api/agent-profiles")).status_code == 403


# --- Garde contre l'élévation à la modification ------------------------------------------------


async def test_on_n_ajoute_que_des_primitives_dont_on_a_la_capacite(
    app: FastAPI, admin: AsyncClient
) -> None:
    profile = (await _create(admin, "Repérage", ["list_spaces"])).json()
    await _issue_key(admin, profile["id"])
    await _person(
        app, "ana@example.org", ["agent_profiles:read", "agent_profiles:write", "spaces:read"]
    )
    async with _as(app, "ana@example.org") as ana:
        # Ajouter list_sources (suppose sources:read, qu'elle n'a pas) : refusé, nommé.
        refused = await ana.put(
            f"/api/agent-profiles/{profile['id']}",
            json={"name": "Repérage", "primitives": ["list_spaces", "list_sources"]},
        )
        assert refused.status_code == 403
        assert refused.json()["error"] == "agent_profile_beyond_rights"
        assert refused.json()["error_args"]["primitives"] == "list_sources"
        # « Toutes les lectures » ajouterait aussi ce qu'elle n'a pas.
        all_read = await ana.put(
            f"/api/agent-profiles/{profile['id']}",
            json={"name": "Repérage", "primitives": ["list_spaces"], "all_read": True},
        )
        assert all_read.status_code == 403
        # Retirer, ou ajouter ce qu'elle a elle-même : permis.
        ok = await ana.put(
            f"/api/agent-profiles/{profile['id']}",
            json={"name": "Repérage", "primitives": ["list_spaces", "list_versions"]},
        )
        assert ok.status_code == 200
        removed = await ana.put(
            f"/api/agent-profiles/{profile['id']}", json={"name": "Repérage", "primitives": []}
        )
        assert removed.status_code == 200
        # Dupliquer « Lecture » lui donnerait tout : refusé aussi.
        lecture_id = (await _profiles(ana))["Lecture"]["id"]
        assert (await ana.post(f"/api/agent-profiles/{lecture_id}/duplicate")).status_code == 403


async def test_primitives_du_brouillon_hors_de_la_garde(app: FastAPI, admin: AsyncClient) -> None:
    """Une clé ne les reçoit jamais : les ajouter n'élève personne."""
    await _person(app, "ana@example.org", ["agent_profiles:write"])
    async with _as(app, "ana@example.org") as ana:
        created = await _create(ana, "Brouillon", sorted(PERSON_ONLY))
    assert created.status_code == 201, created.text


# --- Journal ------------------------------------------------------------------------------------


async def test_modifications_journalisees_avec_leur_auteur(
    admin: AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    profile = (await _create(admin, "Repérage", ["list_spaces"])).json()
    capsys.readouterr()
    await admin.put(
        f"/api/agent-profiles/{profile['id']}",
        json={"name": "Repérage", "primitives": ["list_sources"]},
    )
    await admin.delete(f"/api/agent-profiles/{profile['id']}")
    entries = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]
    updated = next(e for e in entries if e["event"] == "agent_profile_updated")
    assert updated["added"] == ["list_sources"] and updated["removed"] == ["list_spaces"]
    assert updated["author"] == ADMIN_EMAIL
    assert any(e["event"] == "agent_profile_deleted" for e in entries)
