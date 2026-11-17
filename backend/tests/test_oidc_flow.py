from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from ossie_studio.models import ROLE_ADMIN, User
from tests.conftest import bootstrap_admin, wire_fake_idp
from tests.fake_idp import FakeIdP

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
    idp_asgi = idp.app()
    wire_fake_idp(app, idp_asgi)
    await bootstrap_admin(client)
    await client.post(
        "/oidc/connectors",
        data={
            "label": "Mon IdP",
            "issuer": ISSUER,
            "client_id": "client-x",
            "client_secret": SECRET,
            "scopes": "openid email profile",
        },
    )
    async with app.state.session_factory() as session:
        from ossie_studio.models import OidcConnector

        connector_id = (await session.execute(select(OidcConnector))).scalar_one().id
    await client.post("/logout")
    return client, idp, connector_id


async def _run_login(
    app: FastAPI, client: AsyncClient, idp: FakeIdP, connector_id: uuid.UUID
) -> object:
    """Déroule login → autorisation (faux IdP) → callback, comme un navigateur."""
    start = await client.get(f"/auth/oidc/{connector_id}/login")
    assert start.status_code == 303
    authz_url = start.headers["location"]
    # L'agent suivrait la redirection vers l'IdP : on la joue sur le faux IdP,
    # qui partage son état (_pending, clé, next_user) avec celui câblé dans l'app.
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
    response = await _run_login(app, client, idp, connector_id)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    home = await client.get("/")
    assert "neuf@example.org" in home.text
    async with app.state.session_factory() as session:
        users = list((await session.execute(select(User))).scalars())
    # admin de bootstrap + nouvel arrivant
    assert len(users) == 2


async def test_login_complet_admin_garde_son_role(
    flow: tuple[AsyncClient, FakeIdP, uuid.UUID], app: FastAPI
) -> None:
    client, idp, connector_id = flow
    # L'admin de bootstrap porte gael@example.org : on revient par OIDC avec cet email.
    idp.set_next_user(sub="sub-admin", email="gael@example.org", email_verified=True)
    response = await _run_login(app, client, idp, connector_id)
    assert response.status_code == 303
    async with app.state.session_factory() as session:
        users = list((await session.execute(select(User))).scalars())
    assert len(users) == 1  # pas de doublon : rattaché au compte admin
    assert users[0].role == ROLE_ADMIN


async def test_login_complet_email_non_verifie_refuse(
    flow: tuple[AsyncClient, FakeIdP, uuid.UUID], app: FastAPI
) -> None:
    client, idp, connector_id = flow
    idp.set_next_user(sub="sub-x", email="neuf@example.org", email_verified=False)
    response = await _run_login(app, client, idp, connector_id)
    assert response.status_code == 403


async def test_deuxieme_login_email_change_meme_compte(
    flow: tuple[AsyncClient, FakeIdP, uuid.UUID], app: FastAPI
) -> None:
    client, idp, connector_id = flow
    idp.set_next_user(sub="sub-stable", email="personne@example.org", email_verified=True)
    await _run_login(app, client, idp, connector_id)
    await client.post("/logout")
    # L'IdP change l'email, mais garde le même sub.
    idp.set_next_user(sub="sub-stable", email="personne-renommee@example.org", email_verified=True)
    response = await _run_login(app, client, idp, connector_id)
    assert response.status_code == 303
    async with app.state.session_factory() as session:
        from ossie_studio.models import UserIdentity

        identities = list((await session.execute(select(UserIdentity))).scalars())
    assert len(identities) == 1  # retrouvé par (issuer, sub), aucun doublon
