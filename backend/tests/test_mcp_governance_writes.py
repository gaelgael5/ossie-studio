"""Écriture de la gouvernance par l'agent : tags, classifications et leurs niveaux,
certifications, par l'écriture commune `write_documents` / `patch_document` (epic
« Primitives MCP — écriture commune par genre », ticket 62fbfe11). Les treize
primitives propres à la gouvernance sont retirées ; chaque geste qu'elles
permettaient est couvert ici. Même service que l'écran, dans le brouillon de la
personne ; jamais pour une clé d'API ; borné par les droits de la personne sur
l'espace."""

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
from tests.mcp_helpers import call_tool, mcp_client, tool_names
from tests.space_fixture import ApiSpace, make_api_space

AGENT = "agent-gouvernance"
KEY = "cle-gouvernance"
COMMON = ["patch_document", "write_documents"]
REMOVED = {
    "create_tag",
    "update_tag",
    "delete_tag",
    "create_classification",
    "update_classification",
    "delete_classification",
    "create_level",
    "update_level",
    "delete_level",
    "reorder_levels",
    "create_certification",
    "update_certification",
    "delete_certification",
}
TAG = {"id": "gouvernance/pii"}
SCHEME = {"id": "sensibilite"}
CERT = {"slug": "or"}


async def _profile(app: FastAPI, name: str, primitives: list[str]) -> uuid.UUID:
    async with app.state.pool.acquire() as conn:
        value = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives, all_read) VALUES ($1, $2, true) "
            "RETURNING id",
            name,
            primitives,
        )
    assert isinstance(value, uuid.UUID)
    return value


def _wire(app: FastAPI, callers: dict[str, McpCaller]) -> None:
    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return callers.get(token)

    app.state.mcp_resolvers.append(resolver)


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    """Un espace sans gouvernance publiée. Un agent connecté pour l'administrateur,
    et une clé, avec un profil « Gouvernance » (toutes les lectures + l'écriture
    commune)."""
    api_space = await make_api_space(client, app, tmp_path)
    profile = await _profile(app, "Gouvernance", COMMON)
    async with app.state.pool.acquire() as conn:
        admin_id = await conn.fetchval("SELECT id FROM users WHERE is_bootstrap")
    _wire(
        app,
        {
            AGENT: McpCaller(
                kind="oauth", ref="c", agent_profile_id=profile, user_id=admin_id, client_id="c"
            ),
            KEY: McpCaller(kind="key", ref="k", agent_profile_id=profile),
        },
    )
    return api_space


def _ids(space: ApiSpace, **extra: Any) -> dict[str, Any]:
    return {"space_id": space.space_id, "branch_id": space.branch_id, **extra}


async def _call(
    app: FastAPI, space: ApiSpace, tool: str, token: str = AGENT, **args: Any
) -> tuple[bool, dict[str, Any]]:
    result = await call_tool(app, token, tool, _ids(space, **args))
    if result.is_error:
        return True, json.loads(result.content[0].text)
    data: dict[str, Any] = result.structured_content
    return False, data


async def _write(
    app: FastAPI, space: ApiSpace, documents: list[dict[str, Any]], token: str = AGENT
) -> dict[str, Any]:
    error, body = await _call(
        app, space, "write_documents", token, message="Gouvernance", documents=documents
    )
    assert not error, body
    return body


async def _refused(
    app: FastAPI, space: ApiSpace, documents: list[dict[str, Any]], token: str = AGENT
) -> dict[str, Any]:
    error, body = await _call(
        app, space, "write_documents", token, message="Gouvernance", documents=documents
    )
    assert error, body
    return body


async def _patch(
    app: FastAPI, space: ApiSpace, kind: str, identity: dict[str, Any], ops: list[dict[str, Any]]
) -> tuple[bool, dict[str, Any]]:
    current = await _read(app, space, kind, identity)
    return await _call(
        app,
        space,
        "patch_document",
        document_type=kind,
        identity=identity,
        version=current["version"],
        operations=ops,
    )


async def _read(
    app: FastAPI, space: ApiSpace, kind: str, identity: dict[str, Any]
) -> dict[str, Any]:
    error, body = await _call(
        app, space, "read_document", document_type=kind, identity=identity, branch="draft"
    )
    assert not error, body
    return body


async def _listed(app: FastAPI, space: ApiSpace, kind: str, **args: Any) -> list[dict[str, Any]]:
    error, body = await _call(app, space, "list_documents", document_type=kind, **args)
    assert not error, body
    documents: list[dict[str, Any]] = body["documents"]
    return documents


async def _draft_paths(app: FastAPI, space: ApiSpace) -> list[str]:
    error, draft = await _call(app, space, "get_my_draft")
    assert not error, draft
    return sorted(o["path"] for o in draft["objects"])


def _delete(kind: str, identity: dict[str, Any], version: str) -> dict[str, Any]:
    return {"document_type": kind, "identity": identity, "version": version, "delete": True}


# --- Critère 2 : chaque geste des primitives retirées, par l'écriture commune ------------


async def test_tag_creer_modifier_supprimer(app: FastAPI, space: ApiSpace) -> None:
    created = await _write(
        app,
        space,
        [
            {
                "document_type": "tag",
                "identity": TAG,
                "content": {
                    "info": {"owner": "dpo@example.org", "description": "Données personnelles"},
                    "links": {"wiki": "https://wiki.example.org/pii"},
                },
            }
        ],
    )
    assert created["documents"][0]["path"] == "governance/tags/gouvernance/pii.yaml"
    # Seule la description change : le responsable et les liens restent.
    error, patched = await _patch(
        app, space, "tag", TAG, [{"op": "set", "path": "$.info.description", "value": "PII"}]
    )
    assert not error, patched
    tag = await _read(app, space, "tag", TAG)
    assert tag["content"]["info"] == {"owner": "dpo@example.org", "description": "PII"}
    assert tag["content"]["links"] == {"wiki": "https://wiki.example.org/pii"}
    # Les liens donnés remplacent la table entière.
    error, relinked = await _patch(
        app, space, "tag", TAG, [{"op": "set", "path": "$.links", "value": {}}]
    )
    assert not error, relinked
    assert not (await _read(app, space, "tag", TAG))["content"].get("links")

    draft = await _listed(app, space, "tag", branch="draft")
    assert [t["identity"] for t in draft] == [TAG] and draft[0]["unpublished"] is True
    assert await _listed(app, space, "tag") == []

    version = (await _read(app, space, "tag", TAG))["version"]
    deleted = await _write(app, space, [_delete("tag", TAG, version)])
    assert deleted["documents"][0]["action"] == "deleted"
    assert await _listed(app, space, "tag", branch="draft") == []


async def test_classification_et_niveaux(app: FastAPI, space: ApiSpace) -> None:
    """Créer, modifier, supprimer une classification ; créer, modifier, supprimer un
    niveau ; réordonner les niveaux."""
    await _write(
        app,
        space,
        [
            {
                "document_type": "classification",
                "identity": SCHEME,
                "content": {
                    "name": "Sensibilité",
                    "multi_value": True,
                    "levels": [
                        {"name": "Public", "short_name": "PUB"},
                        {"name": "Interne", "short_name": "INT"},
                    ],
                },
            }
        ],
    )
    # Créer un niveau.
    error, body = await _patch(
        app,
        space,
        "classification",
        SCHEME,
        [{"op": "add", "path": "$.levels[2]", "value": {"name": "Secret", "short_name": "SEC"}}],
    )
    assert not error, body
    # Modifier un niveau : seul le libellé change.
    error, body = await _patch(
        app,
        space,
        "classification",
        SCHEME,
        [{"op": "set", "path": "$.levels[2].label", "value": "Très sensible"}],
    )
    assert not error, body
    scheme = (await _read(app, space, "classification", SCHEME))["content"]
    secret = scheme["levels"][2]
    assert (secret["name"], secret["short_name"], secret["label"]) == (
        "Secret",
        "SEC",
        "Très sensible",
    )
    # Réordonner : le document remplacé donne l'ordre des niveaux.
    current = await _read(app, space, "classification", SCHEME)
    reordered = {**current["content"], "levels": current["content"]["levels"][::-1]}
    await _write(
        app,
        space,
        [
            {
                "document_type": "classification",
                "identity": SCHEME,
                "version": current["version"],
                "content": reordered,
            }
        ],
    )
    scheme = (await _read(app, space, "classification", SCHEME))["content"]
    assert [lv["name"] for lv in scheme["levels"]] == ["Secret", "Interne", "Public"]
    assert scheme["levels"][0]["id"] == secret["id"]
    # Modifier la classification : seule la description change.
    error, body = await _patch(
        app,
        space,
        "classification",
        SCHEME,
        [{"op": "set", "path": "$.description", "value": "Niveau"}],
    )
    assert not error, body
    # La validation porte sur l'objet complet : principal + multi-valeurs refusé.
    error, refused = await _patch(
        app, space, "classification", SCHEME, [{"op": "set", "path": "$.is_primary", "value": True}]
    )
    assert error and refused["errors"][0]["code"] == "invalid_request"
    assert refused["errors"][0]["fields"] == ["scheme_primary_multivalue"]
    # Supprimer un niveau.
    error, body = await _patch(
        app, space, "classification", SCHEME, [{"op": "remove", "path": "$.levels[0]"}]
    )
    assert not error, body
    [listed] = await _listed(app, space, "classification", branch="draft")
    assert [lv["name"] for lv in listed["levels"]] == ["Interne", "Public"]
    assert listed["description"] == "Niveau" and listed["multi_value"] is True
    assert listed["unpublished"] is True
    assert await _listed(app, space, "classification") == []
    # Supprimer la classification.
    version = (await _read(app, space, "classification", SCHEME))["version"]
    await _write(app, space, [_delete("classification", SCHEME, version)])
    assert await _listed(app, space, "classification", branch="draft") == []


async def test_certification_creer_modifier_supprimer(app: FastAPI, space: ApiSpace) -> None:
    assert await _listed(app, space, "certification") == []
    created = await _write(
        app,
        space,
        [
            {
                "document_type": "certification",
                "identity": CERT,
                "content": {
                    "name": "Or",
                    "tag": "certification/or",
                    "description": "Qualité la plus élevée",
                },
            }
        ],
    )
    path = created["documents"][0]["path"]
    assert path.startswith("governance/certifications/") and path.endswith(".yaml")
    # Seul le rang change ; le nom, la description et le slug restent.
    error, body = await _patch(
        app, space, "certification", CERT, [{"op": "set", "path": "$.rank", "value": 2}]
    )
    assert not error, body
    cert = (await _read(app, space, "certification", CERT))["content"]
    assert (cert["rank"], cert["name"], cert["slug"]) == (2, "Or", "or")
    assert cert["description"] == "Qualité la plus élevée"
    draft = await _listed(app, space, "certification", branch="draft")
    assert [c["name"] for c in draft] == ["Or"] and draft[0]["unpublished"] is True
    assert await _listed(app, space, "certification") == []
    version = (await _read(app, space, "certification", CERT))["version"]
    await _write(app, space, [_delete("certification", CERT, version)])
    assert await _listed(app, space, "certification", branch="draft") == []


# --- Refus nommés, rien d'écrit -----------------------------------------------------------


async def test_refus_nommes_sans_ecriture(app: FastAPI, space: ApiSpace) -> None:
    await _write(
        app,
        space,
        [
            {"document_type": "tag", "identity": TAG, "content": {}},
            {
                "document_type": "classification",
                "identity": SCHEME,
                "content": {
                    "name": "Sensibilité",
                    "levels": [
                        {"name": "Public", "short_name": "PUB"},
                        {"name": "Interne", "short_name": "INT"},
                    ],
                },
            },
            {
                "document_type": "certification",
                "identity": CERT,
                "content": {"name": "Or", "tag": "certification/or"},
            },
        ],
    )
    before = await _draft_paths(app, space)

    def level(value: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"op": "add", "path": "$.levels[0]", "value": value}]

    exists = await _refused(app, space, [{"document_type": "tag", "identity": TAG, "content": {}}])
    assert exists["errors"][0]["code"] == "write_exists"
    bad_id = await _refused(
        app, space, [{"document_type": "tag", "identity": {"id": "pas valide!"}}]
    )
    assert bad_id["errors"][0]["code"] == "write_identity_invalid"
    other_branch = await call_tool(
        app,
        AGENT,
        "write_documents",
        _ids(
            space,
            message="x",
            branch="main",
            documents=[{"document_type": "tag", "identity": {"id": "autre"}, "content": {}}],
        ),
    )
    refusal = json.loads(other_branch.content[0].text)
    assert refusal["error"] == "branch_unsupported" and refusal["accepted"] == ["draft"]
    patches = [
        (level({"name": "Public", "short_name": "PUB2"}), "level_name_taken"),
        (level({"name": "Autre", "short_name": "PUB"}), "level_short_name_taken"),
        (level({"name": "a,b", "short_name": "AB"}), "invalid_request"),
    ]
    for ops, code in patches:
        error, body = await _patch(app, space, "classification", SCHEME, ops)
        assert error and body["errors"][0]["code"] == code, (ops, body)
    error, body = await _patch(
        app, space, "certification", CERT, [{"op": "set", "path": "$.rank", "value": 0}]
    )
    assert error and body["errors"][0]["fields"] == ["certification_rank_invalid"]
    taken = await _refused(
        app,
        space,
        [
            {
                "document_type": "certification",
                "identity": {"slug": "gold"},
                "content": {"name": "Gold", "tag": "certification/or"},
            }
        ],
    )
    assert taken["errors"][0]["code"] == "certification_tag_taken"
    # Aucune de ces tentatives n'a écrit : le brouillon n'a pas bougé.
    assert await _draft_paths(app, space) == before


# --- Droits -------------------------------------------------------------------------------


async def test_une_cle_ne_voit_ni_n_appelle_aucune_ecriture(app: FastAPI, space: ApiSpace) -> None:
    names = set(await tool_names(app, KEY))
    assert not ({*COMMON} | REMOVED) & names
    assert "list_documents" in names
    error, refused = await _call(
        app, space, "write_documents", KEY, message="x", documents=[{"document_type": "tag"}]
    )
    assert error and refused["error"] == "primitive_forbidden"


async def test_ecriture_bornee_par_les_droits_sur_l_espace(app: FastAPI, space: ApiSpace) -> None:
    async with app.state.pool.acquire() as conn:
        user_id = await conn.fetchval(
            "INSERT INTO users (email, role) VALUES ('ana@example.org', 'user') RETURNING id"
        )
        base = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) "
            "VALUES ('base-ana', '{spaces:read,governance:read}') RETURNING id"
        )
        await conn.execute("UPDATE users SET profile_id = $2 WHERE id = $1", user_id, base)
        other = await conn.fetchval(
            "INSERT INTO git_spaces (name, slug, connection_id, repo_url, last_probe_status) "
            "SELECT 'autre', 'autre', connection_id, 'file:///x', 'valid' FROM git_spaces "
            "WHERE id = $1 RETURNING id",
            uuid.UUID(space.space_id),
        )
        team = await conn.fetchval(
            "INSERT INTO teams (name, slug, origin) VALUES ('t', 't', 'manual') RETURNING id"
        )
        await conn.execute(
            "INSERT INTO user_teams (user_id, team_id, origin) VALUES ($1, $2, 'manual')",
            user_id,
            team,
        )
        writer = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) VALUES ('gouv', '{governance:write}') "
            "RETURNING id"
        )
        await conn.execute(
            "INSERT INTO space_team_profiles (space_id, team_id, profile_id) VALUES ($1, $2, $3)",
            other,
            team,
            writer,
        )
        profile = await conn.fetchval("SELECT id FROM agent_profiles WHERE name = 'Gouvernance'")
    _wire(app, {"ana": McpCaller(kind="oauth", ref="a", agent_profile_id=profile, user_id=user_id)})
    entry = [{"document_type": "tag", "identity": TAG, "content": {}}]

    # governance:write sur un AUTRE espace seulement : refusé sur celui-ci.
    assert "write_documents" in await tool_names(app, "ana")
    refused = await _refused(app, space, entry, token="ana")
    assert refused["errors"][0]["code"] == "document_type_forbidden"
    assert refused["errors"][0]["capability"] == "governance:write"

    # Donné sur cet espace : permis.
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "UPDATE profiles SET capabilities = '{spaces:read,governance:read,governance:write}' "
            "WHERE id = $1",
            base,
        )
    assert (await _write(app, space, entry, token="ana"))["written"] is True


# --- Critères 1 et 3 : les primitives retirées et les profils qui les citaient -----------


async def test_primitives_retirees(app: FastAPI, space: ApiSpace) -> None:
    async with mcp_client(app, AGENT) as client:
        tools = {t.name: t.annotations for t in (await client.list_tools()).tools}
    for name in REMOVED:
        assert name not in BY_ID and name not in tools, name
    assert tools["write_documents"].destructive_hint is True
    assert tools["list_documents"].read_only_hint is True


async def test_un_profil_qui_citait_une_ecriture_retiree_se_charge(
    app: FastAPI, space: ApiSpace
) -> None:
    """Critère 3 : la référence est ignorée tant qu'elle reste, puis nettoyée par la
    migration 0043 (sans ajouter l'écriture commune à sa place)."""
    old = await _profile(app, "Ancien", ["create_tag", "list_spaces", "reorder_levels"])
    async with app.state.pool.acquire() as conn:
        admin_id = await conn.fetchval("SELECT id FROM users WHERE is_bootstrap")
    caller = McpCaller(kind="oauth", ref="o", agent_profile_id=old, user_id=admin_id)
    _wire(app, {"ancien": caller})
    names = set(await tool_names(app, "ancien"))
    assert "list_spaces" in names and not REMOVED & names
    migration = (
        Path(__file__).parents[1]
        / "src/ossie_studio/db/migrations/0043_governance_writes_removed.sql"
    ).read_text(encoding="utf-8")
    async with app.state.pool.acquire() as conn:
        await conn.execute(migration)
        kept = await conn.fetchval("SELECT primitives FROM agent_profiles WHERE id = $1", old)
    assert kept == ["list_spaces"]
    assert "list_spaces" in await tool_names(app, "ancien")


# --- L'écran passe par le même service ----------------------------------------------------


async def test_ecran_refuse_un_ordre_de_niveaux_avec_doublon(
    app: FastAPI, client: AsyncClient, space: ApiSpace
) -> None:
    await _write(
        app,
        space,
        [
            {
                "document_type": "classification",
                "identity": SCHEME,
                "content": {
                    "name": "Sensibilité",
                    "levels": [
                        {"name": "Public", "short_name": "PUB"},
                        {"name": "Interne", "short_name": "INT"},
                    ],
                },
            }
        ],
    )
    base = f"/api/spaces/{space.space_id}/branches/{space.branch_id}/governance/classifications"
    levels = (await client.get(base)).json()["schemes"][0]["levels"]
    first = levels[0]["id"]
    response = await client.put(
        f"{base}/sensibilite/levels", json={"order": [first, first, levels[1]["id"]]}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    # L'agent et l'écran voient le même brouillon.
    response = await client.put(
        f"{base}/sensibilite/levels/{first}",
        json={"name": "Public", "short_name": "PUB", "label": "Ouvert"},
    )
    assert response.status_code == 200
    [scheme] = await _listed(app, space, "classification", branch="draft")
    assert scheme["levels"][0]["label"] == "Ouvert"
