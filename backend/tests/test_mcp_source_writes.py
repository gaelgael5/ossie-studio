"""Sources par l'agent (epic « Primitives MCP d'écriture », ticket 3) : déclarer,
modifier, supprimer, collecter, contrats OpenAPI — mêmes contrôles que l'écran,
dans le brouillon de la personne ; jamais pour une clé d'API."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.mcp_surface.caller import McpCaller
from tests.fake_forge import GOOD_TOKEN
from tests.mcp_helpers import call_tool, mcp_client, tool_names
from tests.space_fixture import ApiSpace, make_api_space

AGENT = "agent-sources"
KEY = "cle-sources"
WRITES = [
    "declare_source",
    "update_source",
    "delete_source",
    "collect_source",
    "deposit_contract",
    "remove_contract",
]

CONTRACT = """
openapi: 3.0.3
info:
  title: API boutique
  version: 1.0.0
paths:
  /commandes:
    post:
      requestBody:
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Commande'
      responses:
        '200':
          description: ok
components:
  schemas:
    Commande:
      type: object
      properties:
        note:
          type: string
        ligne:
          $ref: '#/components/schemas/Ligne'
    Ligne:
      type: object
"""

GIT_SOURCE = {
    "slug": "api",
    "label": "API",
    "type": "git",
    "connection": "github",
    "parameters": {"repository": "o/r", "branch": "main", "parsers": ["markdown"]},
}


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    """Espace vide (connexion GitHub « github » vers la fausse forge). Un agent
    connecté pour l'administrateur et une clé, avec un profil « Sources »
    (toutes les lectures + les écritures des sources)."""
    api_space = await make_api_space(client, app, tmp_path)
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives, all_read) VALUES ('Sources', $1, true) "
            "RETURNING id",
            WRITES,
        )
        admin_id = await conn.fetchval("SELECT id FROM users WHERE is_bootstrap")
    callers = {
        AGENT: McpCaller(
            kind="oauth", ref="c", agent_profile_id=profile, user_id=admin_id, client_id="claude"
        ),
        KEY: McpCaller(kind="key", ref="k", agent_profile_id=profile),
    }

    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return callers.get(token)

    app.state.mcp_resolvers.append(resolver)
    return api_space


def _branch(space: ApiSpace, **extra: Any) -> dict[str, Any]:
    return {"space_id": space.space_id, "branch_id": space.branch_id, **extra}


async def _ok(app: FastAPI, token: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await call_tool(app, token, name, args)
    assert not result.is_error, result.content[0].text
    data: dict[str, Any] = result.structured_content
    return data


async def _refusal(app: FastAPI, token: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await call_tool(app, token, name, args)
    assert result.is_error
    data: dict[str, Any] = json.loads(result.content[0].text)
    return data


async def _sources(app: FastAPI, space: ApiSpace, branch: str = "draft") -> dict[str, Any]:
    listed = await _ok(app, AGENT, "list_sources", _branch(space, branch=branch))
    return {s["slug"]: s for s in listed["sources"]}


# --- Déclarer, modifier, supprimer ----------------------------------------------------


async def test_declarer_modifier_supprimer_une_source_git(app: FastAPI, space: ApiSpace) -> None:
    declared = await _ok(app, AGENT, "declare_source", _branch(space, **GIT_SOURCE))
    assert declared["source"]["slug"] == "api" and declared["source"]["type"] == "git"
    assert declared["changed_paths"] == ["sources/api/manifest.yaml"]
    draft = await _sources(app, space)
    assert draft["api"]["unpublished_changes"] == 1
    assert "api" not in await _sources(app, space, branch="")

    updated = await _ok(
        app,
        AGENT,
        "update_source",
        _branch(
            space,
            source="api",
            label="API commandes",
            connection="github",
            parameters={**GIT_SOURCE["parameters"], "branch": "develop"},
        ),
    )
    assert updated["source"]["label"] == "API commandes"
    assert (await _sources(app, space))["api"]["parameters"]["branch"] == "develop"

    # Un index de collecte existe pour la source : il part avec elle, comme à l'écran.
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO scan_files (space_id, branch_slug, source_slug, path, crc, statut) "
            "VALUES ($1, 'work/principale', 'api', 'README.md', 'x', 'ok')",
            uuid.UUID(space.space_id),
        )
    deleted = await _ok(app, AGENT, "delete_source", _branch(space, source="api"))
    assert deleted["changed_paths"] == ["sources/api/manifest.yaml"]
    assert "api" not in await _sources(app, space)
    async with app.state.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM scan_files WHERE source_slug = 'api'") == 0
    again = await _refusal(app, AGENT, "delete_source", _branch(space, source="api"))
    assert again["error"] == "source_not_found"


async def test_memes_refus_que_l_ecran_sans_ecriture(app: FastAPI, space: ApiSpace) -> None:
    cases = [
        ({"type": "fax"}, {"error": "invalid_request", "fields": ["type_unknown"]}),
        ({"slug": "Pas Un Slug"}, {"error": "invalid_request", "fields": ["slug_invalid"]}),
        (
            {"connection": "inconnue"},
            {"error": "invalid_request", "fields": ["connection_invalid"]},
        ),
        (
            {"parameters": {**GIT_SOURCE["parameters"], "branch": "nulle-part"}},
            {"error": "invalid_request", "fields": ["branch_not_found"]},
        ),
        (
            {"parameters": {**GIT_SOURCE["parameters"], "parsers": ["cobol"]}},
            {"error": "parseur_unknown", "id": "cobol"},
        ),
    ]
    for override, expected in cases:
        refused = await _refusal(
            app, AGENT, "declare_source", _branch(space, **{**GIT_SOURCE, **override})
        )
        assert refused == expected, override
    assert await _sources(app, space) == {}

    await _ok(app, AGENT, "declare_source", _branch(space, **GIT_SOURCE))
    taken = await _refusal(app, AGENT, "declare_source", _branch(space, **GIT_SOURCE))
    assert taken == {"error": "source_slug_taken", "slug": "api"}
    unknown = await _refusal(
        app, AGENT, "update_source", _branch(space, source="absente", label="x")
    )
    assert unknown["error"] == "source_not_found"


# --- Contrats OpenAPI et collecte -----------------------------------------------------


async def test_parcours_openapi_jusqu_a_la_collecte(
    app: FastAPI, client: AsyncClient, space: ApiSpace
) -> None:
    await _ok(
        app,
        AGENT,
        "declare_source",
        _branch(space, slug="boutique", label="Boutique", type="openapi"),
    )
    deposited = await _ok(
        app,
        AGENT,
        "deposit_contract",
        _branch(space, source="boutique", version="v1", content=CONTRACT, filename="boutique.yaml"),
    )
    assert deposited["contract"]["schema_count"] == 2
    assert any(p.startswith("sources/boutique/contrats/") for p in deposited["changed_paths"])
    contracts = await _ok(
        app, AGENT, "list_contracts", _branch(space, branch="draft", source="boutique")
    )
    assert [c["version"] for c in contracts["contracts"]] == ["v1"]

    # L'agent ne remplace jamais une version existante.
    taken = await _refusal(
        app,
        AGENT,
        "deposit_contract",
        _branch(space, source="boutique", version="v1", content=CONTRACT),
    )
    assert taken == {"error": "contract_version_taken", "version": "v1"}

    collected = await _ok(app, AGENT, "collect_source", _branch(space, source="boutique"))
    assert collected["state"] == "done", collected
    raw = {"kind": "schema_raw", "identity": {"source": "boutique"}}
    schemas = await _ok(app, AGENT, "list_documents", _branch(space, branch="draft", **raw))
    assert {s["identity"]["name"] for s in schemas["documents"]} == {
        "v1-commande.schema.yaml",
        "v1-ligne.schema.yaml",
    }
    assert all(s.get("unpublished") for s in schemas["documents"])

    status = await _ok(app, AGENT, "get_collect_status", _branch(space, source="boutique"))
    assert status["state"] == "done"
    # La même collecte se voit à l'écran (même tâche, rattachée à la personne).
    screen = await client.get(f"{space.base}/sources/boutique/collect")
    assert screen.json()["state"] == "done"

    removed = await _ok(
        app, AGENT, "remove_contract", _branch(space, source="boutique", version="v1")
    )
    assert removed["changed_paths"]
    assert (
        await _ok(app, AGENT, "list_contracts", _branch(space, branch="draft", source="boutique"))
    )["contracts"] == []
    gone = await _refusal(
        app, AGENT, "remove_contract", _branch(space, source="boutique", version="v1")
    )
    assert gone == {"error": "contract_not_found", "version": "v1"}


async def test_refus_des_contrats(app: FastAPI, space: ApiSpace) -> None:
    await _ok(
        app, AGENT, "declare_source", _branch(space, slug="boutique", label="B", type="openapi")
    )
    await _ok(app, AGENT, "declare_source", _branch(space, **GIT_SOURCE))
    deposit = _branch(space, source="boutique", version="v1")
    assert (await _refusal(app, AGENT, "deposit_contract", {**deposit, "content": "a: [b"}))[
        "error"
    ] == "contract_yaml_invalid"
    assert (await _refusal(app, AGENT, "deposit_contract", {**deposit, "content": "a: 1"}))[
        "error"
    ] == "contract_not_openapi"
    bad_version = await _refusal(
        app, AGENT, "deposit_contract", {**deposit, "version": "v 1", "content": CONTRACT}
    )
    assert bad_version["error"] == "contract_version_invalid"
    on_git = await _refusal(
        app, AGENT, "deposit_contract", _branch(space, source="api", version="v1", content=CONTRACT)
    )
    assert on_git == {"error": "invalid_request", "fields": ["not_openapi_source"]}


async def test_collecte_d_une_source_inconnue(app: FastAPI, space: ApiSpace) -> None:
    refused = await _refusal(app, AGENT, "collect_source", _branch(space, source="absente"))
    assert refused["error"] == "source_not_found"
    idle = await _ok(app, AGENT, "get_collect_status", _branch(space, source="absente"))
    assert idle["state"] == "idle"


# --- Droits, clés, annotations, secrets -----------------------------------------------


async def test_jamais_pour_une_cle_et_bornee_par_les_capacites(
    app: FastAPI, space: ApiSpace
) -> None:
    key_tools = set(await tool_names(app, KEY))
    assert not (set(WRITES) | {"get_collect_status"}) & key_tools
    assert "list_contracts" in key_tools  # simple lecture
    assert (await _refusal(app, KEY, "declare_source", _branch(space, **GIT_SOURCE)))[
        "error"
    ] == "primitive_forbidden"

    async with app.state.pool.acquire() as conn:
        user_id = await conn.fetchval(
            "INSERT INTO users (email, role) VALUES ('ana@example.org', 'user') RETURNING id"
        )
        base = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) "
            "VALUES ('ana', '{spaces:read,sources:read,sources:declare}') RETURNING id"
        )
        await conn.execute("UPDATE users SET profile_id = $2 WHERE id = $1", user_id, base)
        profile = await conn.fetchval("SELECT id FROM agent_profiles WHERE name = 'Sources'")
    ana = McpCaller(kind="oauth", ref="a", agent_profile_id=profile, user_id=user_id)

    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return ana if token == "ana" else None

    app.state.mcp_resolvers.append(resolver)
    tools = set(await tool_names(app, "ana"))
    # sources:declare oui, sources:collect non.
    assert "declare_source" in tools and "collect_source" not in tools
    refused = await _refusal(app, "ana", "collect_source", _branch(space, source="api"))
    assert refused["error"] == "primitive_forbidden"


async def test_annotations_et_aucun_secret(
    app: FastAPI, space: ApiSpace, capsys: pytest.CaptureFixture[str]
) -> None:
    async with mcp_client(app, AGENT) as client:
        tools = {t.name: t.annotations for t in (await client.list_tools()).tools}
    assert tools["delete_source"].destructive_hint is True
    assert tools["remove_contract"].destructive_hint is True
    assert tools["declare_source"].destructive_hint is False
    assert tools["update_source"].idempotent_hint is True
    assert tools["list_contracts"].read_only_hint is True

    capsys.readouterr()
    declared = await _ok(app, AGENT, "declare_source", _branch(space, **GIT_SOURCE))
    out = capsys.readouterr().out
    assert GOOD_TOKEN not in json.dumps(declared) and GOOD_TOKEN not in out
