"""Règles de tri et application d'une revue par l'agent (epic « Primitives MCP
d'écriture », ticket 2) : même service que l'écran, dans le brouillon de la
personne ; décisions contrôlées contre la revue lue ; garde review_stale."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.cartographie.service import CartographieTarget
from ossie_studio.mcp_surface.caller import McpCaller
from ossie_studio.sources.manifest import SourceManifest, dump_manifest
from ossie_studio.spaces.service import db_get_space
from tests.mcp_helpers import call_tool, mcp_client, tool_names
from tests.space_fixture import ApiSpace, admin_workdir, draft_write, forge_edit, make_api_space

AGENT = "agent-revue"
KEY = "cle-revue"
WRITES = ["write_documents", "patch_document", "apply_review"]

CUSTOMER = {
    "title": "Customer",
    "type": "object",
    "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
}
LEGACY = {"title": "Legacy", "type": "object", "properties": {"id": {"type": "integer"}}}


def _asset(raw: dict[str, Any], name: str) -> dict[str, Any]:
    return {**raw, "x-origin": f"raw/{name}"}


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    """Source « ventes » publiée : deux objets bruts retenus comme assets. Un
    agent connecté pour l'administrateur et une clé, avec un profil
    « Revue » (toutes les lectures + l'écriture commune + apply_review)."""
    api_space = await make_api_space(client, app, tmp_path)
    manifest = SourceManifest(
        slug="ventes",
        label="Ventes",
        type="git",
        connection="gh",
        parameters={"repository": "o/r", "branch": "main", "parsers": ["csharp"]},
    )
    forge_edit(
        api_space,
        {
            "sources/ventes/manifest.yaml": dump_manifest(manifest),
            "sources/ventes/raw/customer.schema.yaml": json.dumps(CUSTOMER),
            "sources/ventes/raw/legacy.schema.yaml": json.dumps(LEGACY),
            "sources/ventes/assets/customer.schema.yaml": json.dumps(
                _asset(CUSTOMER, "customer.schema.yaml")
            ),
            "sources/ventes/assets/legacy.schema.yaml": json.dumps(
                _asset(LEGACY, "legacy.schema.yaml")
            ),
        },
    )
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives, all_read) VALUES ('Revue', $1, true) "
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


def _ids(space: ApiSpace, **extra: Any) -> dict[str, Any]:
    return {"space_id": space.space_id, "branch_id": space.branch_id, "source": "ventes", **extra}


def _assets_args(space: ApiSpace, **extra: Any) -> dict[str, Any]:
    """list_documents des assets de la source (ce que rendait list_assets, ticket
    56f3784a)."""
    return {
        "space_id": space.space_id,
        "branch_id": space.branch_id,
        "document_type": "schema_asset",
        "identity": {"source": "ventes"},
        **extra,
    }


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


async def _draft_paths(app: FastAPI, space: ApiSpace) -> set[str]:
    draft = await _ok(
        app, AGENT, "get_my_draft", {"space_id": space.space_id, "branch_id": space.branch_id}
    )
    return {o["path"] for o in draft["objects"]}


async def _collected_in_draft(app: FastAPI, space: ApiSpace) -> dict[str, Any]:
    """Une collecte change le brut dans le brouillon de la personne (la branche
    publiée ne bouge pas) : rend la revue, comparée à la branche publiée
    (ticket docflow aad1d3c6, plus de point de reprise)."""
    # name change de type (changed), email apparaît (appeared)
    customer = {
        **CUSTOMER,
        "properties": {
            "id": {"type": "integer"},
            "name": {"type": "integer"},
            "email": {"type": "string"},
        },
    }
    await draft_write(app, space, "sources/ventes/raw/customer.schema.yaml", customer)
    await draft_write(app, space, "sources/ventes/raw/legacy.schema.yaml", None)  # retenu disparu
    await draft_write(app, space, "sources/ventes/raw/order.schema.yaml", {"title": "Order"})
    return await _ok(app, AGENT, "get_review", _ids(space, branch="draft"))


# --- Critères 1 et 2 : règles de tri ------------------------------------------------------


TRI = {"document_type": "regles_tri", "identity": {"source": "ventes"}}


async def _tri(app: FastAPI, space: ApiSpace) -> dict[str, Any]:
    return await _ok(app, AGENT, "read_document", _branch_ids(space, branch="draft", **TRI))


def _branch_ids(space: ApiSpace, **extra: Any) -> dict[str, Any]:
    return {"space_id": space.space_id, "branch_id": space.branch_id, **extra}


async def _write_tri(app: FastAPI, space: ApiSpace, rules: list[Any]) -> dict[str, Any]:
    version = (await _tri(app, space))["version"]
    entry = {**TRI, "version": version, "content": rules}
    return await _ok(
        app,
        AGENT,
        "write_documents",
        _branch_ids(space, message="règles de tri", documents=[entry]),
    )


async def _manifest(app: FastAPI, space: ApiSpace) -> str:
    """Le manifeste tel que le brouillon de la personne le contient."""
    workdir = await admin_workdir(app, space)
    async with app.state.pool.acquire() as conn:
        git_space = await db_get_space(conn, uuid.UUID(space.space_id))
    assert git_space is not None
    target = CartographieTarget(workdir, git_space, "work/principale", "x", None, "a@a")
    text: str = await target.read(
        lambda root: (root / "sources/ventes/manifest.yaml").read_text(encoding="utf-8")
    )
    return text


async def test_regles_de_tri_posees_rejouees_effacees(app: FastAPI, space: ApiSpace) -> None:
    """Ticket 71347227 : les règles de tri par l'écriture commune (genre regles_tri) ;
    critère 5 : le reste du manifeste ne bouge pas, à l'octet près."""
    before = await _manifest(app, space)
    assert (await _tri(app, space))["content"] == []
    rules = [
        {"target": "object-name", "pattern": "*Dto", "kind": "glob", "label": "DTO"},
        {"target": "field-name", "pattern": "^tmp_", "kind": "regex", "label": ""},
    ]
    posed = await _write_tri(app, space, rules)
    assert posed["changed_paths"] == ["sources/ventes/manifest.yaml"]
    read = await _tri(app, space)
    assert read["content"] == [
        {"target": "object-name", "pattern": "*Dto", "kind": "glob", "label": "DTO"},
        {"target": "field-name", "pattern": "^tmp_", "kind": "regex"},
    ]
    assert read["path"] == "sources/ventes/manifest.yaml" and read["unpublished"] is True
    replayed = await _write_tri(app, space, read["content"])
    assert replayed["documents"][0]["action"] == "unchanged"
    patched = await _ok(
        app,
        AGENT,
        "patch_document",
        _branch_ids(
            space,
            **TRI,
            version=read["version"],
            operations=[{"op": "set", "path": "$[0].pattern", "value": "*Snapshot*"}],
        ),
    )
    assert patched["written"] is True
    assert (await _tri(app, space))["content"][0]["pattern"] == "*Snapshot*"
    await _write_tri(app, space, [])
    assert (await _tri(app, space))["content"] == []
    assert await _manifest(app, space) == before


async def test_regle_invalide_refusee_en_la_nommant(app: FastAPI, space: ApiSpace) -> None:
    version = (await _tri(app, space))["version"]
    bad = [{"target": "object-name", "pattern": "x", "kind": "flou"}]
    refused = await _refusal(
        app,
        AGENT,
        "write_documents",
        _branch_ids(space, message="x", documents=[{**TRI, "version": version, "content": bad}]),
    )
    [error] = refused["errors"]
    assert (error["code"], error["path"]) == (
        "write_content_invalid",
        "$.documents[0].content[0]",
    )
    assert any(f.startswith("triage[0]") for f in error["problems"])
    assert error["aide"] == {"primitive": "describe_standard", "standard": "regles-tri"}
    assert await _draft_paths(app, space) == set()
    unknown = await _refusal(
        app,
        AGENT,
        "write_documents",
        _branch_ids(
            space,
            message="x",
            documents=[
                {"document_type": "regles_tri", "identity": {"source": "inconnue"}, "content": []}
            ],
        ),
    )
    assert unknown["errors"][0]["code"] == "write_identity_invalid"


# --- Critère 3 : la revue appliquée en une écriture, dans le brouillon ---------------------


async def test_revue_appliquee_dans_le_brouillon(app: FastAPI, space: ApiSpace) -> None:
    review = await _collected_in_draft(app, space)
    by_path = {o["path"]: o for o in review["objects"]}
    assert by_path["customer.schema.yaml"]["status"] == "retained-touched"
    changes = {f["name"]: f["change"] for f in by_path["customer.schema.yaml"]["fields"]}
    assert changes == {"name": "changed", "email": "appeared"}
    assert by_path["legacy.schema.yaml"]["status"] == "object-disappeared"
    assert by_path["order.schema.yaml"]["status"] == "new"

    applied = await _ok(
        app,
        AGENT,
        "apply_review",
        _ids(
            space,
            head_sha=review["head_sha"],
            apply_fields=[{"path": "customer.schema.yaml", "name": "name"}],
            take_fields=[{"path": "customer.schema.yaml", "name": "email"}],
            remove_objects=["legacy.schema.yaml"],
            retain_objects=["order.schema.yaml"],
        ),
    )
    assert applied["changed"] is True
    assert set(applied["changed_paths"]) == {
        "sources/ventes/assets/customer.schema.yaml",
        "sources/ventes/assets/legacy.schema.yaml",
        "sources/ventes/assets/order.schema.yaml",
    }
    assert applied["review"]["objects"] == []
    assert "up_to_date" not in applied["review"]  # plus de point de reprise

    customer = await _ok(
        app,
        AGENT,
        "read_schema",
        {
            "space_id": space.space_id,
            "branch_id": space.branch_id,
            "branch": "draft",
            "path": "sources/ventes/assets/customer.schema.yaml",
        },
    )
    fields = {f["name"]: f for f in customer["extras"]["fields"]}
    assert fields["email"]["retained"] is True and fields["name"]["type"] == "integer"
    draft = await _ok(app, AGENT, "list_documents", _assets_args(space, branch="draft"))
    assets = {a["identity"]["name"] for a in draft["documents"]}
    assert assets == {"customer.schema.yaml", "order.schema.yaml"}
    # Rien n'est publié.
    published = await _ok(app, AGENT, "list_documents", _assets_args(space))
    assert {a["identity"]["name"] for a in published["documents"]} == {
        "customer.schema.yaml",
        "legacy.schema.yaml",
    }


# --- Critère 4 : head_sha périmé ---------------------------------------------------------------


async def test_revue_perimee_refusee_sans_ecriture(app: FastAPI, space: ApiSpace) -> None:
    first = await _ok(app, AGENT, "get_review", _ids(space, branch="draft"))
    forge_edit(space, {"sources/ventes/raw/order.schema.yaml": json.dumps({"title": "Order"})})
    before = await _draft_paths(app, space)
    stale = await _refusal(app, AGENT, "apply_review", _ids(space, head_sha=first["head_sha"]))
    assert stale["error"] == "review_stale"
    assert await _draft_paths(app, space) == before


# --- Critère 5 : décision sans objet ou de la mauvaise nature ------------------------------------


async def test_decision_qui_ne_correspond_pas_a_la_revue_refusee(
    app: FastAPI, space: ApiSpace
) -> None:
    review = await _collected_in_draft(app, space)
    before = await _draft_paths(app, space)
    refused = await _refusal(
        app,
        AGENT,
        "apply_review",
        _ids(
            space,
            head_sha=review["head_sha"],
            # valide :
            apply_fields=[{"path": "customer.schema.yaml", "name": "name"}],
            # mauvaise nature (name a changé, il n'est pas apparu) et objet inconnu :
            take_fields=[{"path": "customer.schema.yaml", "name": "name"}],
            retain_objects=["legacy.schema.yaml", "fantome.schema.yaml"],
        ),
    )
    assert refused["error"] == "review_decision_invalid"
    assert refused["decisions"] == [
        "take_fields:customer.schema.yaml#name",
        "retain_objects:legacy.schema.yaml",
        "retain_objects:fantome.schema.yaml",
    ]
    # Aucune écriture partielle : la décision valide n'a pas été appliquée.
    assert await _draft_paths(app, space) == before
    still = await _ok(app, AGENT, "get_review", _ids(space, branch="draft"))
    assert still["objects"] == review["objects"]


async def test_arguments_de_revue_invalides(app: FastAPI, space: ApiSpace) -> None:
    sha = await _refusal(app, AGENT, "apply_review", _ids(space, head_sha="pas-un-sha"))
    assert sha == {"error": "invalid_arguments", "fields": ["head_sha"]}
    path = await _refusal(
        app, AGENT, "apply_review", _ids(space, head_sha="abcdef1", remove_objects=["../x"])
    )
    assert path["error"] == "curation_path_invalid"


# --- Critère 6 : droits ------------------------------------------------------------------------


async def test_droits_par_capacite_et_jamais_pour_une_cle(app: FastAPI, space: ApiSpace) -> None:
    assert not set(WRITES) & set(await tool_names(app, KEY))
    refused_key = await _refusal(
        app, KEY, "write_documents", _branch_ids(space, message="x", documents=[TRI])
    )
    assert refused_key["error"] == "primitive_forbidden"
    async with app.state.pool.acquire() as conn:
        user_id = await conn.fetchval(
            "INSERT INTO users (email, role) VALUES ('ana@example.org', 'user') RETURNING id"
        )
        base = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) "
            "VALUES ('ana', '{spaces:read,curation:read,curation:rules}') RETURNING id"
        )
        await conn.execute("UPDATE users SET profile_id = $2 WHERE id = $1", user_id, base)
        profile = await conn.fetchval("SELECT id FROM agent_profiles WHERE name = 'Revue'")
    ana = McpCaller(kind="oauth", ref="a", agent_profile_id=profile, user_id=user_id)

    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return ana if token == "ana" else None

    app.state.mcp_resolvers.append(resolver)
    tools = set(await tool_names(app, "ana"))
    # curation:rules oui, curation:review non.
    assert "write_documents" in tools and "apply_review" not in tools
    current = await _ok(app, "ana", "read_document", _branch_ids(space, branch="draft", **TRI))
    written = await _ok(
        app,
        "ana",
        "write_documents",
        _branch_ids(
            space,
            message="x",
            documents=[{**TRI, "version": current["version"], "content": []}],
        ),
    )
    assert written["written"] is True
    refused = await _refusal(app, "ana", "apply_review", _ids(space, head_sha="abcdef1"))
    assert refused["error"] == "primitive_forbidden"


# --- Critère 7 : annotations ---------------------------------------------------------------------


async def test_apply_review_annoncee_destructive(app: FastAPI, space: ApiSpace) -> None:
    async with mcp_client(app, AGENT) as client:
        tools = {t.name: t.annotations for t in (await client.list_tools()).tools}
    assert tools["apply_review"].destructive_hint is True


# --- L'écran passe par le même service, sans changement de comportement -------------------------


async def test_routes_de_l_ecran_inchangees(
    app: FastAPI, client: AsyncClient, space: ApiSpace
) -> None:
    base = f"{space.base}/sources/ventes/curation"
    rules = await client.put(
        f"{base}/rules",
        json={
            "rules": [{"target": "object-name", "pattern": "*Dto", "kind": "glob", "label": " "}]
        },
    )
    assert rules.status_code == 200
    assert rules.json()["rules"] == [{"target": "object-name", "pattern": "*Dto", "kind": "glob"}]
    assert rules.json()["commit"].startswith("draft:")
    invalid = await client.put(
        f"{base}/rules", json={"rules": [{"target": "x", "pattern": "y", "kind": "glob"}]}
    )
    assert invalid.status_code == 400 and invalid.json()["error"] == "invalid_request"
    missing = await client.put(f"{space.base}/sources/absente/curation/rules", json={"rules": []})
    assert missing.status_code == 404 and missing.json()["error"] == "source_not_found"

    review = (await client.get(f"{base}/review")).json()
    applied = await client.post(f"{base}/review/apply", json={"head_sha": review["head_sha"]})
    # Sans décision, rien à écrire (plus de point de reprise à avancer).
    assert applied.status_code == 200 and applied.json()["commit"] is None
    legacy = await client.post(
        f"{base}/review/apply", json={"head_sha": review["head_sha"], "advance": False}
    )
    assert legacy.status_code == 422  # l'option `advance` n'existe plus
    forge_edit(space, {"sources/ventes/raw/order.schema.yaml": json.dumps({"title": "Order"})})
    stale = await client.post(f"{base}/review/apply", json={"head_sha": review["head_sha"]})
    assert stale.status_code == 409 and stale.json()["error"] == "review_stale"
