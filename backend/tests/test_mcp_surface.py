"""Surface MCP : garde d'authentification, catalogue des primitives, point de
contrôle des droits, isolement de l'IHM et journal des appels."""

from __future__ import annotations

import json
import uuid
from typing import Any

import asyncpg
import httpx2
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.authz.catalog import ALL_CAPABILITIES
from ossie_studio.mcp_surface.caller import McpCaller
from ossie_studio.mcp_surface.catalog import CATALOG, READ_PRIMITIVES
from ossie_studio.mcp_surface.primitives import IMPLEMENTATIONS
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, bootstrap_admin, create_plain_user, login
from tests.mcp_helpers import call_tool, tool_names

# Primitives qui ne supposent que la lecture des espaces.
SPACES_READ = {p.id for p in CATALOG if p.capability == "spaces:read"}

KEY_TOKEN = "test-key-token-0123456789"
OAUTH_TOKEN = "test-oauth-token-0123456789"
METADATA = "http://test/.well-known/oauth-protected-resource/mcp"


# --- Outillage -----------------------------------------------------------------


async def _agent_profile(
    app: FastAPI, name: str, primitives: list[str], *, all_read: bool = False
) -> uuid.UUID:
    async with app.state.pool.acquire() as conn:
        value = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives, all_read) VALUES ($1, $2, $3) "
            "RETURNING id",
            name,
            primitives,
            all_read,
        )
    assert isinstance(value, uuid.UUID)
    return value


async def _lecture_profile(app: FastAPI) -> uuid.UUID:
    async with app.state.pool.acquire() as conn:
        value = await conn.fetchval("SELECT id FROM agent_profiles WHERE slug = 'lecture'")
    assert isinstance(value, uuid.UUID)
    return value


def _wire(app: FastAPI, token: str, caller: McpCaller) -> None:
    """Branche un résolveur de test : ce jeton → cet appelant."""

    async def resolver(_conn: asyncpg.Connection, presented: str, _res: str) -> McpCaller | None:
        return caller if presented == token else None

    app.state.mcp_resolvers.append(resolver)


def _key(profile_id: uuid.UUID) -> McpCaller:
    return McpCaller(kind="key", ref="key-1", agent_profile_id=profile_id)


def _oauth(profile_id: uuid.UUID, user_id: uuid.UUID) -> McpCaller:
    return McpCaller(
        kind="oauth", ref="conn-1", agent_profile_id=profile_id, user_id=user_id, client_id="claude"
    )


async def _tool_names(app: FastAPI, token: str) -> list[str]:
    return await tool_names(app, token)


async def _call(app: FastAPI, token: str, name: str, args: dict[str, Any] | None = None) -> Any:
    return await call_tool(app, token, name, args)


async def _space(app: FastAPI, name: str) -> uuid.UUID:
    async with app.state.pool.acquire() as conn:
        connection_id = await conn.fetchval("SELECT id FROM connections LIMIT 1")
        if connection_id is None:
            await conn.execute(
                "INSERT INTO secrets (slug, label, category, value_encrypted) "
                "VALUES ('gh-mcp', 'x', 'github', '\\x00')"
            )
            connection_id = await conn.fetchval(
                "INSERT INTO connections (label, slug, forge, url, secret_slug, identity, "
                "last_status) VALUES ('GH', 'gh', 'github', 'https://github.com', 'gh-mcp', "
                "'moi', 'valid') RETURNING id"
            )
        space_id = await conn.fetchval(
            "INSERT INTO git_spaces (name, slug, connection_id, repo_url, last_probe_status) "
            "VALUES ($1, $1, $2, $3, 'valid') RETURNING id",
            name,
            connection_id,
            f"https://github.com/o/{name}",
        )
        await conn.execute(
            "INSERT INTO space_branches (space_id, label, slug, kind, git_ref) "
            "VALUES ($1, 'principale', 'principale', 'base', 'work/principale')",
            space_id,
        )
    assert isinstance(space_id, uuid.UUID)
    return space_id


async def _person(app: FastAPI, email: str, caps: list[str]) -> uuid.UUID:
    await create_plain_user(app, email, "motdepasse-solide")
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


async def _set_person_caps(app: FastAPI, user_id: uuid.UUID, caps: list[str]) -> None:
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "UPDATE profiles SET capabilities = $2 "
            "WHERE id = (SELECT profile_id FROM users WHERE id = $1)",
            user_id,
            caps,
        )


def _refusal(result: Any) -> str:
    assert result.is_error
    return str(json.loads(result.content[0].text)["error"])


# --- Catalogue -------------------------------------------------------------------


def test_chaque_primitive_declare_une_capacite_humaine_connue() -> None:
    for primitive in CATALOG:
        assert primitive.capability in ALL_CAPABILITIES, primitive.id
        assert primitive.access in ("read", "write"), primitive.id
        assert primitive.description.strip(), primitive.id


def test_catalogue_et_implementations_concordent() -> None:
    assert {p.id for p in CATALOG} == set(IMPLEMENTATIONS)
    assert len({p.id for p in CATALOG}) == len(CATALOG)


async def test_profil_lecture_livre_avec_toutes_les_lectures(app: FastAPI) -> None:
    async with app.state.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM agent_profiles WHERE slug = 'lecture'")
    assert row is not None
    assert row["all_read"] and row["is_builtin"]
    _wire(app, KEY_TOKEN, _key(row["id"]))
    assert set(await _tool_names(app, KEY_TOKEN)) == READ_PRIMITIVES


# --- Garde d'authentification -------------------------------------------------------


async def test_sans_authentification_401_avec_www_authenticate(client: AsyncClient) -> None:
    for path in ("/mcp", "/mcp/"):
        response = await client.post(path, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == f'Bearer resource_metadata="{METADATA}"'


async def test_jeton_inconnu_401_invalid_token(client: AsyncClient) -> None:
    for headers in (
        {"Authorization": "Bearer inconnu"},
        {"Authorization": "Basic Zm9vOmJhcg=="},
        {"Authorization": "Bearer "},
    ):
        response = await client.post("/mcp", headers=headers, json={})
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == (
            f'Bearer error="invalid_token", resource_metadata="{METADATA}"'
        )


async def test_jeton_dans_l_url_jamais_lu(app: FastAPI, client: AsyncClient) -> None:
    _wire(app, KEY_TOKEN, _key(await _lecture_profile(app)))
    response = await client.post(f"/mcp?access_token={KEY_TOKEN}", json={})
    assert response.status_code == 401


async def test_deux_en_tetes_authorization_refuses(app: FastAPI) -> None:
    _wire(app, KEY_TOKEN, _key(await _lecture_profile(app)))
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.post(
            "/mcp",
            headers=[("Authorization", f"Bearer {KEY_TOKEN}"), ("Authorization", "Bearer autre")],
            json={},
        )
    assert response.status_code == 401


async def test_adresse_publique_configuree_dans_l_en_tete(
    app: FastAPI, client: AsyncClient
) -> None:
    app.state.settings.public_base_url = "https://ossie.example.org/"
    response = await client.post("/mcp", json={})
    assert response.headers["www-authenticate"] == (
        'Bearer resource_metadata="https://ossie.example.org/.well-known/'
        'oauth-protected-resource/mcp"'
    )


# --- Lister et appeler ---------------------------------------------------------------


async def test_cle_liste_les_outils_et_appelle_list_spaces(app: FastAPI) -> None:
    space_id = await _space(app, "ventes")
    _wire(app, KEY_TOKEN, _key(await _lecture_profile(app)))

    assert "list_spaces" in await _tool_names(app, KEY_TOKEN)
    result = await _call(app, KEY_TOKEN, "list_spaces")

    assert not result.is_error
    spaces = result.structured_content["spaces"]
    assert [s["id"] for s in spaces] == [str(space_id)]
    assert spaces[0]["branches"] == [
        {"id": spaces[0]["branches"][0]["id"], "name": "work/principale", "kind": "base"}
    ]


async def test_appelant_sans_primitive_voit_une_liste_vide(app: FastAPI) -> None:
    _wire(app, KEY_TOKEN, _key(await _agent_profile(app, "Vide", [])))
    assert await _tool_names(app, KEY_TOKEN) == []
    assert _refusal(await _call(app, KEY_TOKEN, "list_spaces")) == "primitive_forbidden"


async def test_primitive_retiree_disparait_et_son_appel_est_refuse(app: FastAPI) -> None:
    profile_id = await _agent_profile(app, "Repérage", ["list_spaces"])
    _wire(app, KEY_TOKEN, _key(profile_id))
    assert await _tool_names(app, KEY_TOKEN) == ["list_spaces"]

    async with app.state.pool.acquire() as conn:
        await conn.execute("UPDATE agent_profiles SET primitives = '{}' WHERE id = $1", profile_id)

    assert await _tool_names(app, KEY_TOKEN) == []
    assert _refusal(await _call(app, KEY_TOKEN, "list_spaces")) == "primitive_forbidden"


async def test_primitive_inconnue_du_catalogue_ignoree(app: FastAPI) -> None:
    profile_id = await _agent_profile(app, "Ancien", ["primitive_disparue", "list_spaces"])
    _wire(app, KEY_TOKEN, _key(profile_id))
    assert await _tool_names(app, KEY_TOKEN) == ["list_spaces"]


async def test_arguments_inattendus_refuses(app: FastAPI) -> None:
    _wire(app, KEY_TOKEN, _key(await _lecture_profile(app)))
    result = await _call(app, KEY_TOKEN, "list_spaces", {"intrus": 1})
    assert _refusal(result) == "invalid_arguments"


# --- Connexion pour une personne : bornée par ses capacités ------------------------------


async def test_oauth_borne_par_les_capacites_de_la_personne(app: FastAPI) -> None:
    await _space(app, "ventes")
    user_id = await _person(app, "ana@example.org", [])
    _wire(app, OAUTH_TOKEN, _oauth(await _lecture_profile(app), user_id))

    # La personne n'a pas `spaces:read` : l'agent non plus.
    assert await _tool_names(app, OAUTH_TOKEN) == []
    assert _refusal(await _call(app, OAUTH_TOKEN, "list_spaces")) == "primitive_forbidden"

    await _set_person_caps(app, user_id, ["spaces:read"])
    assert set(await _tool_names(app, OAUTH_TOKEN)) == SPACES_READ
    result = await _call(app, OAUTH_TOKEN, "list_spaces")
    assert [s["name"] for s in result.structured_content["spaces"]] == ["ventes"]

    # Retirée à la personne, retirée à l'agent dès l'appel suivant.
    await _set_person_caps(app, user_id, [])
    assert await _tool_names(app, OAUTH_TOKEN) == []


async def test_oauth_ne_voit_que_les_espaces_de_la_personne(app: FastAPI) -> None:
    visible = await _space(app, "ventes")
    await _space(app, "rh")
    user_id = await _person(app, "ana@example.org", [])
    async with app.state.pool.acquire() as conn:
        team_id = await conn.fetchval(
            "INSERT INTO teams (name, slug, origin) VALUES ('t', 't', 'manual') RETURNING id"
        )
        await conn.execute(
            "INSERT INTO user_teams (user_id, team_id, origin) VALUES ($1, $2, 'manual')",
            user_id,
            team_id,
        )
        reader = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) VALUES ('lecteur', '{spaces:read}') "
            "RETURNING id"
        )
        await conn.execute(
            "INSERT INTO space_team_profiles (space_id, team_id, profile_id) VALUES ($1, $2, $3)",
            visible,
            team_id,
            reader,
        )
    _wire(app, OAUTH_TOKEN, _oauth(await _lecture_profile(app), user_id))

    result = await _call(app, OAUTH_TOKEN, "list_spaces")
    assert [s["name"] for s in result.structured_content["spaces"]] == ["ventes"]


async def test_oauth_personne_disparue_rien(app: FastAPI) -> None:
    _wire(app, OAUTH_TOKEN, _oauth(await _lecture_profile(app), uuid.uuid4()))
    assert await _tool_names(app, OAUTH_TOKEN) == []


# --- Isolement de l'IHM ------------------------------------------------------------------


async def test_session_de_l_ihm_n_ouvre_pas_la_surface_mcp(client: AsyncClient) -> None:
    await bootstrap_admin(client)
    await login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    assert (await client.get("/api/spaces")).status_code == 200  # la session est bien ouverte

    response = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert response.status_code == 401


async def test_jeton_mcp_n_ouvre_aucune_route_de_l_ihm(app: FastAPI, client: AsyncClient) -> None:
    await bootstrap_admin(client)
    _wire(app, KEY_TOKEN, _key(await _lecture_profile(app)))
    async with AsyncClient(
        transport=client._transport, base_url="http://test"
    ) as fresh:  # aucun cookie
        response = await fresh.get("/api/spaces", headers={"Authorization": f"Bearer {KEY_TOKEN}"})
    assert response.status_code == 401


# --- Journal --------------------------------------------------------------------------------


def _events(out: str, event: str) -> list[dict[str, Any]]:
    entries = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    return [e for e in entries if e.get("event") == event]


async def test_chaque_appel_journalise_sans_le_jeton(
    app: FastAPI, capsys: pytest.CaptureFixture[str]
) -> None:
    _wire(app, KEY_TOKEN, _key(await _lecture_profile(app)))
    capsys.readouterr()
    await _call(app, KEY_TOKEN, "list_spaces")
    out = capsys.readouterr().out
    calls = _events(out, "mcp_call")
    assert len(calls) == 1
    entry = calls[0]
    assert entry["primitive"] == "list_spaces"
    assert entry["outcome"] == "ok"
    assert entry["caller_kind"] == "key" and entry["caller_ref"] == "key-1"
    assert isinstance(entry["duration_ms"], float)
    assert KEY_TOKEN not in out


async def test_refus_et_401_journalises_sans_le_jeton(
    app: FastAPI, client: AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    _wire(app, KEY_TOKEN, _key(await _agent_profile(app, "Vide", [])))
    capsys.readouterr()
    await _call(app, KEY_TOKEN, "list_spaces")
    await client.post("/mcp", headers={"Authorization": "Bearer jeton-secret-refuse"}, json={})
    out = capsys.readouterr().out
    assert [e["outcome"] for e in _events(out, "mcp_call")] == ["primitive_forbidden"]
    assert [e["reason"] for e in _events(out, "mcp_unauthenticated")] == ["invalid"]
    assert KEY_TOKEN not in out and "jeton-secret-refuse" not in out
