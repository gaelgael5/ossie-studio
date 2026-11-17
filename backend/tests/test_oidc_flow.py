from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.models import ROLE_ADMIN
from tests.conftest import bootstrap_admin, db_fetch, db_fetchval, wire_fake_idp
from tests.fake_idp import FakeIdP, split_query

ISSUER = "http://idp.test/realms/test"
SECRET = "client-secret-ultra-confidentiel"


@pytest.fixture
def idp() -> FakeIdP:
    return FakeIdP()


@pytest.fixture
async def flow(
    client: AsyncClient, app: FastAPI, idp: FakeIdP
) -> tuple[AsyncClient, FakeIdP, uuid.UUID]:
    """App amorcée, connecteur ajouté, prête pour un login OIDC."""
    wire_fake_idp(app, idp.app())
    await bootstrap_admin(client)
    await client.post(
        "/api/oidc/connectors",
        json={
            "label": "Mon IdP",
            "issuer": ISSUER,
            "client_id": "client-x",
            "client_secret": SECRET,
            "scopes": "openid email profile",
        },
    )
    connector_id = await db_fetchval(app, "SELECT id FROM oidc_connectors")
    await client.post("/api/logout")
    return client, idp, connector_id


async def _run_login(client: AsyncClient, idp: FakeIdP, connector_id: uuid.UUID) -> object:
    """Déroule login → autorisation (faux IdP) → callback, comme un navigateur."""
    start = await client.get(f"/auth/oidc/{connector_id}/login")
    assert start.status_code == 303
    authz_url = start.headers["location"]
    async with AsyncClient(
        transport=ASGITransport(app=idp.app()), base_url="http://idp.test"
    ) as idp_client:
        redirect = await idp_client.get(authz_url)
    callback_url = redirect.headers["location"]
    return await client.get(callback_url)


async def test_login_complet_nouvel_arrivant_cree_un_compte(
    flow: tuple[AsyncClient, FakeIdP, uuid.UUID], app: FastAPI
) -> None:
    client, idp, connector_id = flow
    idp.set_next_user(sub="sub-neuf", email="neuf@example.org", email_verified=True)
    response = await _run_login(client, idp, connector_id)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    session = (await client.get("/api/session")).json()
    assert session["authenticated"] is True
    assert session["user"]["email"] == "neuf@example.org"
    assert len(await db_fetch(app, "SELECT id FROM users")) == 2


async def test_login_complet_admin_garde_son_role(
    flow: tuple[AsyncClient, FakeIdP, uuid.UUID], app: FastAPI
) -> None:
    client, idp, connector_id = flow
    idp.set_next_user(sub="sub-admin", email="gael@example.org", email_verified=True)
    response = await _run_login(client, idp, connector_id)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    users = await db_fetch(app, "SELECT role FROM users")
    assert len(users) == 1  # pas de doublon : rattaché au compte admin
    assert users[0]["role"] == ROLE_ADMIN


async def test_login_complet_email_non_verifie_refuse(
    flow: tuple[AsyncClient, FakeIdP, uuid.UUID],
) -> None:
    client, idp, connector_id = flow
    idp.set_next_user(sub="sub-x", email="neuf@example.org", email_verified=False)
    response = await _run_login(client, idp, connector_id)
    assert response.status_code == 303
    assert split_query(response.headers["location"])["error"] == "oidc_email_unverified"


async def test_deuxieme_login_email_change_meme_compte(
    flow: tuple[AsyncClient, FakeIdP, uuid.UUID], app: FastAPI
) -> None:
    client, idp, connector_id = flow
    idp.set_next_user(sub="sub-stable", email="personne@example.org", email_verified=True)
    await _run_login(client, idp, connector_id)
    await client.post("/api/logout")
    idp.set_next_user(sub="sub-stable", email="personne-renommee@example.org", email_verified=True)
    response = await _run_login(client, idp, connector_id)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    identities = await db_fetch(app, "SELECT id FROM user_identities")
    assert len(identities) == 1  # retrouvé par (issuer, sub), aucun doublon
