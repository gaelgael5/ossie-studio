"""Page « Paramètres » (epic « Primitives MCP — écriture commune par genre », ticket
ae8fc4b6) : la taille maximale d'un document écrit par un agent, 200 Ko par défaut
dans la configuration, réglable par un administrateur ; la valeur posée prime, vaut
sans redémarrage, et se voit dans list_write_kinds et dans les refus de taille."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.mcp_surface.caller import McpCaller
from tests.conftest import create_plain_user, login
from tests.mcp_helpers import call_tool
from tests.space_fixture import ApiSpace, forge_edit, make_api_space

AGENT = "parametres-agent"
URL = "/api/settings"
KEY = "document_max_bytes"
ROLE_TYPE = "title: Rôle\nx-archimate: BusinessRole\ntype: object\n"


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    api_space = await make_api_space(client, app, tmp_path)
    forge_edit(
        api_space,
        {"cartographie/types/role.schema.yaml": ROLE_TYPE},
    )
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives, all_read) VALUES ('p', $1, true) "
            "RETURNING id",
            ["write_documents"],
        )
        admin_id = await conn.fetchval("SELECT id FROM users WHERE is_bootstrap")
    caller = McpCaller(kind="oauth", ref="p", agent_profile_id=profile, user_id=admin_id)

    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return caller if token == AGENT else None

    app.state.mcp_resolvers.append(resolver)
    return api_space


@pytest.fixture
async def simple(app: FastAPI, space: ApiSpace) -> AsyncIterator[AsyncClient]:
    await create_plain_user(app, "simple@example.org", "mot-de-passe-simple")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        await login(other, "simple@example.org", "mot-de-passe-simple")
        yield other


async def _setting(http: AsyncClient) -> dict[str, Any]:
    response = await http.get(URL)
    assert response.status_code == 200, response.text
    [found] = response.json()["settings"]
    assert found["key"] == KEY
    data: dict[str, Any] = found
    return data


async def _kinds_limit(app: FastAPI) -> int:
    result = await call_tool(app, AGENT, "list_write_kinds", {})
    limit: int = result.structured_content["document_max_bytes"]
    return limit


async def _write_role(
    app: FastAPI, space: ApiSpace, size: int, key: str = "ROL-X"
) -> tuple[bool, dict[str, Any]]:
    content = {"cle": key, "type": "role", "nom": "X", "documentation": "x" * size}
    result = await call_tool(
        app,
        AGENT,
        "write_documents",
        {
            "space_id": space.space_id,
            "branch_id": space.branch_id,
            "message": "taille",
            "documents": [
                {
                    "kind": "carto_objet",
                    "identity": {"type": "role", "key": key},
                    "content": content,
                }
            ],
        },
    )
    if result.is_error:
        return True, json.loads(result.content[0].text)
    return False, result.structured_content


async def test_par_defaut_la_configuration(app: FastAPI, space: ApiSpace) -> None:
    """Critère 1 : sans réglage sur la page, 200 Ko (la configuration)."""
    found = await _setting(space.client)
    assert (found["value"], found["default"], found["source"]) == (200 * 1024, 200 * 1024, "config")
    assert (found["minimum"], found["maximum"]) == (1024, 10 * 1024 * 1024)
    assert await _kinds_limit(app) == 200 * 1024


async def test_modifier_vaut_sans_redemarrage(app: FastAPI, space: ApiSpace) -> None:
    """Critères 2 et 3 : la valeur posée s'applique à l'écriture suivante et se voit
    dans list_write_kinds ; une entrée plus grande est refusée avec taille, limite,
    chemin et `aide`, rien n'est écrit. Revenir à la valeur par défaut."""
    error, _body = await _write_role(app, space, 7000)
    assert not error
    response = await space.client.put(f"{URL}/{KEY}", json={"value": 6 * 1024})
    assert response.status_code == 200, response.text
    assert (response.json()["value"], response.json()["source"]) == (6144, "page")
    assert response.json()["updated_by"]
    assert await _kinds_limit(app) == 6144
    error, body = await _write_role(app, space, 7000, key="ROL-Y")
    assert error
    [problem] = body["errors"]
    assert (problem["path"], problem["code"]) == ("$.documents[0]", "write_document_too_large")
    assert problem["limit"] == 6144 and problem["size"] > 7000 and problem["aide"]
    reset = await space.client.delete(f"{URL}/{KEY}")
    assert reset.status_code == 200
    assert (reset.json()["value"], reset.json()["source"]) == (200 * 1024, "config")
    assert await _kinds_limit(app) == 200 * 1024


async def test_valeurs_refusees(space: ApiSpace) -> None:
    """Critère 7 : nulle, négative, non numérique, non entière, hors bornes."""
    for value in (0, -5, "abc", 3.5, True, None, 1023, 10 * 1024 * 1024 + 1):
        response = await space.client.put(f"{URL}/{KEY}", json={"value": value})
        assert response.status_code == 400, value
        body = response.json()
        assert body["error"] == "setting_value_invalid", value
    assert (await _setting(space.client))["source"] == "config"
    unknown = await space.client.put(f"{URL}/inconnu", json={"value": 2048})
    assert unknown.status_code == 404 and unknown.json()["error"] == "setting_not_found"


async def test_reserve_a_l_administration(app: FastAPI, simple: AsyncClient) -> None:
    """Critère 6 : sans settings:read, ni lecture ni modification ; settings:read
    seul lit sans modifier."""
    assert (await simple.get(URL)).status_code == 403
    assert (await simple.put(f"{URL}/{KEY}", json={"value": 4096})).status_code == 403
    async with app.state.pool.acquire() as conn:
        reader = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) VALUES ('lecteur', '{settings:read}') "
            "RETURNING id"
        )
        await conn.execute(
            "UPDATE users SET profile_id = $1 WHERE email = 'simple@example.org'", reader
        )
    assert (await simple.get(URL)).status_code == 200
    assert (await simple.put(f"{URL}/{KEY}", json={"value": 4096})).status_code == 403
    assert (await simple.delete(f"{URL}/{KEY}")).status_code == 403


async def test_chaque_changement_est_journalise(app: FastAPI, space: ApiSpace) -> None:
    await space.client.put(f"{URL}/{KEY}", json={"value": 8192})
    await space.client.put(f"{URL}/{KEY}", json={"value": 8192})  # inchangé : pas de ligne
    await space.client.delete(f"{URL}/{KEY}")
    async with app.state.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT action, object_id, detail FROM audit_log WHERE object_type = 'app_setting' "
            "ORDER BY at"
        )
    assert [(r["action"], r["object_id"]) for r in rows] == [("updated", KEY), ("updated", KEY)]
    assert rows[0]["detail"] == {"bytes": 8192, "previous": 200 * 1024}
    assert rows[1]["detail"]["reset"] is True
