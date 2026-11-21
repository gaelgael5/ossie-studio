from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, bootstrap_admin, db_fetchval, login


@pytest.fixture
async def admin(client: AsyncClient) -> AsyncClient:
    await bootstrap_admin(client)
    return client


async def test_langue_enregistree_et_relue(admin: AsyncClient) -> None:
    assert (await admin.get("/api/session")).json()["language"] == ""
    r = await admin.put("/api/session/language", json={"language": "fr"})
    assert r.status_code == 200
    assert (await admin.get("/api/session")).json()["language"] == "fr"


async def test_autre_session_du_meme_compte_retrouve_la_langue(
    admin: AsyncClient, app: FastAPI
) -> None:
    await admin.put("/api/session/language", json={"language": "fr"})
    # Une autre session (autre navigateur) du même compte s'ouvre en français.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as other:
        await login(other, ADMIN_EMAIL, ADMIN_PASSWORD)
        assert (await other.get("/api/session")).json()["language"] == "fr"


async def test_langue_invalide_refusee(admin: AsyncClient) -> None:
    r = await admin.put("/api/session/language", json={"language": "xx"})
    assert r.status_code == 400
    assert "language_invalid" in r.json()["error_args"]["fields"]


async def test_langue_stockee_inconnue_renvoie_vide(admin: AsyncClient, app: FastAPI) -> None:
    # Une valeur invalide en base n'est jamais renvoyée telle quelle (→ "").
    await app.state.pool.execute(
        "UPDATE users SET language = 'martien' WHERE email = $1", ADMIN_EMAIL
    )
    assert (await admin.get("/api/session")).json()["language"] == ""


async def test_non_authentifie_refuse(client: AsyncClient) -> None:
    # Sans compte amorcé ni session, on ne peut pas enregistrer une langue.
    r = await client.put("/api/session/language", json={"language": "fr"})
    assert r.status_code == 401


async def test_seul_son_propre_compte(admin: AsyncClient, app: FastAPI) -> None:
    # L'endpoint n'agit que sur le compte de la session : l'autre reste intact.
    from ossie_studio.models import ROLE_USER
    from ossie_studio.security import hash_password

    await app.state.pool.execute(
        "INSERT INTO users (email, password_hash, role) VALUES ($1, $2, $3)",
        "autre@example.org",
        hash_password("mot-de-passe-solide"),
        ROLE_USER,
    )
    await admin.put("/api/session/language", json={"language": "fr"})
    autre_lang = await db_fetchval(
        app, "SELECT language FROM users WHERE email = 'autre@example.org'"
    )
    assert autre_lang is None
