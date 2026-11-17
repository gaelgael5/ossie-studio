from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.conftest import (
    bootstrap_admin,
    create_plain_user,
    db_fetchrow,
    db_fetchval,
    login,
    wire_fake_idp,
)
from tests.fake_forge import GITHUB_LOGIN, GOOD_TOKEN, fake_github_app

URL = "https://github.com"


@pytest.fixture
async def admin_client(client: AsyncClient, app: FastAPI) -> AsyncClient:
    # Le faux GitHub est branché via la fabrique de client HTTP sortant.
    wire_fake_idp(app, fake_github_app())
    await bootstrap_admin(client)
    return client


async def _create(
    client: AsyncClient, label: str = "GitHub perso", token: str = GOOD_TOKEN
) -> AsyncClient:
    response = await client.post(
        "/api/connections",
        json={"label": label, "forge": "github", "url": URL, "token": token},
    )
    assert response.status_code == 201, response.text
    return client


async def test_ajout_verifie_puis_enregistre_connexion_et_secret(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    await _create(admin_client)
    row = await db_fetchrow(app, "SELECT forge, identity, secret_slug FROM connections")
    assert row is not None
    assert row["forge"] == "github"
    assert row["identity"] == GITHUB_LOGIN
    # Le jeton vit comme secret, catégorie github, chiffré.
    secret = await db_fetchrow(
        app, "SELECT category, value_encrypted FROM secrets WHERE slug = $1", row["secret_slug"]
    )
    assert secret is not None
    assert secret["category"] == "github"
    assert GOOD_TOKEN.encode() not in secret["value_encrypted"]


async def test_reponse_ne_contient_ni_jeton_ni_slug_de_secret(admin_client: AsyncClient) -> None:
    response = await admin_client.post(
        "/api/connections",
        json={"label": "GitHub", "forge": "github", "url": URL, "token": GOOD_TOKEN},
    )
    body = response.json()
    assert GOOD_TOKEN not in str(body)
    assert "secret_slug" not in body
    assert body["identity"] == GITHUB_LOGIN


async def test_jeton_invalide_refuse_rien_enregistre(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    response = await admin_client.post(
        "/api/connections",
        json={"label": "GitHub", "forge": "github", "url": URL, "token": "mauvais"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "connection_token_invalid"
    assert await db_fetchval(app, "SELECT count(*) FROM connections") == 0
    assert await db_fetchval(app, "SELECT count(*) FROM secrets") == 0


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("label", "", "label_required"),
        ("forge", "bitbucket", "forge_invalid"),
        ("url", "pas-une-url", "url_invalid"),
        ("token", "", "token_required"),
    ],
)
async def test_champ_invalide_refuse(
    admin_client: AsyncClient, field: str, value: str, code: str
) -> None:
    data = {"label": "X", "forge": "github", "url": URL, "token": GOOD_TOKEN}
    data[field] = value
    response = await admin_client.post("/api/connections", json=data)
    assert response.status_code == 400
    assert code in response.json()["error_args"]["fields"]


async def test_doublon_meme_forge_url_identite_refuse(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    await _create(admin_client)
    response = await admin_client.post(
        "/api/connections",
        json={"label": "Doublon", "forge": "github", "url": URL, "token": GOOD_TOKEN},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "connection_duplicate"
    assert await db_fetchval(app, "SELECT count(*) FROM connections") == 1


async def test_liste_affiche_identite_et_etat(admin_client: AsyncClient) -> None:
    await _create(admin_client)
    body = (await admin_client.get("/api/connections")).json()
    assert len(body["connections"]) == 1
    c = body["connections"][0]
    assert c["identity"] == GITHUB_LOGIN
    assert c["last_status"] == "valid"
    assert GOOD_TOKEN not in str(body)


async def test_tester_remet_a_jour_l_etat(admin_client: AsyncClient, app: FastAPI) -> None:
    await _create(admin_client)
    connection_id = await db_fetchval(app, "SELECT id FROM connections")
    response = await admin_client.post(f"/api/connections/{connection_id}/test")
    assert response.status_code == 200
    assert response.json()["last_status"] == "valid"


async def test_suppression_retire_connexion_et_secret(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    await _create(admin_client)
    connection_id = await db_fetchval(app, "SELECT id FROM connections")
    response = await admin_client.delete(f"/api/connections/{connection_id}")
    assert response.status_code == 204
    assert await db_fetchval(app, "SELECT count(*) FROM connections") == 0
    assert await db_fetchval(app, "SELECT count(*) FROM secrets") == 0


async def test_suppression_inexistante_404(admin_client: AsyncClient) -> None:
    response = await admin_client.delete(f"/api/connections/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"] == "connection_not_found"


async def test_jeton_absent_des_logs(
    admin_client: AsyncClient, capfd: pytest.CaptureFixture[str]
) -> None:
    await _create(admin_client)
    logs = capfd.readouterr()
    assert GOOD_TOKEN not in (logs.out + logs.err)


@pytest.fixture
async def user_client(admin_client: AsyncClient, app: FastAPI) -> AsyncIterator[AsyncClient]:
    await create_plain_user(app, "simple@example.org", "mot-de-passe-simple")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as second:
        await login(second, "simple@example.org", "mot-de-passe-simple")
        yield second


async def test_non_admin_refus_403(user_client: AsyncClient) -> None:
    assert (await user_client.get("/api/connections")).status_code == 403
    creation = await user_client.post(
        "/api/connections",
        json={"label": "X", "forge": "github", "url": URL, "token": GOOD_TOKEN},
    )
    assert creation.status_code == 403
