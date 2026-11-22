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
            "sources/ventes/raw/customer.json": json.dumps(CUSTOMER),
            "sources/ventes/raw/order.json": json.dumps({"title": "Order", "properties": {}}),
            "sources/ventes/assets/customer.json": json.dumps(CUSTOMER_ASSET),
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


async def test_cle_lit_le_publie_l_agent_voit_aussi_le_brouillon(
    app: FastAPI, space: ApiSpace
) -> None:
    # Source déclarée dans le brouillon de la personne seulement.
    await _draft_text(app, space, "sources/achats/manifest.yaml", _manifest("achats"))

    published = await _ok(app, KEY, "list_sources", _ids(space))
    assert published["view"] == "published"
    assert [s["slug"] for s in published["sources"]] == ["ventes"]

    draft = await _ok(app, AGENT, "list_sources", _ids(space))
    assert draft["view"] == "draft"
    by_slug = {s["slug"]: s for s in draft["sources"]}
    assert set(by_slug) == {"ventes", "achats"}
    assert by_slug["achats"]["unpublished_changes"] == 1
    assert by_slug["ventes"]["unpublished_changes"] == 0
    assert by_slug["ventes"]["connection"] == "gh"

    only_published = await _ok(app, AGENT, "list_sources", _ids(space, view="published"))
    assert [s["slug"] for s in only_published["sources"]] == ["ventes"]


async def test_cle_ne_peut_pas_demander_le_brouillon(app: FastAPI, space: ApiSpace) -> None:
    assert await _refusal(app, KEY, "list_sources", _ids(space, view="draft")) == (
        "draft_requires_person"
    )
    # Les primitives du brouillon ne sont même pas proposées à une clé.
    assert await _refusal(app, KEY, "get_my_draft", _ids(space)) == "primitive_forbidden"
    assert await _refusal(app, KEY, "get_publish_readiness", _ids(space)) == (
        "primitive_forbidden"
    )


# --- Schémas bruts ---------------------------------------------------------------------


async def test_schemas_bruts_listes_et_lus_avec_la_marque_du_brouillon(
    app: FastAPI, space: ApiSpace
) -> None:
    changed = {**CUSTOMER, "properties": {**CUSTOMER["properties"], "email": {"type": "string"}}}
    await draft_write(app, space, "sources/ventes/raw/customer.json", changed)

    listed = await _ok(app, AGENT, "list_raw_schemas", _ids(space, source="ventes"))
    by_path = {s["path"]: s for s in listed["schemas"]}
    assert set(by_path) == {"customer.json", "order.json"}
    assert by_path["customer.json"]["unpublished"] is True
    assert by_path["customer.json"]["fields"] == 3
    assert by_path["customer.json"]["retained"] == "partial"
    assert by_path["order.json"]["unpublished"] is False

    customer = _ids(space, source="ventes", path="customer.json")
    draft = await _ok(app, AGENT, "read_raw_schema", customer)
    assert "email" in draft["schema"]["properties"] and draft["unpublished"] is True
    published = await _ok(app, KEY, "read_raw_schema", customer)
    assert published["schema"] == CUSTOMER and published["unpublished"] is False


async def test_schema_et_source_introuvables_nommes(app: FastAPI, space: ApiSpace) -> None:
    assert await _refusal(app, KEY, "list_raw_schemas", _ids(space, source="inconnue")) == (
        "source_not_found"
    )
    assert await _refusal(app, KEY, "list_raw_schemas", _ids(space, source="../etc")) == (
        "source_not_found"
    )
    for path in ("absent.json", "../manifest.yaml", "a/b.json"):
        error = await _refusal(app, KEY, "read_raw_schema", _ids(space, source="ventes", path=path))
        assert error == "schema_not_found", path


# --- Assets ------------------------------------------------------------------------------


async def test_assets_listes_et_lus_avec_leur_enrichissement(
    app: FastAPI, space: ApiSpace
) -> None:
    listed = await _ok(app, KEY, "list_assets", _ids(space, source="ventes"))
    assert [(a["path"], a["retained"], a["orphan"]) for a in listed["assets"]] == [
        ("customer.json", "partial", False)
    ]

    asset = await _ok(app, KEY, "read_asset", _ids(space, source="ventes", path="customer.json"))
    assert asset["object"]["business_name"] == "Client"
    fields = {f["name"]: f for f in asset["fields"]}
    assert fields["id"]["retained"] is True and fields["id"]["business_name"] == "Identifiant"
    assert fields["name"]["retained"] is False and "business_name" not in fields["name"]

    order = _ids(space, source="ventes", path="order.json")
    assert await _refusal(app, KEY, "read_asset", order) == "asset_not_retained"


async def test_regles_de_tri_et_revue(app: FastAPI, space: ApiSpace) -> None:
    rules = await _ok(app, KEY, "get_tri_rules", _ids(space, source="ventes"))
    assert rules["rules"][0]["pattern"] == "*Dto"

    review = await _ok(app, KEY, "get_review", _ids(space, source="ventes"))
    assert {"base_sha", "head_sha", "up_to_date", "objects"} <= set(review)
    assert isinstance(review["objects"], list)


# --- Brouillon de la personne, versions --------------------------------------------------


async def test_brouillon_et_preparation_de_la_publication(app: FastAPI, space: ApiSpace) -> None:
    empty = await _ok(app, AGENT, "get_my_draft", _ids(space))
    assert empty == {"count": 0, "objects": []}

    await draft_write(app, space, "sources/ventes/assets/order.json", {"title": "Order"})
    draft = await _ok(app, AGENT, "get_my_draft", _ids(space))
    assert draft["objects"] == [
        {"path": "sources/ventes/assets/order.json", "state": "created", "changed_upstream": False}
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

    draft = await _ok(app, AGENT, "list_tags", _ids(space))
    assert [t["id"] for t in draft["tags"]] == ["gouvernance/pii"]
    assert draft["unpublished_changes"] >= 1
    published = await _ok(app, KEY, "list_tags", _ids(space))
    assert published["tags"] == [] and published["unpublished_changes"] == 0

    schemes = await _ok(app, KEY, "list_classifications", _ids(space))
    assert schemes["view"] == "published" and isinstance(schemes["schemes"], list)


# --- Règles des espaces : erreurs nommées ------------------------------------------------------


async def test_branche_introuvable_ou_d_un_autre_espace(app: FastAPI, space: ApiSpace) -> None:
    assert await _refusal(app, KEY, "list_sources", _ids(space, branch_id=str(uuid.uuid4()))) == (
        "branch_not_found"
    )
    assert await _refusal(app, KEY, "list_sources", _ids(space, space_id=str(uuid.uuid4()))) == (
        "space_not_found"
    )


async def test_acces_au_depot_perdu_erreur_nommee(app: FastAPI, space: ApiSpace) -> None:
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "UPDATE git_spaces SET repo_url = 'file:///nulle-part/repo.git' WHERE id = $1",
            uuid.UUID(space.space_id),
        )
    # Copie locale oubliée : la lecture doit retourner au dépôt.
    shutil.rmtree(app.state.settings.work_volume_path, ignore_errors=True)
    error = await _refusal(app, KEY, "list_sources", _ids(space))
    assert error == "repo_access_lost"


async def test_arguments_invalides_refuses(app: FastAPI, space: ApiSpace) -> None:
    assert await _refusal(app, KEY, "list_sources", {"space_id": "pas-un-uuid"}) == (
        "invalid_arguments"
    )


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
    assert await _refusal(app, "ana", "list_sources", _ids(space)) == "primitive_forbidden"
