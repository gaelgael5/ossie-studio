"""Identifiant personnel par forge : un par (utilisateur, connexion de forge),
état explicite, privé, testé contre le droit d'écriture sur le dépôt de l'espace."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.conftest import bootstrap_admin, create_plain_user, login, wire_fake_idp
from tests.fake_forge import GITHUB_LOGIN, GOOD_TOKEN, READONLY_TOKEN, fake_github_app

PASSWORD = "motdepasse-solide"


@asynccontextmanager
async def _as(app: FastAPI, email: str) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, email, PASSWORD)
        yield client


@pytest.fixture
async def setup(client: AsyncClient, app: FastAPI) -> dict[str, str]:
    """Admin + connexion GitHub + deux espaces sur la même connexion ; Alice a
    un profil de base qui lit les espaces."""
    wire_fake_idp(app, fake_github_app())
    await bootstrap_admin(client)
    await client.post(
        "/api/secrets",
        json={"slug": "gh", "label": "Jeton", "category": "github", "value": GOOD_TOKEN},
    )
    created = await client.post(
        "/api/connections",
        json={
            "label": "GitHub",
            "forge": "github",
            "url": "https://github.com",
            "secret_slug": "gh",
        },
    )
    connection_id = created.json()["id"]
    await create_plain_user(app, "alice@x.org", PASSWORD)
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) VALUES ('Lecteur', $1) RETURNING id",
            ["spaces:read"],
        )
        await conn.execute("UPDATE users SET profile_id = $1 WHERE email = 'alice@x.org'", profile)
        spaces = []
        for name in ("e1", "e2"):
            spaces.append(
                await conn.fetchval(
                    "INSERT INTO git_spaces (name, connection_id, repo_url, last_probe_status) "
                    "VALUES ($1, $2, $3, 'valid') RETURNING id",
                    name,
                    uuid.UUID(connection_id),
                    f"https://github.com/octocat/{name}.git",
                )
            )
    return {"connection_id": connection_id, "e1": str(spaces[0]), "e2": str(spaces[1])}


async def _mine(client: AsyncClient, space_id: str) -> dict[str, object]:
    response = await client.get("/api/me/credentials", params={"space_id": space_id})
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


async def test_creation_au_premier_besoin_sans_doublon(app: FastAPI, setup: dict[str, str]) -> None:
    async with _as(app, "alice@x.org") as client:
        first = await _mine(client, setup["e1"])
        second = await _mine(client, setup["e1"])
    assert first["status"] == "a_saisir"
    assert first["id"] == second["id"]
    assert first["forge"] == "github"
    assert first["url"] == "https://github.com"
    async with app.state.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM personal_credentials") == 1


async def test_meme_connexion_meme_identifiant_pour_deux_espaces(
    app: FastAPI, setup: dict[str, str]
) -> None:
    async with _as(app, "alice@x.org") as client:
        assert (await _mine(client, setup["e1"]))["id"] == (await _mine(client, setup["e2"]))["id"]


async def test_tester_valide_avec_droit_ecriture(app: FastAPI, setup: dict[str, str]) -> None:
    async with _as(app, "alice@x.org") as client:
        cred = await _mine(client, setup["e1"])
        saved = await client.put(f"/api/me/credentials/{cred['id']}", json={"value": GOOD_TOKEN})
        assert saved.json()["status"] == "jamais_teste"
        tested = await client.post(
            f"/api/me/credentials/{cred['id']}/test", json={"space_id": setup["e1"]}
        )
    assert tested.status_code == 200, tested.text
    assert tested.json()["status"] == "valide"
    assert tested.json()["forge_login"] == GITHUB_LOGIN


async def test_tester_jeton_rejete(app: FastAPI, setup: dict[str, str]) -> None:
    async with _as(app, "alice@x.org") as client:
        cred = await _mine(client, setup["e1"])
        await client.put(f"/api/me/credentials/{cred['id']}", json={"value": "mauvais"})
        tested = await client.post(
            f"/api/me/credentials/{cred['id']}/test", json={"space_id": setup["e1"]}
        )
    assert tested.json()["status"] == "refuse"
    assert tested.json()["error_code"] == "connection_token_invalid"


async def test_tester_sans_droit_ecriture_refuse(app: FastAPI, setup: dict[str, str]) -> None:
    async with _as(app, "alice@x.org") as client:
        cred = await _mine(client, setup["e1"])
        await client.put(f"/api/me/credentials/{cred['id']}", json={"value": READONLY_TOKEN})
        tested = await client.post(
            f"/api/me/credentials/{cred['id']}/test", json={"space_id": setup["e1"]}
        )
    assert tested.json()["status"] == "refuse"
    assert tested.json()["error_code"] == "credential_no_write"
    assert tested.json()["forge_login"] == "lecteur"


async def test_tester_sans_secret_refuse_proprement(app: FastAPI, setup: dict[str, str]) -> None:
    async with _as(app, "alice@x.org") as client:
        cred = await _mine(client, setup["e1"])
        tested = await client.post(
            f"/api/me/credentials/{cred['id']}/test", json={"space_id": setup["e1"]}
        )
    assert tested.status_code == 400
    assert tested.json()["error"] == "credential_value_missing"


async def test_forge_injoignable_etat_inchange(app: FastAPI, setup: dict[str, str]) -> None:
    async with _as(app, "alice@x.org") as client:
        cred = await _mine(client, setup["e1"])
        await client.put(f"/api/me/credentials/{cred['id']}", json={"value": GOOD_TOKEN})

        def _down() -> AsyncClient:
            raise_transport = ASGITransport(app=_Boom())
            return AsyncClient(transport=raise_transport, base_url="http://forge")

        app.state.http_client_factory = _down
        tested = await client.post(
            f"/api/me/credentials/{cred['id']}/test", json={"space_id": setup["e1"]}
        )
    assert tested.status_code == 400
    assert tested.json()["error"] == "connection_url_unreachable"
    async with app.state.pool.acquire() as conn:
        assert await conn.fetchval("SELECT status FROM personal_credentials") == "jamais_teste"


class _Boom:
    async def __call__(self, scope: object, receive: object, send: object) -> None:
        import httpx

        raise httpx.ConnectError("injoignable")


async def test_changer_le_secret_remet_jamais_teste(app: FastAPI, setup: dict[str, str]) -> None:
    async with _as(app, "alice@x.org") as client:
        cred = await _mine(client, setup["e1"])
        await client.put(f"/api/me/credentials/{cred['id']}", json={"value": GOOD_TOKEN})
        await client.post(f"/api/me/credentials/{cred['id']}/test", json={"space_id": setup["e1"]})
        again = await client.put(f"/api/me/credentials/{cred['id']}", json={"value": "autre"})
    assert again.json()["status"] == "jamais_teste"
    assert again.json()["forge_login"] is None


async def test_un_admin_ne_voit_pas_l_identifiant_d_un_autre(
    client: AsyncClient, app: FastAPI, setup: dict[str, str]
) -> None:
    async with _as(app, "alice@x.org") as alice:
        cred = await _mine(alice, setup["e1"])
        await alice.put(f"/api/me/credentials/{cred['id']}", json={"value": GOOD_TOKEN})
    # `client` est connecté en administrateur de démarrage.
    assert (
        await client.put(f"/api/me/credentials/{cred['id']}", json={"value": "x"})
    ).status_code == 404
    tested = await client.post(
        f"/api/me/credentials/{cred['id']}/test", json={"space_id": setup["e1"]}
    )
    assert tested.status_code == 404
    mine = await _mine(client, setup["e1"])
    assert mine["id"] != cred["id"]


async def test_valeur_jamais_dans_reponse_ni_logs(
    app: FastAPI, setup: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    async with _as(app, "alice@x.org") as client:
        cred = await _mine(client, setup["e1"])
        saved = await client.put(f"/api/me/credentials/{cred['id']}", json={"value": GOOD_TOKEN})
        tested = await client.post(
            f"/api/me/credentials/{cred['id']}/test", json={"space_id": setup["e1"]}
        )
        listing = await client.get("/api/me/credentials", params={"space_id": setup["e1"]})
    for response in (saved, tested, listing):
        assert GOOD_TOKEN not in response.text
    assert GOOD_TOKEN not in capsys.readouterr().out


async def test_suppression_utilisateur_supprime_identifiants(
    app: FastAPI, setup: dict[str, str]
) -> None:
    async with _as(app, "alice@x.org") as client:
        await _mine(client, setup["e1"])
    async with app.state.pool.acquire() as conn:
        await conn.execute("DELETE FROM users WHERE email = 'alice@x.org'")
        assert await conn.fetchval("SELECT count(*) FROM personal_credentials") == 0


async def test_espace_illisible_refuse(app: FastAPI, setup: dict[str, str]) -> None:
    await create_plain_user(app, "bob@x.org", PASSWORD)  # aucun profil
    async with _as(app, "bob@x.org") as client:
        response = await client.get("/api/me/credentials", params={"space_id": setup["e1"]})
    assert response.status_code == 403


async def test_rattachement_equipe_avec_ecriture_cree_les_identifiants(
    client: AsyncClient, app: FastAPI, setup: dict[str, str]
) -> None:
    async with app.state.pool.acquire() as conn:
        writer = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) VALUES ('Contrib', $1) RETURNING id",
            ["spaces:read", "sources:declare"],
        )
        reader = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) VALUES ('Lect', $1) RETURNING id",
            ["spaces:read"],
        )
        team = await conn.fetchval(
            "INSERT INTO teams (name, slug, origin) VALUES ('data', 'data', 'manual') RETURNING id"
        )
        alice = await conn.fetchval("SELECT id FROM users WHERE email = 'alice@x.org'")
        await conn.execute(
            "INSERT INTO user_teams (user_id, team_id, origin) VALUES ($1, $2, 'manual')",
            alice,
            team,
        )
    e1 = setup["e1"]
    await client.put(f"/api/spaces/{e1}/teams/{team}", json={"profile_id": str(reader)})
    async with app.state.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM personal_credentials") == 0
    await client.put(f"/api/spaces/{e1}/teams/{team}", json={"profile_id": str(writer)})
    async with app.state.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT user_id, status FROM personal_credentials")
    assert row is not None and row["user_id"] == alice and row["status"] == "a_saisir"
