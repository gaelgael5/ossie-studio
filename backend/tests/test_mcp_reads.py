"""Primitives de lecture de la surface MCP, sur un vrai dépôt git de test :
vue publiée (clé d'API) et vue brouillon (agent connecté pour une personne),
marque `unpublished`, erreurs nommées des espaces, droits par espace."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.mcp_surface.caller import McpCaller
from ossie_studio.sources.manifest import SourceManifest, dump_manifest
from tests.mcp_helpers import call_tool
from tests.space_fixture import (
    ApiSpace,
    admin_workdir,
    draft_write,
    forge_edit,
    make_api_space,
)

KEY = "test-key"
AGENT = "test-agent"

CUSTOMER = {
    "title": "Customer",
    "type": "object",
    "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
}
CUSTOMER_ASSET = {
    "title": "Customer",
    "type": "object",
    "x-businessName": "Client",
    "x-description": "Une personne ou une société qui achète.",
    "properties": {"id": {"type": "integer", "x-businessName": "Identifiant"}},
}


def _manifest(slug: str, triage: list[dict[str, Any]] | None = None) -> str:
    return dump_manifest(
        SourceManifest(
            slug=slug,
            label=slug.capitalize(),
            type="git",
            connection="gh",
            parameters={"repository": "o/r", "branch": "main", "parsers": ["csharp"]},
            triage=triage or [],
        )
    )


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    """Espace avec, côté publié, la source « ventes » : un schéma brut retenu
    comme asset enrichi, un autre non retenu, une règle de tri. Deux appelants
    branchés : une clé (Lecture) et un agent agissant pour l'administrateur."""
    api_space = await make_api_space(client, app, tmp_path)
    forge_edit(
        api_space,
        {
            "sources/ventes/manifest.yaml": _manifest(
                "ventes", [{"target": "object-name", "pattern": "*Dto", "kind": "glob"}]
            ),
            "sources/ventes/raw/customer.schema.yaml": json.dumps(CUSTOMER),
            "sources/ventes/raw/order.schema.yaml": json.dumps(
                {"title": "Order", "properties": {}}
            ),
            "sources/ventes/assets/customer.schema.yaml": json.dumps(CUSTOMER_ASSET),
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


def _ids(space: ApiSpace, **extra: Any) -> dict[str, Any]:
    return {"space_id": space.space_id, "branch_id": space.branch_id, **extra}


async def _ok(app: FastAPI, token: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await call_tool(app, token, name, args)
    assert not result.is_error, result.content[0].text
    data: dict[str, Any] = result.structured_content
    return data


async def _refusal(app: FastAPI, token: str, name: str, args: dict[str, Any]) -> str:
    result = await call_tool(app, token, name, args)
    assert result.is_error
    return str(json.loads(result.content[0].text)["error"])


async def _draft_text(app: FastAPI, space: ApiSpace, rel: str, text: str) -> None:
    workdir = await admin_workdir(app, space)

    def write(root: Path) -> None:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    await workdir.commit_to_branch(
        uuid.UUID(space.space_id), "work/principale", space.url, "x", None, "a", "a", "écrit", write
    )


# --- Vue publiée et vue brouillon ------------------------------------------------------


async def test_sans_branch_l_agent_lit_le_publie_avec_draft_aussi_le_brouillon(
    app: FastAPI, space: ApiSpace
) -> None:
    """Ticket aa19569b : `branch` absent → publiée, même pour un agent connecté
    pour une personne ; `branch: draft` → publiée + brouillon, marqué."""
    # Source déclarée dans le brouillon de la personne seulement.
    await _draft_text(app, space, "sources/achats/manifest.yaml", _manifest("achats"))

    published = await _ok(app, KEY, "list_documents", _ids(space, kind="source"))
    assert published["view"] == "published"
    assert [s["identity"]["source"] for s in published["documents"]] == ["ventes"]

    by_default = await _ok(app, AGENT, "list_documents", _ids(space, kind="source"))
    assert by_default["view"] == "published"
    assert [s["identity"]["source"] for s in by_default["documents"]] == ["ventes"]

    draft = await _ok(app, AGENT, "list_documents", _ids(space, kind="source", branch="draft"))
    assert draft["view"] == "draft"
    by_slug = {s["identity"]["source"]: s for s in draft["documents"]}
    assert set(by_slug) == {"ventes", "achats"}
    assert by_slug["achats"]["unpublished_changes"] == 1
    assert by_slug["achats"]["unpublished"] is True
    assert by_slug["ventes"]["unpublished_changes"] == 0
    assert by_slug["ventes"]["connection"] == "gh"

    # Vide : comme absent.
    empty = await _ok(app, AGENT, "list_documents", _ids(space, kind="source", branch=""))
    assert empty["view"] == "published"


async def test_branch_autre_que_draft_refusee(app: FastAPI, space: ApiSpace) -> None:
    result = await call_tool(
        app, AGENT, "list_documents", _ids(space, kind="source", branch="main")
    )
    assert result.is_error
    body = json.loads(result.content[0].text)
    assert body["error"] == "branch_unsupported"
    assert body["accepted"] == ["draft"]
    # Aussi sur les primitives qui ne lisent que le brouillon.
    assert await _refusal(app, AGENT, "get_my_draft", _ids(space, branch="main")) == (
        "branch_unsupported"
    )
    # L'ancien paramètre `view` n'existe plus.
    assert await _refusal(
        app, AGENT, "list_documents", _ids(space, kind="source", view="draft")
    ) == ("invalid_arguments")


async def test_cle_ne_peut_pas_demander_le_brouillon(app: FastAPI, space: ApiSpace) -> None:
    assert await _refusal(
        app, KEY, "list_documents", _ids(space, kind="source", branch="draft")
    ) == ("draft_requires_person")
    # Les primitives du brouillon ne sont même pas proposées à une clé.
    assert await _refusal(app, KEY, "get_my_draft", _ids(space)) == "primitive_forbidden"
    assert await _refusal(app, KEY, "get_publish_readiness", _ids(space)) == ("primitive_forbidden")


# --- Schémas bruts ---------------------------------------------------------------------


async def test_schemas_bruts_listes_et_lus_avec_la_marque_du_brouillon(
    app: FastAPI, space: ApiSpace
) -> None:
    changed = {**CUSTOMER, "properties": {**CUSTOMER["properties"], "email": {"type": "string"}}}
    await draft_write(app, space, "sources/ventes/raw/customer.schema.yaml", changed)

    # Ticket 56f3784a : ce que rendait list_raw_schemas, par list_documents.
    raw = {"kind": "schema_raw", "identity": {"source": "ventes"}}
    listed = await _ok(app, AGENT, "list_documents", _ids(space, branch="draft", **raw))
    by_name = {s["identity"]["name"]: s for s in listed["documents"]}
    assert set(by_name) == {"customer.schema.yaml", "order.schema.yaml"}
    assert by_name["customer.schema.yaml"]["unpublished"] is True
    assert by_name["customer.schema.yaml"]["fields"] == 3
    assert by_name["customer.schema.yaml"]["retained"] == "partial"
    assert len(by_name["customer.schema.yaml"]["version"]) == 40
    assert "unpublished" not in by_name["order.schema.yaml"]

    schema_path = by_name["customer.schema.yaml"]["path"]
    assert schema_path == "sources/ventes/raw/customer.schema.yaml"
    by_kind = _ids(
        space,
        kind="schema_raw",
        identity={"source": "ventes", "name": "customer.schema.yaml"},
    )
    assert (await _ok(app, KEY, "read_schema", by_kind))["schema"] == CUSTOMER
    customer = _ids(space, path=schema_path)
    draft = await _ok(app, AGENT, "read_schema", {**customer, "branch": "draft"})
    assert "email" in draft["schema"]["properties"] and draft["unpublished"] is True
    published = await _ok(app, KEY, "read_schema", customer)
    assert published["schema"] == CUSTOMER and published["unpublished"] is False
    assert published["kind"] == "raw" and len(published["version"]) == 40
    assert draft["version"] != published["version"]
    # Sans `branch`, l'agent lit lui aussi le publié.
    assert (await _ok(app, AGENT, "read_schema", customer))["schema"] == CUSTOMER


async def test_schema_et_source_introuvables_nommes(app: FastAPI, space: ApiSpace) -> None:
    for source in ("inconnue", "../etc"):
        unknown = _ids(space, kind="schema_raw", identity={"source": source})
        assert await _refusal(app, KEY, "list_documents", unknown) == "write_identity_invalid"
    for name in ("absent.schema.yaml", "a/b.schema.yaml"):
        path = f"sources/ventes/raw/{name}"
        assert await _refusal(app, KEY, "read_schema", _ids(space, path=path)) == (
            "schema_not_found"
        ), path
    for path in ("sources/ventes/raw/../manifest.yaml", "sources/ventes/manifest.yaml", "x.yaml"):
        assert await _refusal(app, KEY, "read_schema", _ids(space, path=path)) == (
            "schema_path_unknown"
        ), path


# --- Assets ------------------------------------------------------------------------------


async def test_assets_listes_et_lus_avec_leur_enrichissement(app: FastAPI, space: ApiSpace) -> None:
    # Ticket 56f3784a : ce que rendait list_assets, par list_documents.
    listed = await _ok(
        app,
        KEY,
        "list_documents",
        _ids(space, kind="schema_asset", identity={"source": "ventes"}),
    )
    docs = listed["documents"]
    assert [(a["identity"]["name"], a["retained"], a["orphan"]) for a in docs] == [
        ("customer.schema.yaml", "partial", False)
    ]
    assert docs[0]["path"] == "sources/ventes/assets/customer.schema.yaml"
    by_kind = await _ok(
        app,
        KEY,
        "read_schema",
        _ids(
            space,
            kind="schema_asset",
            identity={"source": "ventes", "name": "customer.schema.yaml"},
        ),
    )
    assert by_kind["kind"] == "asset" and by_kind["schema"]["x-businessName"] == "Client"

    # Ticket 06ef8679, critère 2 : le schéma de l'asset et, dans `extras`, tout ce
    # que rendait read_asset.
    read = await _ok(
        app, KEY, "read_schema", _ids(space, path="sources/ventes/assets/customer.schema.yaml")
    )
    assert read["kind"] == "asset" and read["schema"]["x-businessName"] == "Client"
    asset = read["extras"]
    assert asset["object"]["business_name"] == "Client"
    assert (asset["source"], asset["path"], asset["orphan"]) == (
        "ventes",
        "customer.schema.yaml",
        False,
    )
    fields = {f["name"]: f for f in asset["fields"]}
    assert fields["id"]["retained"] is True and fields["id"]["business_name"] == "Identifiant"
    assert fields["name"]["retained"] is False and "business_name" not in fields["name"]

    order = _ids(space, path="sources/ventes/assets/order.schema.yaml")
    assert await _refusal(app, KEY, "read_schema", order) == "schema_not_found"


async def test_regles_de_tri_et_revue(app: FastAPI, space: ApiSpace) -> None:
    rules = await _ok(
        app, KEY, "read_document", _ids(space, kind="regles_tri", identity={"source": "ventes"})
    )
    assert rules["content"][0]["pattern"] == "*Dto"

    review = await _ok(app, KEY, "get_review", _ids(space, source="ventes"))
    assert {"head_sha", "objects"} <= set(review)
    assert "base_sha" not in review  # plus de point de reprise (aad1d3c6)
    assert isinstance(review["objects"], list)


# --- Brouillon de la personne, versions --------------------------------------------------


async def test_brouillon_et_preparation_de_la_publication(app: FastAPI, space: ApiSpace) -> None:
    empty = await _ok(app, AGENT, "get_my_draft", _ids(space))
    assert empty == {"count": 0, "objects": []}

    await draft_write(app, space, "sources/ventes/assets/order.schema.yaml", {"title": "Order"})
    draft = await _ok(app, AGENT, "get_my_draft", _ids(space))
    assert draft["objects"] == [
        {
            "path": "sources/ventes/assets/order.schema.yaml",
            "state": "created",
            "changed_upstream": False,
        }
    ]

    readiness = await _ok(app, AGENT, "get_publish_readiness", _ids(space))
    assert set(readiness) == {"ok", "reasons", "conflicts"}
    # Sans identifiant personnel saisi, la publication n'est pas prête.
    assert readiness["ok"] is False and "credential_missing" in readiness["reasons"]


async def test_versions_d_une_branche(app: FastAPI, space: ApiSpace) -> None:
    versions = await _ok(app, KEY, "list_versions", _ids(space))
    assert versions == {"versions": []}


# --- Gouvernance ------------------------------------------------------------------------------


async def test_gouvernance_lue_avec_le_brouillon(
    app: FastAPI, client: AsyncClient, space: ApiSpace
) -> None:
    created = await client.post(f"{space.base}/governance/tags", json={"id": "gouvernance/pii"})
    assert created.status_code == 201, created.text

    draft = await _ok(app, AGENT, "list_documents", _ids(space, kind="tag", branch="draft"))
    assert [t["identity"]["id"] for t in draft["documents"]] == ["gouvernance/pii"]
    assert draft["documents"][0]["unpublished"] is True
    published = await _ok(app, KEY, "list_documents", _ids(space, kind="tag"))
    assert published["documents"] == []

    schemes = await _ok(app, KEY, "list_documents", _ids(space, kind="classification"))
    assert schemes["view"] == "published" and isinstance(schemes["documents"], list)


# --- Règles des espaces : erreurs nommées ------------------------------------------------------


async def test_branche_introuvable_ou_d_un_autre_espace(app: FastAPI, space: ApiSpace) -> None:
    assert await _refusal(
        app, KEY, "list_documents", _ids(space, kind="source", branch_id=str(uuid.uuid4()))
    ) == ("branch_not_found")
    assert await _refusal(
        app, KEY, "list_documents", _ids(space, kind="source", space_id=str(uuid.uuid4()))
    ) == ("space_not_found")


async def test_acces_au_depot_perdu_erreur_nommee(app: FastAPI, space: ApiSpace) -> None:
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "UPDATE git_spaces SET repo_url = 'file:///nulle-part/repo.git' WHERE id = $1",
            uuid.UUID(space.space_id),
        )
    # Copie locale oubliée : la lecture doit retourner au dépôt.
    shutil.rmtree(app.state.settings.work_volume_path, ignore_errors=True)
    error = await _refusal(app, KEY, "list_documents", _ids(space, kind="source"))
    assert error == "repo_access_lost"


async def test_arguments_invalides_refuses(app: FastAPI, space: ApiSpace) -> None:
    assert await _refusal(
        app, KEY, "list_documents", {"space_id": "pas-un-uuid", "kind": "source"}
    ) == ("invalid_arguments")


# --- Droits par espace (agent pour une personne) -------------------------------------------------


async def test_capacite_d_un_autre_espace_ne_vaut_pas_ici(app: FastAPI, space: ApiSpace) -> None:
    """La personne lit les sources grâce à une équipe rattachée à un AUTRE espace :
    l'outil lui est visible, mais refusé sur cet espace-ci."""
    async with app.state.pool.acquire() as conn:
        user_id = await conn.fetchval(
            "INSERT INTO users (email, role) VALUES ('ana@example.org', 'user') RETURNING id"
        )
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
        reader = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) "
            "VALUES ('lecteur', '{spaces:read,sources:read}') RETURNING id"
        )
        await conn.execute(
            "INSERT INTO space_team_profiles (space_id, team_id, profile_id) VALUES ($1, $2, $3)",
            other,
            team,
            reader,
        )
        lecture = await conn.fetchval("SELECT id FROM agent_profiles WHERE slug = 'lecture'")
    ana = McpCaller(kind="oauth", ref="a", agent_profile_id=lecture, user_id=user_id, client_id="c")

    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return ana if token == "ana" else None

    app.state.mcp_resolvers.append(resolver)
    assert (
        await _refusal(app, "ana", "list_documents", _ids(space, kind="source"))
        == "primitive_forbidden"
    )
