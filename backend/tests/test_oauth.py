"""Connexion OAuth d'un client MCP avec choix du profil agent : Ossie-studio est
son propre serveur d'autorisation (découverte, enregistrement dynamique, PKCE,
consentement, jetons opaques, rafraîchissement, révocation)."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx2
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mcp import Client
from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from ossie_studio.mcp_surface.catalog import CATALOG, READ_PRIMITIVES
from ossie_studio.oidc.redirect import safe_next
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, bootstrap_admin, create_plain_user, login
from tests.mcp_helpers import tool_names

BASE = "https://ossie.example.org"
# Primitives qui ne supposent que la lecture des espaces.
SPACES_READ = {p.id for p in CATALOG if p.capability == "spaces:read"}
RESOURCE = f"{BASE}/mcp"
REDIRECT = "http://localhost:3118/callback"
PASSWORD = "motdepasse-solide"


# --- Outillage ------------------------------------------------------------------------


@pytest.fixture
async def admin(app: FastAPI, client: AsyncClient) -> AsyncClient:
    """Administrateur connecté à l'IHM ; Ossie-studio publié en HTTPS."""
    app.state.settings.public_base_url = BASE
    await bootstrap_admin(client)
    await login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    return client


@asynccontextmanager
async def _as(app: FastAPI, email: str) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as browser:
        await login(browser, email, PASSWORD)
        yield browser


async def _person(app: FastAPI, email: str, caps: list[str]) -> uuid.UUID:
    await create_plain_user(app, email, PASSWORD)
    async with app.state.pool.acquire() as conn:
        profile_id = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) VALUES ($1, $2) RETURNING id",
            f"profil-{email}",
            caps,
        )
        user_id = await conn.fetchval(
            "UPDATE users SET profile_id = $2 WHERE email = $1 RETURNING id", email, profile_id
        )
    assert isinstance(user_id, uuid.UUID)
    return user_id


async def _set_caps(app: FastAPI, user_id: uuid.UUID, caps: list[str]) -> None:
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "UPDATE profiles SET capabilities = $2 "
            "WHERE id = (SELECT profile_id FROM users WHERE id = $1)",
            user_id,
            caps,
        )


async def _profile_id(app: FastAPI, name: str = "Lecture") -> str:
    async with app.state.pool.acquire() as conn:
        return str(await conn.fetchval("SELECT id FROM agent_profiles WHERE name = $1", name))


async def _agent_profile(app: FastAPI, name: str, primitives: list[str]) -> str:
    async with app.state.pool.acquire() as conn:
        return str(
            await conn.fetchval(
                "INSERT INTO agent_profiles (name, primitives) VALUES ($1, $2) RETURNING id",
                name,
                primitives,
            )
        )


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


async def _register(http: AsyncClient) -> str:
    response = await http.post(
        f"{BASE}/register",
        json={
            "client_name": "Claude (essai)",
            "redirect_uris": [REDIRECT],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["client_id"])


@dataclass
class Flow:
    client_id: str
    verifier: str
    request_id: str


async def _authorize(http: AsyncClient, client_id: str | None = None, **extra: str) -> Flow:
    """Client → /authorize → renvoi vers l'écran de consentement de l'IHM."""
    client_id = client_id or await _register(http)
    verifier = secrets.token_urlsafe(48)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
        "state": "etat-1",
        "resource": RESOURCE,
        **extra,
    }
    response = await http.get(f"{BASE}/authorize", params=params)
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith(f"{BASE}/oauth/consent?request=")
    return Flow(client_id, verifier, _query(location)["request"])


async def _approve(browser: AsyncClient, flow: Flow, profile_id: str) -> str:
    response = await browser.post(
        f"/api/oauth/requests/{flow.request_id}/approve", json={"agent_profile_id": profile_id}
    )
    assert response.status_code == 200, response.text
    redirect = response.json()["redirect_url"]
    assert redirect.startswith(REDIRECT)
    assert _query(redirect)["state"] == "etat-1"
    return _query(redirect)["code"]


async def _exchange(http: AsyncClient, flow: Flow, code: str) -> httpx2.Response | Any:
    return await http.post(
        f"{BASE}/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": flow.client_id,
            "code_verifier": flow.verifier,
        },
    )


async def _connect(app: FastAPI, browser: AsyncClient, profile_id: str) -> dict[str, Any]:
    """Flux complet sans le client du SDK : rend les jetons et le client."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as http:
        flow = await _authorize(http)
        code = await _approve(browser, flow, profile_id)
        response = await _exchange(http, flow, code)
    assert response.status_code == 200, response.text
    return {**response.json(), "client_id": flow.client_id}


async def _refresh(app: FastAPI, client_id: str, refresh_token: str) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as http:
        return await http.post(
            f"{BASE}/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
        )


async def _mcp_status(app: FastAPI, token: str) -> tuple[int, str]:
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE) as http:
        response = await http.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
    return response.status_code, response.headers.get("www-authenticate", "")


def _events(out: str, event: str) -> list[dict[str, Any]]:
    entries = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    return [e for e in entries if e.get("event") == event]


class _Memory(TokenStorage):
    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client = client_info


# --- Le client MCP standard du SDK, de bout en bout -----------------------------------


async def test_client_mcp_standard_decouvre_s_enregistre_et_obtient_un_jeton(
    app: FastAPI, admin: AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    lecture = await _profile_id(app)
    seen: dict[str, str] = {}

    async def redirect_handler(authorization_url: str) -> None:
        # Le navigateur de la personne : /authorize → consentement → retour au client.
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as browser:
            response = await browser.get(authorization_url)
        assert response.status_code == 302
        request_id = _query(response.headers["location"])["request"]
        consent = (await admin.get(f"/api/oauth/requests/{request_id}")).json()
        assert consent["client"]["name"] == "Claude (essai)"
        assert [p["name"] for p in consent["agent_profiles"]] == ["Lecture"]
        approved = await admin.post(
            f"/api/oauth/requests/{request_id}/approve", json={"agent_profile_id": lecture}
        )
        seen.update(_query(approved.json()["redirect_url"]))

    async def callback_handler() -> AuthorizationCodeResult:
        return AuthorizationCodeResult(code=seen["code"], state=seen.get("state"))

    storage = _Memory()
    auth = OAuthClientProvider(
        server_url=RESOURCE,
        client_metadata=OAuthClientMetadata(
            client_name="Claude (essai)",
            redirect_uris=[REDIRECT],  # type: ignore[list-item]
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        ),
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
    capsys.readouterr()
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), auth=auth) as http:
        async with Client(streamable_http_client(RESOURCE, http_client=http)) as mcp:
            tools = [t.name for t in (await mcp.list_tools()).tools]
            result = await mcp.call_tool("list_spaces", {})

    assert set(tools) == READ_PRIMITIVES
    assert not result.is_error
    assert storage.tokens is not None and storage.tokens.access_token.startswith("osa_")
    out = capsys.readouterr().out
    # Aucun jeton ni code dans les journaux.
    for secret in (storage.tokens.access_token, storage.tokens.refresh_token or "x", seen["code"]):
        assert secret not in out
    assert [e["outcome"] for e in _events(out, "mcp_call")] == ["ok"]


# --- Découverte -----------------------------------------------------------------------


async def test_documents_de_decouverte(app: FastAPI, admin: AsyncClient) -> None:
    prm = (await admin.get(f"{BASE}/.well-known/oauth-protected-resource/mcp")).json()
    assert prm["resource"] == RESOURCE
    assert prm["authorization_servers"] == [BASE]
    asm = (await admin.get(f"{BASE}/.well-known/oauth-authorization-server")).json()
    assert asm["issuer"] == BASE
    assert asm["authorization_endpoint"] == f"{BASE}/authorize"
    assert asm["token_endpoint"] == f"{BASE}/token"
    assert asm["registration_endpoint"] == f"{BASE}/register"
    assert asm["code_challenge_methods_supported"] == ["S256"]
    assert "implicit" not in json.dumps(asm.get("grant_types_supported"))


async def test_sans_adresse_https_pas_de_serveur_d_autorisation(client: AsyncClient) -> None:
    response = await client.get("/.well-known/oauth-authorization-server")
    assert response.status_code == 404


# --- Consentement ---------------------------------------------------------------------


async def test_refuser_le_consentement_n_emet_aucun_jeton(app: FastAPI, admin: AsyncClient) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as http:
        flow = await _authorize(http)
    response = await admin.post(f"/api/oauth/requests/{flow.request_id}/deny")
    redirect = response.json()["redirect_url"]
    assert _query(redirect) == {"error": "access_denied", "state": "etat-1"}
    async with app.state.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM oauth_codes") == 0
        assert await conn.fetchval("SELECT count(*) FROM oauth_tokens") == 0
    # La demande ne sert qu'une fois.
    again = await admin.post(
        f"/api/oauth/requests/{flow.request_id}/approve",
        json={"agent_profile_id": await _profile_id(app)},
    )
    assert again.status_code == 404


async def test_consentement_exige_une_personne_connectee(app: FastAPI, admin: AsyncClient) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as http:
        flow = await _authorize(http)
        anonymous = await http.get(f"/api/oauth/requests/{flow.request_id}")
    assert anonymous.status_code == 401


async def test_demande_liee_a_la_premiere_personne_qui_l_ouvre(
    app: FastAPI, admin: AsyncClient
) -> None:
    await _person(app, "ana@example.org", ["spaces:read"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as http:
        flow = await _authorize(http)
    assert (await admin.get(f"/api/oauth/requests/{flow.request_id}")).status_code == 200
    async with _as(app, "ana@example.org") as ana:
        assert (await ana.get(f"/api/oauth/requests/{flow.request_id}")).status_code == 404


async def test_seuls_les_profils_utilisables_sont_proposes(
    app: FastAPI, admin: AsyncClient
) -> None:
    user_id = await _person(app, "ana@example.org", [])
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as http:
        flow = await _authorize(http)
    async with _as(app, "ana@example.org") as ana:
        consent = (await ana.get(f"/api/oauth/requests/{flow.request_id}")).json()
        assert consent["agent_profiles"] == []
        refused = await ana.post(
            f"/api/oauth/requests/{flow.request_id}/approve",
            json={"agent_profile_id": await _profile_id(app)},
        )
        assert refused.status_code == 403 and refused.json()["error"] == "agent_profile_not_usable"

        await _set_caps(app, user_id, ["spaces:read"])
        consent = (await ana.get(f"/api/oauth/requests/{flow.request_id}")).json()
        assert [(p["name"], p["granted"]) for p in consent["agent_profiles"]] == [
            ("Lecture", sorted(SPACES_READ))
        ]


# --- Droits de l'agent : profil ∩ capacités de la personne ----------------------------


async def test_capacite_retiree_a_la_personne_retiree_a_l_agent(
    app: FastAPI, admin: AsyncClient
) -> None:
    user_id = await _person(app, "ana@example.org", ["spaces:read"])
    async with _as(app, "ana@example.org") as ana:
        tokens = await _connect(app, ana, await _profile_id(app))
    access = tokens["access_token"]
    assert set(await tool_names(app, access)) == SPACES_READ

    await _set_caps(app, user_id, [])
    assert await tool_names(app, access) == []


# --- Jetons : audience, expiration, rafraîchissement, usage unique --------------------


async def test_jeton_d_une_autre_audience_refuse_avec_motif(
    app: FastAPI, admin: AsyncClient
) -> None:
    tokens = await _connect(app, admin, await _profile_id(app))
    async with app.state.pool.acquire() as conn:
        await conn.execute("UPDATE oauth_tokens SET resource = 'https://autre.example.org/mcp'")
    status, header = await _mcp_status(app, tokens["access_token"])
    assert status == 401
    assert 'error_description="token_audience_mismatch"' in header


async def test_jeton_expire_401_puis_rafraichissement(app: FastAPI, admin: AsyncClient) -> None:
    tokens = await _connect(app, admin, await _profile_id(app))
    assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 3600
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "UPDATE oauth_tokens SET expires_at = now() - interval '1 second' WHERE kind = 'access'"
        )
    status, header = await _mcp_status(app, tokens["access_token"])
    assert status == 401 and 'error_description="token_expired"' in header

    refreshed = await _refresh(app, tokens["client_id"], tokens["refresh_token"])
    assert refreshed.status_code == 200, refreshed.text
    assert (await _mcp_status(app, refreshed.json()["access_token"]))[0] == 200
    # Rotation : l'ancien jeton de rafraîchissement ne resert pas.
    replay = await _refresh(app, tokens["client_id"], tokens["refresh_token"])
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"


async def test_code_d_autorisation_a_usage_unique(app: FastAPI, admin: AsyncClient) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as http:
        flow = await _authorize(http)
        code = await _approve(admin, flow, await _profile_id(app))
        assert (await _exchange(http, flow, code)).status_code == 200
        second = await _exchange(http, flow, code)
    assert second.status_code == 400 and second.json()["error"] == "invalid_grant"


async def test_code_verifier_faux_refuse(app: FastAPI, admin: AsyncClient) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as http:
        flow = await _authorize(http)
        code = await _approve(admin, flow, await _profile_id(app))
        flow.verifier = secrets.token_urlsafe(48)
        response = await _exchange(http, flow, code)
    assert response.status_code == 400 and response.json()["error"] == "invalid_grant"


# --- PKCE, redirect_uri, audience demandée --------------------------------------------


async def test_pkce_absent_ou_non_s256_et_redirect_uri_inconnue_refuses(
    app: FastAPI, admin: AsyncClient
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as http:
        client_id = await _register(http)
        base = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "state": "s",
        }
        no_pkce = await http.get(f"{BASE}/authorize", params=base)
        plain = await http.get(
            f"{BASE}/authorize",
            params={**base, "code_challenge": "abc", "code_challenge_method": "plain"},
        )
        unknown_redirect = await http.get(
            f"{BASE}/authorize",
            params={
                **base,
                "redirect_uri": "https://attaquant.example.org/cb",
                "code_challenge": _challenge("v" * 50),
                "code_challenge_method": "S256",
            },
        )
        other_resource = await http.get(
            f"{BASE}/authorize",
            params={
                **base,
                "code_challenge": _challenge("v" * 50),
                "code_challenge_method": "S256",
                "resource": "https://autre.example.org/mcp",
            },
        )
    for response in (no_pkce, plain, other_resource):
        assert response.status_code == 302
        assert "error" in _query(response.headers["location"])
        assert response.headers["location"].startswith(REDIRECT)
    assert _query(other_resource.headers["location"])["error"] == "invalid_target"
    # redirect_uri non enregistrée : jamais de renvoi vers elle.
    assert unknown_redirect.status_code == 400
    async with app.state.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM oauth_authorization_requests") == 0


async def test_codes_et_jetons_stockes_par_empreinte(app: FastAPI, admin: AsyncClient) -> None:
    tokens = await _connect(app, admin, await _profile_id(app))
    async with app.state.pool.acquire() as conn:
        stored = [r["token_hash"] for r in await conn.fetch("SELECT token_hash FROM oauth_tokens")]
        codes = [r["code_hash"] for r in await conn.fetch("SELECT code_hash FROM oauth_codes")]
    assert tokens["access_token"] not in stored and tokens["refresh_token"] not in stored
    assert hashlib.sha256(tokens["access_token"].encode()).hexdigest() in stored
    assert len(codes) == 1 and len(codes[0]) == 64


# --- Mes connexions agents : révoquer, changer de profil ------------------------------


async def test_revoquer_une_connexion_refuse_jeton_et_rafraichissement(
    app: FastAPI, admin: AsyncClient
) -> None:
    tokens = await _connect(app, admin, await _profile_id(app))
    mine = (await admin.get("/api/agent-connections")).json()["connections"]
    assert [(c["client"]["name"], c["agent_profile"]["name"], c["state"]) for c in mine] == [
        ("Claude (essai)", "Lecture", "active")
    ]

    revoked = await admin.post(f"/api/agent-connections/{mine[0]['id']}/revoke")
    assert revoked.json()["state"] == "revoked"
    assert (await _mcp_status(app, tokens["access_token"]))[0] == 401
    refresh = await _refresh(app, tokens["client_id"], tokens["refresh_token"])
    assert refresh.status_code == 400
    # Rejouable sans effet.
    assert (await admin.post(f"/api/agent-connections/{mine[0]['id']}/revoke")).status_code == 200


async def test_changer_le_profil_d_une_connexion_vaut_des_l_appel_suivant(
    app: FastAPI, admin: AsyncClient
) -> None:
    tokens = await _connect(app, admin, await _profile_id(app))
    connection_id = (await admin.get("/api/agent-connections")).json()["connections"][0]["id"]
    reperage = await _agent_profile(app, "Repérage", ["list_spaces"])

    changed = await admin.put(
        f"/api/agent-connections/{connection_id}/profile", json={"agent_profile_id": reperage}
    )
    assert changed.json()["agent_profile"]["name"] == "Repérage"
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "UPDATE agent_profiles SET primitives = '{}' WHERE id = $1", uuid.UUID(reperage)
        )
    assert await tool_names(app, tokens["access_token"]) == []

    empty = await _agent_profile(app, "Vide", [])
    refused = await admin.put(
        f"/api/agent-connections/{connection_id}/profile", json={"agent_profile_id": empty}
    )
    assert refused.status_code == 403 and refused.json()["error"] == "agent_profile_not_usable"


async def test_chacun_ses_connexions_l_administration_les_voit_toutes(
    app: FastAPI, admin: AsyncClient
) -> None:
    await _person(app, "ana@example.org", ["spaces:read"])
    await _person(app, "bob@example.org", ["spaces:read"])
    async with _as(app, "ana@example.org") as ana:
        await _connect(app, ana, await _profile_id(app))
        ana_connection = (await ana.get("/api/agent-connections")).json()["connections"][0]["id"]
        assert (await ana.get("/api/agent-connections/all")).status_code == 403
    async with _as(app, "bob@example.org") as bob:
        assert (await bob.get("/api/agent-connections")).json()["connections"] == []
        revoke = await bob.post(f"/api/agent-connections/{ana_connection}/revoke")
        assert revoke.status_code == 404
        assert (
            await bob.put(
                f"/api/agent-connections/{ana_connection}/profile",
                json={"agent_profile_id": await _profile_id(app)},
            )
        ).status_code == 404

    everyone = (await admin.get("/api/agent-connections/all")).json()["connections"]
    assert [c["user"] for c in everyone] == ["ana@example.org"]
    revoked = await admin.post(f"/api/agent-connections/{ana_connection}/revoke")
    assert revoked.status_code == 200 and revoked.json()["state"] == "revoked"


async def test_consentement_revocation_changement_journalises(
    app: FastAPI, admin: AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    await _connect(app, admin, await _profile_id(app))
    connection_id = (await admin.get("/api/agent-connections")).json()["connections"][0]["id"]
    reperage = await _agent_profile(app, "Repérage", ["list_spaces"])
    await admin.put(
        f"/api/agent-connections/{connection_id}/profile", json={"agent_profile_id": reperage}
    )
    await admin.post(f"/api/agent-connections/{connection_id}/revoke")
    out = capsys.readouterr().out

    granted = _events(out, "oauth_consent_granted")
    assert len(granted) == 1 and granted[0]["author"] == ADMIN_EMAIL
    assert granted[0]["client_name"] == "Claude (essai)"
    changed = _events(out, "oauth_connection_profile_changed")
    assert len(changed) == 1 and changed[0]["agent_profile_id"] == reperage
    revoked = _events(out, "oauth_connection_revoked")
    assert len(revoked) == 1 and revoked[0]["author"] == ADMIN_EMAIL


# --- Retour après connexion -----------------------------------------------------------


def test_retour_apres_connexion_reste_dans_l_application() -> None:
    assert safe_next("/oauth/consent?request=abc") == "/oauth/consent?request=abc"
    for bad in ("", "https://evil.example.org", "//evil.example.org", "/\\evil", "/a\nb"):
        assert safe_next(bad) is None


# --- Ménage : connexions révoquées et données périmées ------------------------------


async def test_connexion_revoquee_supprimee_au_bout_de_24_heures(
    app: FastAPI, admin: AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    from ossie_studio.app import _purge_once

    await _person(app, "ana@example.org", ["spaces:read"])
    await _person(app, "bob@example.org", ["spaces:read"])
    lecture = await _profile_id(app)
    await _connect(app, admin, lecture)  # active : reste
    async with _as(app, "ana@example.org") as ana:
        await _connect(app, ana, lecture)
    async with _as(app, "bob@example.org") as bob:
        await _connect(app, bob, lecture)
    # Une demande de connexion jamais consentie, expirée.
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as http:
        await _authorize(http)
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "UPDATE oauth_connections c SET revoked_at = now() - $2::interval "
            "FROM users u WHERE u.id = c.user_id AND u.email = $1",
            "ana@example.org",
            timedelta(hours=25),
        )
        await conn.execute(
            "UPDATE oauth_connections c SET revoked_at = now() - $2::interval "
            "FROM users u WHERE u.id = c.user_id AND u.email = $1",
            "bob@example.org",
            timedelta(hours=23),
        )
        await conn.execute("UPDATE oauth_authorization_requests SET expires_at = now()")
        await conn.execute("UPDATE oauth_codes SET expires_at = now() - interval '1 minute'")
    capsys.readouterr()

    await _purge_once(app)

    everyone = (await admin.get("/api/agent-connections/all")).json()["connections"]
    assert sorted(c["user"] for c in everyone) == sorted([ADMIN_EMAIL, "bob@example.org"])
    async with app.state.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM oauth_authorization_requests") == 0
        assert await conn.fetchval("SELECT count(*) FROM oauth_codes") == 0
        # Les jetons de la connexion supprimée partent avec elle.
        orphans = await conn.fetchval(
            "SELECT count(*) FROM oauth_tokens t LEFT JOIN oauth_connections c "
            "ON c.id = t.connection_id WHERE c.id IS NULL"
        )
        assert orphans == 0
    purged = _events(capsys.readouterr().out, "oauth_purged")
    assert len(purged) == 1
    assert purged[0]["connections"] == 1 and purged[0]["requests"] == 1 and purged[0]["codes"] == 3


async def test_jetons_perimes_depuis_plus_de_24_heures_supprimes(
    app: FastAPI, admin: AsyncClient
) -> None:
    from ossie_studio.app import _purge_once

    tokens = await _connect(app, admin, await _profile_id(app))
    refreshed = await _refresh(app, tokens["client_id"], tokens["refresh_token"])
    assert refreshed.status_code == 200
    async with app.state.pool.acquire() as conn:
        # Les premiers jetons : l'accès a expiré il y a 2 jours, le rafraîchissement a été
        # tourné (révoqué) il y a 2 jours.
        await conn.execute(
            "UPDATE oauth_tokens SET expires_at = now() - interval '2 days' "
            "WHERE token_hash = $1",
            hashlib.sha256(tokens["access_token"].encode()).hexdigest(),
        )
        await conn.execute(
            "UPDATE oauth_tokens SET revoked_at = now() - interval '2 days' "
            "WHERE token_hash = $1",
            hashlib.sha256(tokens["refresh_token"].encode()).hexdigest(),
        )
        before = await conn.fetchval("SELECT count(*) FROM oauth_tokens")

    await _purge_once(app)

    async with app.state.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM oauth_tokens") == before - 2
    # Les jetons en cours continuent de servir.
    assert (await _mcp_status(app, refreshed.json()["access_token"]))[0] == 200
