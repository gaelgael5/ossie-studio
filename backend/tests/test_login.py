from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, bootstrap_admin


async def test_login_identifiants_valides_connecte(client: AsyncClient) -> None:
    await bootstrap_admin(client)
    await client.post("/api/logout")
    response = await client.post(
        "/api/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200
    assert response.json()["user"]["email"] == ADMIN_EMAIL
    assert (await client.get("/api/session")).json()["authenticated"] is True


async def test_login_mauvais_mot_de_passe_refuse(client: AsyncClient) -> None:
    await bootstrap_admin(client)
    await client.post("/api/logout")
    response = await client.post(
        "/api/login", json={"email": ADMIN_EMAIL, "password": "faux-mot-de-passe"}
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_credentials"


async def test_login_email_inconnu_refuse(client: AsyncClient) -> None:
    await bootstrap_admin(client)
    await client.post("/api/logout")
    response = await client.post(
        "/api/login", json={"email": "inconnu@example.org", "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 401


async def test_logout_ferme_la_session(client: AsyncClient) -> None:
    await bootstrap_admin(client)
    assert (await client.post("/api/logout")).status_code == 200
    assert (await client.get("/api/session")).json()["authenticated"] is False


async def test_connecteurs_publics_listes_sans_secret(client: AsyncClient) -> None:
    await bootstrap_admin(client)
    body = (await client.get("/api/auth/connectors")).json()
    assert body == []  # aucun connecteur déclaré
