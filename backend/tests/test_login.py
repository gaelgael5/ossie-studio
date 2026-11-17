from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, bootstrap_admin


async def test_login_identifiants_valides_connecte(client: AsyncClient) -> None:
    await bootstrap_admin(client)
    await client.post("/logout")
    response = await client.post("/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert response.status_code == 303
    assert (await client.get("/")).status_code == 200


async def test_login_mauvais_mot_de_passe_refuse(client: AsyncClient) -> None:
    await bootstrap_admin(client)
    await client.post("/logout")
    response = await client.post(
        "/login", data={"email": ADMIN_EMAIL, "password": "faux-mot-de-passe"}
    )
    assert response.status_code == 401
    assert "incorrect" in response.text


async def test_login_email_inconnu_refuse(client: AsyncClient) -> None:
    await bootstrap_admin(client)
    await client.post("/logout")
    response = await client.post(
        "/login", data={"email": "inconnu@example.org", "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 401


async def test_logout_ferme_la_session(client: AsyncClient) -> None:
    await bootstrap_admin(client)
    response = await client.post("/logout")
    assert response.status_code == 303
    home = await client.get("/")
    assert home.status_code == 302
    assert home.headers["location"] == "/login"


async def test_home_sans_session_redirige_vers_login(client: AsyncClient) -> None:
    await bootstrap_admin(client)
    await client.post("/logout")
    response = await client.get("/")
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


async def test_login_page_accessible_apres_bootstrap(client: AsyncClient) -> None:
    await bootstrap_admin(client)
    await client.post("/logout")
    response = await client.get("/login")
    assert response.status_code == 200
    assert "Connexion" in response.text
