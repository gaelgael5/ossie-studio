from __future__ import annotations

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


async def _add_connector(client: AsyncClient, issuer: str = ISSUER, label: str = "Mon IdP") -> None:
    response = await client.post(
        "/oidc/connectors",
        data={
            "label": label,
            "issuer": issuer,
            "client_id": "client-x",
            "client_secret": SECRET,
            "scopes": "openid email profile",
        },
    )
    assert response.status_code == 303, response.text


async def test_ajout_interroge_la_decouverte_et_enregistre(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    await _add_connector(admin_client)
    row = await db_fetchrow(app, "SELECT issuer, client_secret_encrypted FROM oidc_connectors")
    assert row is not None
    assert row["issuer"] == ISSUER
    assert SECRET.encode() not in row["client_secret_encrypted"]


async def test_issuer_injoignable_refuse_rien_enregistre(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    response = await admin_client.post(
        "/oidc/connectors",
        data={
            "label": "Cassé",
            "issuer": "http://idp.test/realms/inexistant",
            "client_id": "client-x",
            "client_secret": SECRET,
            "scopes": "openid email profile",
        },
    )
    assert response.status_code == 400
    count = await db_fetchval(app, "SELECT count(*) FROM oidc_connectors")
    assert count == 0


async def test_deux_connecteurs_meme_issuer_refuse(admin_client: AsyncClient, app: FastAPI) -> None:
    await _add_connector(admin_client)
    response = await admin_client.post(
        "/oidc/connectors",
        data={
            "label": "Doublon",
            "issuer": ISSUER,
            "client_id": "autre",
            "client_secret": SECRET,
            "scopes": "openid email profile",
        },
    )
    assert response.status_code == 409
    count = await db_fetchval(app, "SELECT count(*) FROM oidc_connectors")
    assert count == 1


@pytest.mark.parametrize("missing", ["label", "issuer", "client_id", "client_secret"])
async def test_champ_obligatoire_vide_refuse(
    admin_client: AsyncClient, app: FastAPI, missing: str
) -> None:
    data = {
        "label": "X",
        "issuer": ISSUER,
        "client_id": "client-x",
        "client_secret": SECRET,
        "scopes": "openid email profile",
    }
    data[missing] = ""
    response = await admin_client.post("/oidc/connectors", data=data)
    assert response.status_code == 400
    count = await db_fetchval(app, "SELECT count(*) FROM oidc_connectors")
    assert count == 0


async def test_bouton_apparait_sur_login_sans_redemarrage(admin_client: AsyncClient) -> None:
    await _add_connector(admin_client, label="Mon IdP")
    await admin_client.post("/logout")
    page = await admin_client.get("/login")
    assert "Se connecter avec Mon IdP" in page.text


async def test_secret_absent_de_la_liste(admin_client: AsyncClient) -> None:
    await _add_connector(admin_client)
    page = await admin_client.get("/oidc/connectors")
    assert page.status_code == 200
    assert SECRET not in page.text


async def test_suppression_dernier_connecteur_possible(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    await _add_connector(admin_client)
    connector_id = await db_fetchval(app, "SELECT id FROM oidc_connectors")
    response = await admin_client.post(f"/oidc/connectors/{connector_id}/delete")
    assert response.status_code == 303
    count = await db_fetchval(app, "SELECT count(*) FROM oidc_connectors")
    assert count == 0
    # Le login local reste ouvert : la page de connexion répond.
    assert (await admin_client.get("/login")).status_code == 200


async def test_suppression_connecteur_inexistant_404(admin_client: AsyncClient) -> None:
    import uuid

    response = await admin_client.post(f"/oidc/connectors/{uuid.uuid4()}/delete")
    assert response.status_code == 404


@pytest.fixture
async def user_client(admin_client: AsyncClient, app: FastAPI) -> AsyncIterator[AsyncClient]:
    await create_plain_user(app, "simple@example.org", "mot-de-passe-simple")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as second:
        await login(second, "simple@example.org", "mot-de-passe-simple")
        yield second


async def test_non_admin_refus_403(user_client: AsyncClient) -> None:
    import uuid

    assert (await user_client.get("/oidc/connectors")).status_code == 403
    creation = await user_client.post(
        "/oidc/connectors",
        data={
            "label": "X",
            "issuer": ISSUER,
            "client_id": "c",
            "client_secret": "s",
            "scopes": "openid",
        },
    )
    assert creation.status_code == 403
    assert (await user_client.post(f"/oidc/connectors/{uuid.uuid4()}/delete")).status_code == 403
