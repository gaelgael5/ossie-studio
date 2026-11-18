from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.conftest import bootstrap_admin, db_fetchval, wire_fake_idp
from tests.fake_idp import FakeIdP, split_query

ISSUER = "http://idp.test/realms/test"
SECRET = "client-secret-ultra-confidentiel"


@pytest.fixture
def idp() -> FakeIdP:
    return FakeIdP()


@pytest.fixture
async def ready(
    client: AsyncClient, app: FastAPI, idp: FakeIdP
) -> tuple[AsyncClient, FakeIdP, uuid.UUID]:
    wire_fake_idp(app, idp.app())
    await bootstrap_admin(client)
    await client.post(
        "/api/secrets",
        json={"slug": "oidc-sec", "label": "Secret IdP", "category": "oidc", "value": SECRET},
    )
    await client.post(
        "/api/oidc/connectors",
        json={
            "label": "Mon IdP",
            "issuer": ISSUER,
            "client_id": "client-x",
            "client_secret_slug": "oidc-sec",
            "scopes": "openid email profile",
        },
    )
    connector_id = await db_fetchval(app, "SELECT id FROM oidc_connectors")
    await client.post("/api/logout")
    return client, idp, connector_id


async def _authz_redirect(idp: FakeIdP, authz_url: str) -> str:
    async with AsyncClient(
        transport=ASGITransport(app=idp.app()), base_url="http://idp.test"
    ) as idp_client:
        redirect = await idp_client.get(authz_url)
    return redirect.headers["location"]


async def test_id_token_iss_incoherent_refuse(
    ready: tuple[AsyncClient, FakeIdP, uuid.UUID],
) -> None:
    """Un id_token signé avec un iss différent de l'issuer déclaré → rejet."""
    client, idp, connector_id = ready
    idp.sign_issuer = "http://attaquant.test/realms/evil"
    idp.set_next_user(sub="s1", email="x@example.org", email_verified=True)
    start = await client.get(f"/auth/oidc/{connector_id}/login")
    callback_url = await _authz_redirect(idp, start.headers["location"])
    response = await client.get(callback_url)
    assert response.status_code == 303
    assert split_query(response.headers["location"])["error"] == "oidc_token_rejected"


async def test_callback_state_invalide_refuse(
    ready: tuple[AsyncClient, FakeIdP, uuid.UUID],
) -> None:
    """Un callback dont le state ne correspond pas au flux en session → rejet."""
    client, idp, connector_id = ready
    idp.set_next_user(sub="s1", email="x@example.org", email_verified=True)
    start = await client.get(f"/auth/oidc/{connector_id}/login")
    callback_url = await _authz_redirect(idp, start.headers["location"])
    params = split_query(callback_url)
    forged = f"/auth/oidc/{connector_id}/callback?code={params['code']}&state=state-force"
    response = await client.get(forged)
    assert response.status_code == 303
    assert split_query(response.headers["location"])["error"] == "oidc_flow_invalid"


async def test_callback_sans_flux_en_session_refuse(
    ready: tuple[AsyncClient, FakeIdP, uuid.UUID],
) -> None:
    """Appeler le callback directement, sans avoir initié le login → rejet."""
    client, _idp, connector_id = ready
    response = await client.get(f"/auth/oidc/{connector_id}/callback?code=abc&state=xyz")
    assert response.status_code == 303
    assert split_query(response.headers["location"])["error"] == "oidc_flow_invalid"
