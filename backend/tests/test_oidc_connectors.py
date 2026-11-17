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
from tests.fake_idp import FakeIdP

ISSUER = "http://idp.test/realms/test"
SECRET = "client-secret-ultra-confidentiel"


@pytest.fixture
def idp() -> FakeIdP:
    return FakeIdP()


@pytest.fixture
async def admin_client(client: AsyncClient, app: FastAPI, idp: FakeIdP) -> AsyncClient:
    wire_fake_idp(app, idp.app())
    await bootstrap_admin(client)
    return client


async def _add(client: AsyncClient, issuer: str = ISSUER, label: str = "Mon IdP") -> AsyncClient:
    response = await client.post(
        "/api/oidc/connectors",
        json={
            "label": label,
            "issuer": issuer,
            "client_id": "client-x",
            "client_secret": SECRET,
            "scopes": "openid email profile",
        },
    )
    assert response.status_code == 201, response.text
    return client


async def test_ajout_interroge_decouverte_et_enregistre(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    await _add(admin_client)
    row = await db_fetchrow(app, "SELECT issuer, client_secret_encrypted FROM oidc_connectors")
    assert row is not None
    assert row["issuer"] == ISSUER
    assert SECRET.encode() not in row["client_secret_encrypted"]


async def test_issuer_injoignable_refuse(admin_client: AsyncClient, app: FastAPI) -> None:
    response = await admin_client.post(
        "/api/oidc/connectors",
        json={
            "label": "Cassé",
            "issuer": "http://idp.test/realms/inexistant",
            "client_id": "client-x",
            "client_secret": SECRET,
            "scopes": "openid email profile",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "oidc_issuer_unreachable"
    assert await db_fetchval(app, "SELECT count(*) FROM oidc_connectors") == 0


async def test_deux_connecteurs_meme_issuer_refuse(admin_client: AsyncClient, app: FastAPI) -> None:
    await _add(admin_client)
    response = await admin_client.post(
        "/api/oidc/connectors",
        json={
            "label": "Doublon",
            "issuer": ISSUER,
            "client_id": "autre",
            "client_secret": SECRET,
            "scopes": "openid email profile",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"] == "oidc_issuer_already_declared"
    assert await db_fetchval(app, "SELECT count(*) FROM oidc_connectors") == 1


@pytest.mark.parametrize(
    ("missing", "field"),
    [
        ("label", "label_required"),
        ("issuer", "issuer_invalid"),
        ("client_id", "client_id_required"),
        ("client_secret", "client_secret_required"),
    ],
)
async def test_champ_obligatoire_vide_refuse(
    admin_client: AsyncClient, app: FastAPI, missing: str, field: str
) -> None:
    data = {
        "label": "X",
        "issuer": ISSUER,
        "client_id": "client-x",
        "client_secret": SECRET,
        "scopes": "openid email profile",
    }
    data[missing] = ""
    response = await admin_client.post("/api/oidc/connectors", json=data)
    assert response.status_code == 400
    assert field in response.json()["error_args"]["fields"]
    assert await db_fetchval(app, "SELECT count(*) FROM oidc_connectors") == 0


async def test_connecteur_propose_sur_login_public(admin_client: AsyncClient) -> None:
    await _add(admin_client, label="Mon IdP")
    await admin_client.post("/api/logout")
    body = (await admin_client.get("/api/auth/connectors")).json()
    assert len(body) == 1
    assert body[0]["label"] == "Mon IdP"
    assert "client_secret" not in body[0]


async def test_secret_absent_de_la_liste_admin(admin_client: AsyncClient) -> None:
    await _add(admin_client)
    body = (await admin_client.get("/api/oidc/connectors")).json()
    assert SECRET not in str(body)


async def test_suppression_dernier_connecteur_possible(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    await _add(admin_client)
    connector_id = await db_fetchval(app, "SELECT id FROM oidc_connectors")
    response = await admin_client.delete(f"/api/oidc/connectors/{connector_id}")
    assert response.status_code == 204
    assert await db_fetchval(app, "SELECT count(*) FROM oidc_connectors") == 0


async def test_suppression_connecteur_inexistant_404(admin_client: AsyncClient) -> None:
    response = await admin_client.delete(f"/api/oidc/connectors/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


@pytest.fixture
async def user_client(admin_client: AsyncClient, app: FastAPI) -> AsyncIterator[AsyncClient]:
    await create_plain_user(app, "simple@example.org", "mot-de-passe-simple")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as second:
        await login(second, "simple@example.org", "mot-de-passe-simple")
        yield second


async def test_non_admin_refus_403(user_client: AsyncClient) -> None:
    assert (await user_client.get("/api/oidc/connectors")).status_code == 403
    creation = await user_client.post(
        "/api/oidc/connectors",
        json={
            "label": "X",
            "issuer": ISSUER,
            "client_id": "c",
            "client_secret": "s",
            "scopes": "openid",
        },
    )
    assert creation.status_code == 403
    assert (await user_client.delete(f"/api/oidc/connectors/{uuid.uuid4()}")).status_code == 403
