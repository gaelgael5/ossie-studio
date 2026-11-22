"""Clés d'API de service : émission avec un profil agent, accès MCP, changement
de profil, révocation, garde contre l'élévation, isolement de l'IHM, journal."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.api_keys.service import KEY_PREFIX, key_hash
from ossie_studio.mcp_surface.catalog import PERSON_ONLY, READ_PRIMITIVES
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, bootstrap_admin, create_plain_user, login
from tests.mcp_helpers import call_tool, raw_mcp_status, tool_names

PASSWORD = "motdepasse-solide"
# Ce qu'un profil « toutes les lectures » donne à une clé (sans personne, sans brouillon).
KEY_READS = READ_PRIMITIVES - PERSON_ONLY


# --- Outillage ------------------------------------------------------------------------


async def _admin(client: AsyncClient) -> None:
    await bootstrap_admin(client)
    await login(client, ADMIN_EMAIL, ADMIN_PASSWORD)


async def _profile_id(app: FastAPI, slug: str = "lecture") -> str:
    async with app.state.pool.acquire() as conn:
        return str(await conn.fetchval("SELECT id FROM agent_profiles WHERE slug = $1", slug))


async def _agent_profile(app: FastAPI, name: str, primitives: list[str]) -> str:
    async with app.state.pool.acquire() as conn:
        return str(
            await conn.fetchval(
                "INSERT INTO agent_profiles (name, primitives) VALUES ($1, $2) RETURNING id",
                name,
                primitives,
            )
        )


async def _issue(client: AsyncClient, profile_id: str, label: str = "Claude Code") -> Any:
    response = await client.post(
        "/api/api-keys", json={"label": label, "agent_profile_id": profile_id}
    )
    assert response.status_code == 201, response.text
    return response


async def _person(app: FastAPI, email: str, caps: list[str]) -> None:
    await create_plain_user(app, email, PASSWORD)
    async with app.state.pool.acquire() as conn:
        profile_id = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) VALUES ($1, $2) RETURNING id",
            f"profil-{email}",
            caps,
        )
        await conn.execute("UPDATE users SET profile_id = $2 WHERE email = $1", email, profile_id)


@asynccontextmanager
async def _as(app: FastAPI, email: str) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, email, PASSWORD)
        yield client


def _events(out: str, event: str) -> list[dict[str, Any]]:
    entries = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    return [e for e in entries if e.get("event") == event]


# --- Émission -------------------------------------------------------------------------


async def test_cle_rendue_une_seule_fois_puis_prefixe_seulement(
    app: FastAPI, client: AsyncClient
) -> None:
    await _admin(client)
    response = await _issue(client, await _profile_id(app))

    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    secret = body["secret"]
    assert secret.startswith(KEY_PREFIX) and len(secret) == len(KEY_PREFIX) + 43
    assert body["key"]["prefix"] == secret[:12]
    assert body["key"]["agent_profile"]["name"] == "Lecture"
    assert body["key"]["state"] == "active"
    assert body["key"]["created_by"] == ADMIN_EMAIL

    listing = await client.get("/api/api-keys")
    assert secret not in listing.text
    assert [k["label"] for k in listing.json()["keys"]] == ["Claude Code"]

    async with app.state.pool.acquire() as conn:
        stored = await conn.fetchrow("SELECT * FROM api_keys")
    assert stored["key_hash"] == key_hash(secret)
    assert secret not in [str(v) for v in stored.values()]


async def test_libelle_et_profil_obligatoires(app: FastAPI, client: AsyncClient) -> None:
    await _admin(client)
    no_label = await client.post(
        "/api/api-keys", json={"label": " ", "agent_profile_id": await _profile_id(app)}
    )
    assert no_label.json()["error_args"]["fields"] == ["label_required"]
    no_profile = await client.post("/api/api-keys", json={"label": "x", "agent_profile_id": ""})
    assert no_profile.json()["error_args"]["fields"] == ["agent_profile_required"]
    unknown = await client.post(
        "/api/api-keys", json={"label": "x", "agent_profile_id": str(uuid.uuid4())}
    )
    assert unknown.json()["error"] == "agent_profile_not_found"


# --- Accès MCP ------------------------------------------------------------------------


async def test_avec_la_cle_le_client_voit_exactement_le_profil(
    app: FastAPI, client: AsyncClient
) -> None:
    await _admin(client)
    secret = (await _issue(client, await _profile_id(app))).json()["secret"]

    assert set(await tool_names(app, secret)) == KEY_READS
    result = await call_tool(app, secret, "list_spaces")
    assert not result.is_error


async def test_changer_le_profil_vaut_des_l_appel_suivant(
    app: FastAPI, client: AsyncClient
) -> None:
    await _admin(client)
    body = (await _issue(client, await _profile_id(app))).json()
    secret, key_id = body["secret"], body["key"]["id"]
    assert await tool_names(app, secret) != []

    empty = await _agent_profile(app, "Vide", [])
    response = await client.put(f"/api/api-keys/{key_id}/profile", json={"agent_profile_id": empty})
    assert response.status_code == 200
    assert response.json()["agent_profile"]["name"] == "Vide"

    assert await tool_names(app, secret) == []


async def test_cle_revoquee_refusee_et_revocation_rejouable(
    app: FastAPI, client: AsyncClient
) -> None:
    await _admin(client)
    body = (await _issue(client, await _profile_id(app))).json()
    secret, key_id = body["secret"], body["key"]["id"]
    assert await raw_mcp_status(app, secret) == 200

    first = await client.post(f"/api/api-keys/{key_id}/revoke")
    assert first.status_code == 200 and first.json()["state"] == "revoked"
    assert await raw_mcp_status(app, secret) == 401

    again = await client.post(f"/api/api-keys/{key_id}/revoke")
    assert again.status_code == 200
    assert again.json()["revoked_at"] == first.json()["revoked_at"]

    change = await client.put(
        f"/api/api-keys/{key_id}/profile", json={"agent_profile_id": await _profile_id(app)}
    )
    assert change.status_code == 409 and change.json()["error"] == "api_key_revoked"


async def test_cle_inconnue_404(client: AsyncClient) -> None:
    await _admin(client)
    response = await client.post(f"/api/api-keys/{uuid.uuid4()}/revoke")
    assert response.status_code == 404 and response.json()["error"] == "api_key_not_found"


async def test_cle_inconnue_ou_bidon_401(app: FastAPI) -> None:
    assert await raw_mcp_status(app, KEY_PREFIX + "x" * 43) == 401
    assert await raw_mcp_status(app, "pas-une-cle") == 401


# --- Garde contre l'élévation ---------------------------------------------------------


async def test_profil_au_dela_de_ses_droits_refuse_en_nommant_la_primitive(
    app: FastAPI, client: AsyncClient
) -> None:
    await bootstrap_admin(client)
    await _person(app, "ana@example.org", ["api_keys:read", "api_keys:write"])
    lecture = await _profile_id(app)
    async with _as(app, "ana@example.org") as ana:
        refused = await ana.post("/api/api-keys", json={"label": "x", "agent_profile_id": lecture})
        assert refused.status_code == 403
        assert refused.json()["error"] == "agent_profile_beyond_rights"
        assert refused.json()["error_args"]["primitives"] == ", ".join(sorted(KEY_READS))

        options = (await ana.get("/api/api-keys/options")).json()
        by_name = {p["name"]: p for p in options["agent_profiles"]}
        assert by_name["Lecture"]["grantable"] is False
        assert by_name["Lecture"]["beyond"] == sorted(KEY_READS)

        # Un profil sans primitive qu'elle n'a pas : accordé.
        empty = await _agent_profile(app, "Vide", [])
        assert (
            await ana.post("/api/api-keys", json={"label": "x", "agent_profile_id": empty})
        ).status_code == 201


async def test_capacite_gagnee_sur_un_seul_espace_ne_suffit_pas(
    app: FastAPI, client: AsyncClient
) -> None:
    await bootstrap_admin(client)
    await _person(app, "ana@example.org", ["api_keys:write"])
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO secrets (slug, label, category, value_encrypted) "
            "VALUES ('gh', 'x', 'github', '\\x00')"
        )
        connection_id = await conn.fetchval(
            "INSERT INTO connections (label, slug, forge, url, secret_slug, identity, last_status) "
            "VALUES ('GH', 'gh', 'github', 'https://github.com', 'gh', 'moi', 'valid') "
            "RETURNING id"
        )
        space_id = await conn.fetchval(
            "INSERT INTO git_spaces (name, slug, connection_id, repo_url, last_probe_status) "
            "VALUES ('ventes', 'ventes', $1, 'https://github.com/o/v', 'valid') RETURNING id",
            connection_id,
        )
        team_id = await conn.fetchval(
            "INSERT INTO teams (name, slug, origin) VALUES ('t', 't', 'manual') RETURNING id"
        )
        await conn.execute(
            "INSERT INTO user_teams (user_id, team_id, origin) "
            "SELECT id, $1, 'manual' FROM users WHERE email = 'ana@example.org'",
            team_id,
        )
        reader = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) VALUES ('lecteur', '{spaces:read}') "
            "RETURNING id"
        )
        await conn.execute(
            "INSERT INTO space_team_profiles (space_id, team_id, profile_id) VALUES ($1, $2, $3)",
            space_id,
            team_id,
            reader,
        )
    async with _as(app, "ana@example.org") as ana:
        refused = await ana.post(
            "/api/api-keys", json={"label": "x", "agent_profile_id": await _profile_id(app)}
        )
    assert refused.status_code == 403


async def test_changer_vers_un_profil_au_dela_de_ses_droits_refuse(
    app: FastAPI, client: AsyncClient
) -> None:
    await _admin(client)
    empty = await _agent_profile(app, "Vide", [])
    key_id = (await _issue(client, empty)).json()["key"]["id"]
    await _person(app, "ana@example.org", ["api_keys:write"])
    async with _as(app, "ana@example.org") as ana:
        response = await ana.put(
            f"/api/api-keys/{key_id}/profile", json={"agent_profile_id": await _profile_id(app)}
        )
    assert response.status_code == 403


# --- Droits sur les routes ------------------------------------------------------------


async def test_reserve_aux_capacites_cles_d_api(app: FastAPI, client: AsyncClient) -> None:
    await bootstrap_admin(client)
    await _person(app, "lecteur@example.org", ["api_keys:read"])
    await _person(app, "rien@example.org", [])
    lecture = await _profile_id(app)
    async with _as(app, "lecteur@example.org") as reader:
        assert (await reader.get("/api/api-keys")).status_code == 200
        issue = await reader.post("/api/api-keys", json={"label": "x", "agent_profile_id": lecture})
        assert issue.status_code == 403 and issue.json()["error"] == "forbidden"
    async with _as(app, "rien@example.org") as nobody:
        assert (await nobody.get("/api/api-keys")).status_code == 403
        assert (await nobody.get("/api/api-keys/options")).status_code == 403


# --- Isolement de l'IHM ---------------------------------------------------------------


async def test_cle_n_ouvre_aucune_route_de_l_ihm_ni_dans_l_url(
    app: FastAPI, client: AsyncClient
) -> None:
    await _admin(client)
    secret = (await _issue(client, await _profile_id(app))).json()["secret"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as fresh:
        ui = await fresh.get("/api/api-keys", headers={"Authorization": f"Bearer {secret}"})
        assert ui.status_code == 401
        in_url = await fresh.post(f"/mcp?api_key={secret}", json={})
        assert in_url.status_code == 401


# --- Adresse à déclarer ---------------------------------------------------------------


async def test_options_donnent_l_adresse_de_la_surface_mcp(
    app: FastAPI, client: AsyncClient
) -> None:
    await _admin(client)
    options = (await client.get("/api/api-keys/options")).json()
    assert options["endpoint"] == "http://test/mcp"
    assert options["endpoint_configured"] is False

    app.state.settings.public_base_url = "https://ossie.example.org"
    options = (await client.get("/api/api-keys/options")).json()
    assert options["endpoint"] == "https://ossie.example.org/mcp"
    assert options["endpoint_configured"] is True


# --- Journal et dernier usage ---------------------------------------------------------


async def test_emission_changement_revocation_journalises_sans_le_secret(
    app: FastAPI, client: AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    await _admin(client)
    capsys.readouterr()
    body = (await _issue(client, await _profile_id(app))).json()
    key_id, secret = body["key"]["id"], body["secret"]
    empty = await _agent_profile(app, "Vide", [])
    await client.put(f"/api/api-keys/{key_id}/profile", json={"agent_profile_id": empty})
    await client.post(f"/api/api-keys/{key_id}/revoke")
    await client.post(f"/api/api-keys/{key_id}/revoke")
    out = capsys.readouterr().out

    for event in ("api_key_issued", "api_key_profile_changed", "api_key_revoked"):
        entries = _events(out, event)
        assert len(entries) == 1, event
        assert entries[0]["author"] == ADMIN_EMAIL and entries[0]["key_id"] == key_id
    assert secret not in out and secret[:12] not in out


async def test_dernier_usage_au_plus_une_ecriture_par_minute(
    app: FastAPI, client: AsyncClient
) -> None:
    await _admin(client)
    body = (await _issue(client, await _profile_id(app))).json()
    key_id, secret = uuid.UUID(body["key"]["id"]), body["secret"]

    async def last_used() -> Any:
        async with app.state.pool.acquire() as conn:
            return await conn.fetchval("SELECT last_used_at FROM api_keys WHERE id = $1", key_id)

    async def set_last_used(seconds: int) -> None:
        async with app.state.pool.acquire() as conn:
            await conn.execute(
                "UPDATE api_keys SET last_used_at = now() - make_interval(secs => $2) "
                "WHERE id = $1",
                key_id,
                seconds,
            )

    assert await last_used() is None
    await raw_mcp_status(app, secret)
    assert await last_used() is not None

    await set_last_used(30)
    recent = await last_used()
    await raw_mcp_status(app, secret)
    assert await last_used() == recent  # moins d'une minute : pas réécrit

    await set_last_used(120)
    old = await last_used()
    await raw_mcp_status(app, secret)
    assert await last_used() > old
