"""Primitive commune `read_schema` (ticket 06ef8679) : la nature se déduit du
chemin, le schéma effectif par défaut, la capacité du domaine vérifiée sur
l'espace, et les profils agents qui donnaient les anciennes lectures."""

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
from ossie_studio.mcp_surface.catalog import BY_ID
from tests.mcp_helpers import call_tool, tool_names
from tests.space_fixture import ApiSpace, forge_edit, make_api_space

KEY = "schema-key"
TYPE_PATH = "cartographie/types/processus-de-vente.schema.yaml"
TYPE_TEXT = (
    "title: Processus de vente\nx-archimate: BusinessProcess\ntype: object\n"
    "properties:\n"
    "  canal:\n    type: string\n    enum: [boutique, internet]\n"
    "  volume_jour:\n    type: integer\n    minimum: 0\n"
    "  frequence:\n    type: string\n"
)
RAW_PATH = "sources/ventes/raw/customer.schema.yaml"
ASSET_PATH = "sources/ventes/assets/customer.schema.yaml"
SCHEMA = {"title": "Customer", "type": "object", "properties": {"id": {"type": "integer"}}}


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    api_space = await make_api_space(client, app, tmp_path)
    forge_edit(
        api_space,
        {
            TYPE_PATH: TYPE_TEXT,
            "cartographie/types/acces.schema.yaml": (
                "title: Accès\nx-archimate: Access\ntype: object\n"
            ),
            RAW_PATH: json.dumps(SCHEMA),
            ASSET_PATH: json.dumps(SCHEMA),
        },
    )
    async with app.state.pool.acquire() as conn:
        lecture = await conn.fetchval("SELECT id FROM agent_profiles WHERE slug = 'lecture'")

    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return McpCaller(kind="key", ref="k", agent_profile_id=lecture) if token == KEY else None

    app.state.mcp_resolvers.append(resolver)
    return api_space


def _ids(space: ApiSpace, **extra: Any) -> dict[str, Any]:
    return {"space_id": space.space_id, "branch_id": space.branch_id, **extra}


async def _ok(app: FastAPI, token: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await call_tool(app, token, "read_schema", args)
    assert not result.is_error, result.content[0].text
    data: dict[str, Any] = result.structured_content
    return data


async def _refusal(app: FastAPI, token: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await call_tool(app, token, "read_schema", args)
    assert result.is_error
    body: dict[str, Any] = json.loads(result.content[0].text)
    return body


async def test_type_de_cartographie_effectif_et_tel_qu_ecrit(app: FastAPI, space: ApiSpace) -> None:
    """Critère 3 : base ArchiMate (cle, nom, documentation) plus les propriétés
    ajoutées ; resolved=false : seulement le fichier."""
    data = await _ok(app, KEY, _ids(space, path=TYPE_PATH))
    assert data["kind"] == "carto_type" and len(data["version"]) == 40
    schema = data["schema"]
    assert {"cle", "nom", "documentation", "proprietes"} <= set(schema["properties"])
    assert "source" not in schema["properties"]
    added = schema["properties"]["proprietes"]["properties"]
    assert {"canal", "volume_jour", "frequence"} <= set(added)
    assert added["canal"]["enum"] == ["boutique", "internet"]
    assert data["extras"] == {
        "slug": "processus-de-vente",
        "name": "Processus de vente",
        "archimate": "BusinessProcess",
        "layer": "business",
        "nature": "object",
    }
    raw = await _ok(app, KEY, _ids(space, path=TYPE_PATH, resolved=False))
    assert set(raw["schema"]["properties"]) == {"canal", "volume_jour", "frequence"}
    assert raw["extras"] == {} and raw["version"] == data["version"]
    # Une relation : source et cible font partie du document.
    relation = await _ok(app, KEY, _ids(space, path="cartographie/types/acces.schema.yaml"))
    assert {"source", "cible"} <= set(relation["schema"]["required"])
    assert relation["extras"]["nature"] == "relation"


async def test_chemins_inconnus_ou_absents(app: FastAPI, space: ApiSpace) -> None:
    unknown = await _refusal(app, KEY, _ids(space, path="cartographie/objets/a/b.yaml"))
    assert unknown == {"error": "schema_path_unknown", "path": "cartographie/objets/a/b.yaml"}
    missing = await _refusal(app, KEY, _ids(space, path="cartographie/types/absent.schema.yaml"))
    assert missing["error"] == "schema_not_found"


async def test_capacite_du_domaine_verifiee_sur_l_espace(app: FastAPI, space: ApiSpace) -> None:
    """Critère 5 : un agent pour une personne sans `curation:read` ne lit pas un
    asset ; il lit un schéma brut grâce à `sources:read`."""
    async with app.state.pool.acquire() as conn:
        user_id = await conn.fetchval(
            "INSERT INTO users (email, role) VALUES ('lea@example.org', 'user') RETURNING id"
        )
        team = await conn.fetchval(
            "INSERT INTO teams (name, slug, origin) VALUES ('t', 't', 'manual') RETURNING id"
        )
        await conn.execute(
            "INSERT INTO user_teams (user_id, team_id, origin) VALUES ($1, $2, 'manual')",
            user_id,
            team,
        )
        reader = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) "
            "VALUES ('lecteur', '{spaces:read,sources:read}') RETURNING id"
        )
        await conn.execute(
            "INSERT INTO space_team_profiles (space_id, team_id, profile_id) VALUES ($1, $2, $3)",
            uuid.UUID(space.space_id),
            team,
            reader,
        )
        lecture = await conn.fetchval("SELECT id FROM agent_profiles WHERE slug = 'lecture'")
    lea = McpCaller(kind="oauth", ref="l", agent_profile_id=lecture, user_id=user_id, client_id="c")

    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return lea if token == "lea" else None

    app.state.mcp_resolvers.append(resolver)
    raw = await _ok(app, "lea", _ids(space, path=RAW_PATH))
    assert raw["schema"] == SCHEMA and raw["extras"] == {
        "source": "ventes",
        "name": "customer.schema.yaml",
    }
    refused = await _refusal(app, "lea", _ids(space, path=ASSET_PATH))
    assert refused["error"] == "primitive_forbidden"
    assert refused["capability"] == "curation:read"
    # La clé d'API (profil Lecture) lit l'asset.
    assert (await _ok(app, KEY, _ids(space, path=ASSET_PATH)))["kind"] == "asset"


async def test_anciennes_primitives_retirees(app: FastAPI, space: ApiSpace) -> None:
    """Critère 6 : elles n'existent plus ; un profil qui les avait a read_schema."""
    for old in ("read_raw_schema", "read_asset", "read_referentiel_model"):
        assert old not in BY_ID
    assert "read_schema" in await tool_names(app, KEY)
    migration = (
        Path(__file__).parents[1] / "src/ossie_studio/db/migrations/0041_read_schema.sql"
    ).read_text(encoding="utf-8")
    async with app.state.pool.acquire() as conn:
        old = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives) "
            "VALUES ('ancien', '{list_spaces,read_asset,read_raw_schema}') RETURNING id"
        )
        other = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives) VALUES ('autre', '{list_spaces}') "
            "RETURNING id"
        )
        await conn.execute(migration)
        assert await conn.fetchval("SELECT primitives FROM agent_profiles WHERE id = $1", old) == [
            "list_spaces",
            "read_schema",
        ]
        assert await conn.fetchval(
            "SELECT primitives FROM agent_profiles WHERE id = $1", other
        ) == ["list_spaces"]
