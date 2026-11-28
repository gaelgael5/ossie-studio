"""Les règles dans les primitives communes (epic « Primitives MCP — écriture commune par
genre », ticket 71347227) : règles de tags et de classification (natives comprises,
gérées comme à l'écran), validées par les services de l'écran."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.governance.builtin_tag_rules import builtin_rules
from ossie_studio.mcp_surface.caller import McpCaller
from tests.mcp_helpers import call_tool
from tests.space_fixture import ApiSpace, make_api_space

AGENT = "regles-agent"


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    api_space = await make_api_space(client, app, tmp_path)
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives, all_read) VALUES ('r', $1, true) "
            "RETURNING id",
            ["write_documents", "patch_document"],
        )
        admin_id = await conn.fetchval("SELECT id FROM users WHERE is_bootstrap")
    caller = McpCaller(kind="oauth", ref="r", agent_profile_id=profile, user_id=admin_id)

    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return caller if token == AGENT else None

    app.state.mcp_resolvers.append(resolver)
    return api_space


async def _call(app: FastAPI, space: ApiSpace, tool: str, **args: Any) -> tuple[bool, Any]:
    ids = {"space_id": space.space_id, "branch_id": space.branch_id}
    result = await call_tool(app, AGENT, tool, {**ids, **args})
    if result.is_error:
        return True, json.loads(result.content[0].text)
    return False, result.structured_content


async def _write(app: FastAPI, space: ApiSpace, *documents: dict[str, Any]) -> tuple[bool, Any]:
    return await _call(app, space, "write_documents", message="règles", documents=list(documents))


RULE = {
    "name": "Contrôleurs",
    "files": ["*.cs"],
    "when": {"inherits": ["ControllerBase"]},
    "tags": ["technique/service"],
}


async def test_regle_de_tag_ecrite_validee_et_tags_crees(app: FastAPI, space: ApiSpace) -> None:
    """Critère 1 : le fichier est écrit sous governance/tag-rules/, validé par le service,
    et le tag cité est créé (comme à l'écran) ; une condition mal formée est refusée
    avec le chemin fautif et `aide`."""
    error, body = await _write(
        app, space, {"kind": "regle_tag", "identity": {"id": "controleurs"}, "content": RULE}
    )
    assert not error, body
    [doc] = body["documents"]
    assert doc["path"] == "governance/tag-rules/controleurs.yaml"
    assert any("technique/service" in w["message"] for w in body["warnings"])
    error, read = await _call(
        app,
        space,
        "read_document",
        kind="regle_tag",
        identity={"id": "controleurs"},
        branch="draft",
    )
    assert not error and read["content"]["when"] == {"inherits": ["ControllerBase"]}
    error, tags = await _call(app, space, "list_documents", kind="tag", branch="draft")
    assert "technique/service" in {t["identity"]["id"] for t in tags["documents"]}
    bad = {**RULE, "when": {"inconnue": ["x"]}}
    error, body = await _write(
        app, space, {"kind": "regle_tag", "identity": {"id": "mauvaise"}, "content": bad}
    )
    assert error
    [refusal] = body["errors"]
    assert (refusal["code"], refusal["path"]) == (
        "write_content_invalid",
        "$.documents[0].content.when",
    )
    assert refusal["problems"] == ["tag_rule_condition_unknown"]
    assert refusal["aide"] == {"primitive": "describe_standard", "standard": "regle-tag"}


async def test_regle_de_classification_niveau_inexistant(app: FastAPI, space: ApiSpace) -> None:
    """Critère 2 : une règle qui vise un niveau inexistant est refusée."""
    scheme = {
        "kind": "classification",
        "identity": {"id": "rgpd"},
        "content": {"name": "RGPD", "levels": [{"name": "Publique", "short_name": "PUB"}]},
    }
    rule = {
        "kind": "regle_classification",
        "identity": {"id": "emails"},
        "content": {
            "name": "Emails",
            "when": {"field": ["*email*"]},
            "scheme": "rgpd",
            "values": ["Donnée personnelle"],
        },
    }
    error, body = await _write(app, space, scheme, rule)
    assert error
    [refusal] = body["errors"]
    assert (refusal["path"], refusal["problems"]) == (
        "$.documents[1].content.values",
        ["classification_rule_value_unknown"],
    )
    rule["content"]["values"] = ["Publique"]
    error, body = await _write(app, space, scheme, rule)
    assert not error, body


async def test_regles_natives_listees_et_desactivees(app: FastAPI, space: ApiSpace) -> None:
    """Critères 3 et 4 : les natives sont listées, distinguées ; les désactiver passe par
    le mécanisme de l'écran (le fichier d'état) ; elles ne se modifient ni ne se
    suppriment autrement."""
    native = builtin_rules()[0]
    await _write(
        app, space, {"kind": "regle_tag", "identity": {"id": "controleurs"}, "content": RULE}
    )
    error, listed = await _call(app, space, "list_documents", kind="regle_tag", branch="draft")
    by_id = {d["identity"]["id"]: d for d in listed["documents"]}
    assert by_id[native.id]["native"] is True and by_id[native.id]["enabled"] is True
    assert by_id[native.id]["path"] == "governance/tag-rules-builtin.yaml"
    assert by_id["controleurs"]["native"] is False
    error, read = await _call(
        app, space, "read_document", kind="regle_tag", identity={"id": native.id}, branch="draft"
    )
    assert read["version"] == by_id[native.id]["version"]
    disabled = {**read["content"], "enabled": False}
    error, body = await _write(
        app,
        space,
        {
            "kind": "regle_tag",
            "identity": {"id": native.id},
            "version": read["version"],
            "content": disabled,
        },
    )
    assert not error, body
    assert body["documents"][0]["path"] == "governance/tag-rules-builtin.yaml"
    error, listed = await _call(app, space, "list_documents", kind="regle_tag", branch="draft")
    state = next(d for d in listed["documents"] if d["identity"]["id"] == native.id)
    assert state["enabled"] is False
    error, read = await _call(
        app, space, "read_document", kind="regle_tag", identity={"id": native.id}, branch="draft"
    )
    changed = {**read["content"], "name": "Autre nom"}
    error, body = await _write(
        app,
        space,
        {
            "kind": "regle_tag",
            "identity": {"id": native.id},
            "version": read["version"],
            "content": changed,
        },
    )
    assert error and "seul « enabled » change" in body["errors"][0]["message"]
    error, body = await _write(
        app,
        space,
        {
            "kind": "regle_tag",
            "identity": {"id": native.id},
            "version": read["version"],
            "delete": True,
        },
    )
    assert error and body["errors"][0]["code"] == "write_delete_unsupported"


async def test_profils_qui_citaient_les_regles_de_tri(app: FastAPI) -> None:
    """Migration 0046 : get_tri_rules → lectures communes ; set_tri_rules nettoyée."""
    migration = (
        Path(__file__).parents[1] / "src/ossie_studio/db/migrations/0046_tri_rules_removed.sql"
    ).read_text(encoding="utf-8")
    async with app.state.pool.acquire() as conn:
        reader = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives) "
            "VALUES ('lit', '{get_tri_rules,list_spaces}') RETURNING id"
        )
        writer = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives) "
            "VALUES ('ecrit', '{list_spaces,set_tri_rules}') RETURNING id"
        )
        await conn.execute(migration)
        read = await conn.fetchval("SELECT primitives FROM agent_profiles WHERE id = $1", reader)
        written = await conn.fetchval("SELECT primitives FROM agent_profiles WHERE id = $1", writer)
    assert read == ["list_documents", "list_spaces", "read_document"]
    assert written == ["list_spaces"]
