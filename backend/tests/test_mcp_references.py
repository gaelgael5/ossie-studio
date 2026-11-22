"""Primitive MCP `get_reference_graph` : voisinage d'un objet raw, références
sortantes et entrantes (toutes sources), par clé d'API (vue publiée, index en
mémoire) et par agent connecté (vue brouillon, index de l'explorateur en base)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.mcp_surface.caller import McpCaller
from ossie_studio.sources.manifest import SourceManifest, dump_manifest
from tests.mcp_helpers import call_tool
from tests.space_fixture import ApiSpace, draft_write, forge_edit, make_api_space

KEY = "ref-key"
AGENT = "ref-agent"


def _manifest(slug: str) -> str:
    return dump_manifest(
        SourceManifest(
            slug=slug,
            label=slug.capitalize(),
            type="git",
            connection="gh",
            parameters={"repository": "o/r", "branch": "main", "parsers": ["csharp"]},
        )
    )


def _schema(title: str, props: dict[str, Any] | None = None, **extra: Any) -> str:
    return json.dumps(
        {
            "$id": f"{title.lower()}.json",
            "title": title,
            "type": "object",
            "x-kind": "class",
            "properties": props or {},
            **extra,
        }
    )


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    """Order → Customer (champ requis), Invoice → Order (liste), SpecialOrder
    hérite d'Order, Order → Money (type externe)."""
    api_space = await make_api_space(client, app, tmp_path)
    raw = "sources/ventes/raw"
    forge_edit(
        api_space,
        {
            "sources/ventes/manifest.yaml": _manifest("ventes"),
            f"{raw}/customer.json": _schema("Customer", {"id": {"type": "integer"}}),
            f"{raw}/order.json": _schema(
                "Order",
                {
                    "customer": {"$ref": "customer.json"},
                    "total": {"x-unresolved-type": "Money"},
                },
                required=["customer"],
            ),
            f"{raw}/invoice.json": _schema(
                "Invoice", {"orders": {"type": "array", "items": {"$ref": "order.json"}}}
            ),
            f"{raw}/specialorder.json": _schema("SpecialOrder", allOf=[{"$ref": "order.json"}]),
        },
    )
    async with app.state.pool.acquire() as conn:
        lecture = await conn.fetchval("SELECT id FROM agent_profiles WHERE slug = 'lecture'")
        admin_id = await conn.fetchval("SELECT id FROM users WHERE is_bootstrap")
    callers = {
        KEY: McpCaller(kind="key", ref="k", agent_profile_id=lecture),
        AGENT: McpCaller(
            kind="oauth", ref="c", agent_profile_id=lecture, user_id=admin_id, client_id="claude"
        ),
    }

    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return callers.get(token)

    app.state.mcp_resolvers.append(resolver)
    return api_space


def _args(space: ApiSpace, **extra: Any) -> dict[str, Any]:
    return {
        "space_id": space.space_id,
        "branch_id": space.branch_id,
        "source": "ventes",
        "path": "order.json",
        **extra,
    }


async def _ok(app: FastAPI, token: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await call_tool(app, token, "get_reference_graph", args)
    assert not result.is_error, result.content[0].text
    data: dict[str, Any] = result.structured_content
    return data


def _edges(data: dict[str, Any]) -> set[tuple[str, str, str, str]]:
    return {(e["from"], e["to"], e["kind"], e["direction"]) for e in data["edges"]}


async def test_qui_reference_l_objet(app: FastAPI, space: ApiSpace) -> None:
    data = await _ok(app, KEY, _args(space, direction="incoming"))
    assert data["view"] == "published"
    assert _edges(data) == {
        ("ventes/invoice.json", "ventes/order.json", "list", "incoming"),
        ("ventes/specialorder.json", "ventes/order.json", "inherits", "incoming"),
    }
    titles = {n["title"] for n in data["nodes"]}
    assert titles == {"Order", "Invoice", "SpecialOrder"}


async def test_ce_que_l_objet_reference_et_les_deux_sens(app: FastAPI, space: ApiSpace) -> None:
    out = await _ok(app, AGENT, _args(space, direction="outgoing"))
    assert out["view"] == "draft"
    assert {(e["to"], e["cardinality"]) for e in out["edges"]} == {
        ("ventes/customer.json", "1"),
        ("/ext:Money", "0..1"),
    }
    external = next(n for n in out["nodes"] if n.get("external"))
    assert external["title"] == "Money"
    both = await _ok(app, AGENT, _args(space))
    assert len(both["edges"]) == 4
    # Profondeur 2 en entrant depuis Customer : Order puis Invoice et SpecialOrder.
    deep = await _ok(app, KEY, _args(space, path="customer.json", direction="incoming", depth=2))
    assert {n["title"] for n in deep["nodes"]} == {"Customer", "Order", "Invoice", "SpecialOrder"}


async def test_le_brouillon_de_la_personne_compte(app: FastAPI, space: ApiSpace) -> None:
    await draft_write(
        app,
        space,
        "sources/ventes/raw/refund.json",
        {"$id": "refund.json", "title": "Refund", "properties": {"order": {"$ref": "order.json"}}},
    )
    draft = await _ok(app, AGENT, _args(space, direction="incoming"))
    assert "ventes/refund.json" in {e["from"] for e in draft["edges"]}
    published = await _ok(app, KEY, _args(space, direction="incoming"))
    assert "ventes/refund.json" not in {e["from"] for e in published["edges"]}


async def test_erreurs_nommees(app: FastAPI, space: ApiSpace) -> None:
    for args in (
        _args(space, path="absent.json"),
        _args(space, path="../manifest.yaml"),
    ):
        result = await call_tool(app, KEY, "get_reference_graph", args)
        assert result.is_error
        assert json.loads(result.content[0].text)["error"] == "schema_not_found"
    result = await call_tool(app, KEY, "get_reference_graph", _args(space, depth=4))
    assert result.is_error
