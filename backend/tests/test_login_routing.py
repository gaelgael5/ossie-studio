"""Connexion en deux temps (ticket docflow 145b1414) : domaines et phrase sur
le connecteur, analyse de l'email sur l'écran de connexion."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.oidc.forms import email_domain, parse_login_routing
from tests.conftest import ADMIN_EMAIL, bootstrap_admin, db_fetchval, wire_fake_idp
from tests.fake_idp import FakeIdP


def test_domaines_normalises_sans_arobase_ni_doublon() -> None:
    form = parse_login_routing("@Pickup-Services.com, pickup-services.com ;\n@pickup.fr", " Hey ")
    assert form.errors == []
    assert form.email_domains == ["pickup-services.com", "pickup.fr"]
    assert form.login_prompt == "Hey"


@pytest.mark.parametrize("raw", ["pickup", "@", "pick up.com", "-x.com", "a@b.com"])
def test_domaine_invalide_refuse(raw: str) -> None:
    assert "email_domain_invalid" in parse_login_routing(raw, "").errors


def test_phrase_trop_longue_refusee() -> None:
    assert parse_login_routing("", "x" * 121).errors == ["login_prompt_too_long"]


def test_domaine_de_l_email() -> None:
    assert email_domain(" Jean@Pickup-Services.COM ") == "pickup-services.com"
    assert email_domain("sans-arobase") == ""


@pytest.fixture
async def admin_client(client: AsyncClient, app: FastAPI) -> AsyncClient:
    wire_fake_idp(app, FakeIdP().app())
    await bootstrap_admin(client)
    return client


async def _connector(client: AsyncClient, label: str, issuer: str, domains: str) -> str:
    slug = f"oidc-{label.lower()}"
    response = await client.post(
        "/api/secrets",
        json={"slug": slug, "label": slug, "category": "oidc", "value": "s3cret-value"},
    )
    assert response.status_code == 201, response.text
    response = await client.post(
        "/api/oidc/connectors",
        json={
            "label": label,
            "issuer": issuer,
            "client_id": "client-x",
            "client_secret_slug": slug,
            "email_domains": domains,
            "login_prompt": f"Continuez avec {label}",
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def test_creation_et_modification_des_domaines(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    cid = await _connector(
        admin_client, "Pickup", "http://idp.test/realms/test", "@pickup-services.com"
    )
    listed = (await admin_client.get("/api/oidc/connectors")).json()["connectors"][0]
    assert listed["email_domains"] == ["pickup-services.com"]
    assert listed["login_prompt"] == "Continuez avec Pickup"

    response = await admin_client.put(
        f"/api/oidc/connectors/{cid}/login-routing",
        json={"email_domains": "@pickup.fr @pickup.com", "login_prompt": ""},
    )
    assert response.status_code == 200, response.text
    assert await db_fetchval(app, "SELECT email_domains FROM oidc_connectors") == [
        "pickup.fr",
        "pickup.com",
    ]
    bad = await admin_client.put(
        f"/api/oidc/connectors/{cid}/login-routing", json={"email_domains": "pickup"}
    )
    assert bad.status_code == 400
    assert bad.json()["error_args"]["fields"] == ["email_domain_invalid"]
    # La modification est tracée dans le journal d'audit.
    assert (
        await db_fetchval(
            app, "SELECT count(*) FROM audit_log WHERE detail->>'change' = 'login_routing'"
        )
        == 1
    )


async def test_modifier_les_domaines_exige_le_droit(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    cid = await _connector(admin_client, "Pickup", "http://idp.test/realms/test", "pickup.fr")
    await client.post("/api/logout")
    response = await client.put(
        f"/api/oidc/connectors/{cid}/login-routing", json={"email_domains": "x.fr"}
    )
    assert response.status_code == 401


async def _analyze(client: AsyncClient, email: str) -> Any:
    response = await client.post("/api/auth/login/analyze", json={"email": email})
    assert response.status_code == 200, response.text
    return response.json()


async def test_domaine_inconnu_demande_le_mot_de_passe_meme_sans_compte(
    admin_client: AsyncClient,
) -> None:
    await _connector(admin_client, "Pickup", "http://idp.test/realms/test", "pickup-services.com")
    await admin_client.post("/api/logout")
    # Compte existant ou non : même réponse, l'écran ne révèle pas qui a un compte.
    assert await _analyze(admin_client, ADMIN_EMAIL) == {"next": "password", "connectors": []}
    assert await _analyze(admin_client, "personne@inconnu.org") == {
        "next": "password",
        "connectors": [],
    }
    assert (await _analyze(admin_client, "pas-un-email"))["next"] == "password"


async def test_domaine_connu_sans_mot_de_passe_va_vers_oidc(admin_client: AsyncClient) -> None:
    cid = await _connector(
        admin_client, "Pickup", "http://idp.test/realms/test", "pickup-services.com"
    )
    body = await _analyze(admin_client, "Jean@Pickup-Services.com")
    assert body == {
        "next": "oidc",
        "connectors": [{"id": cid, "label": "Pickup", "prompt": "Continuez avec Pickup"}],
    }


async def test_domaine_connu_avec_mot_de_passe_propose_les_deux(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    # Le premier administrateur a un mot de passe local ; son domaine devient fédéré.
    domain = ADMIN_EMAIL.split("@")[1]
    await _connector(admin_client, "Pickup", "http://idp.test/realms/test", domain)
    # Second connecteur sur le même domaine, posé en base : le faux IdP ne sert
    # qu'un issuer et la création interroge la découverte.
    pool = app.state.pool
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO oidc_connectors (label, issuer, client_id, client_secret_slug,"
            " slug, email_domains)"
            " VALUES ('Autre', 'http://other.test', 'c', 'oidc-pickup', 'autre', $1)",
            [domain],
        )
    body = await _analyze(admin_client, ADMIN_EMAIL)
    assert body["next"] == "password_oidc"
    assert [c["label"] for c in body["connectors"]] == ["Autre", "Pickup"]


async def test_login_hint_transmis_au_fournisseur(admin_client: AsyncClient) -> None:
    cid = await _connector(admin_client, "Pickup", "http://idp.test/realms/test", "pickup.fr")
    start = await admin_client.get(
        f"/auth/oidc/{cid}/login", params={"login_hint": "jean@pickup.fr", "next": "/sources"}
    )
    assert start.status_code == 303
    query = parse_qs(urlparse(start.headers["location"]).query)
    assert query["login_hint"] == ["jean@pickup.fr"]
