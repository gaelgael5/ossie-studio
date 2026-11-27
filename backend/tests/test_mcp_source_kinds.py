"""Les sources et ce qu'elles portent, lues par les primitives communes (epic
« Primitives MCP — lectures communes »). Genre `source` (ticket 88fd2cc5) : le
manifeste d'une source, en lecture seule, à la place de `list_sources`."""

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
from ossie_studio.mcp_surface.catalog import CATALOG
from ossie_studio.sources.manifest import SourceManifest, dump_manifest
from tests.mcp_helpers import call_tool
from tests.space_fixture import ApiSpace, admin_workdir, forge_edit, make_api_space

KEY = "sources-key"
AGENT = "sources-agent"
SECRET = "valeur-secrete-7f3a91"
MIGRATIONS = Path(__file__).resolve().parents[1] / "src/ossie_studio/db/migrations"


def _manifest(slug: str, type_: str = "git", connection: str = "gh", **params: Any) -> str:
    return dump_manifest(
        SourceManifest(
            slug=slug,
            label=slug.capitalize(),
            type=type_,
            connection=connection,
            parameters=params or {"repository": "o/r", "branch": "main", "parsers": ["csharp"]},
        )
    )


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    """Côté publié : une source git « ventes », une source de base « entrepot » et un
    répertoire de source au manifeste cassé. Une clé (profil Lecture) et un agent
    qui peut aussi tenter d'écrire."""
    api_space = await make_api_space(client, app, tmp_path)
    forge_edit(
        api_space,
        {
            "sources/ventes/manifest.yaml": _manifest("ventes"),
            "sources/entrepot/manifest.yaml": _manifest(
                "entrepot", "postgres", "pg-entrepot", database="appdb", schemas=["public"]
            ),
            "sources/casse/manifest.yaml": "slug: casse\nlabel: [\n",
        },
    )
    # Un secret bien réel dans le coffre : il ne doit jamais ressortir.
    created = await client.post(
        "/api/secrets",
        json={"slug": "pg-entrepot", "label": "PG", "category": "postgres", "value": SECRET},
    )
    assert created.status_code == 201, created.text
    async with app.state.pool.acquire() as conn:
        lecture = await conn.fetchval("SELECT id FROM agent_profiles WHERE slug = 'lecture'")
        writer = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives, all_read) VALUES ('s', $1, true) "
            "RETURNING id",
            ["write_documents", "patch_document"],
        )
        admin_id = await conn.fetchval("SELECT id FROM users WHERE is_bootstrap")
    callers = {
        KEY: McpCaller(kind="key", ref="k", agent_profile_id=lecture),
        AGENT: McpCaller(kind="oauth", ref="a", agent_profile_id=writer, user_id=admin_id),
    }

    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return callers.get(token)

    app.state.mcp_resolvers.append(resolver)
    return api_space


async def _call(app: FastAPI, token: str, tool: str, space: ApiSpace, **args: Any) -> Any:
    ids = {"space_id": space.space_id, "branch_id": space.branch_id}
    result = await call_tool(app, token, tool, {**ids, **args})
    if result.is_error:
        return True, json.loads(result.content[0].text)
    return False, result.structured_content


def _migration(name: str) -> str:
    return (MIGRATIONS / name).read_text(encoding="utf-8")


async def _draft_text(app: FastAPI, space: ApiSpace, rel: str, text: str) -> None:
    workdir = await admin_workdir(app, space)

    def write(root: Path) -> None:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    await workdir.commit_to_branch(
        uuid.UUID(space.space_id), "work/principale", space.url, "x", None, "a", "a", "écrit", write
    )


# --- Genre « source » (ticket 88fd2cc5) -----------------------------------------------------


async def test_lister_les_sources(app: FastAPI, space: ApiSpace) -> None:
    """Critère 1 : les champs de `list_sources` ; un manifeste illisible est listé
    avec son erreur, sans faire échouer la liste. Filtre par type."""
    error, listed = await _call(app, KEY, "list_documents", space, kind="source")
    assert not error, listed
    by_slug = {d["identity"]["source"]: d for d in listed["documents"]}
    assert set(by_slug) == {"casse", "entrepot", "ventes"}
    ventes = by_slug["ventes"]
    assert ventes["ok"] is True
    assert (ventes["label"], ventes["type"], ventes["connection"]) == ("Ventes", "git", "gh")
    assert ventes["parameters"]["repository"] == "o/r"
    assert ventes["path"] == "sources/ventes/manifest.yaml" and ventes["version"]
    assert ventes["unpublished_changes"] == 0
    assert by_slug["casse"]["ok"] is False and by_slug["casse"]["error"]
    error, typed = await _call(
        app, KEY, "list_documents", space, kind="source", filters={"type": "postgres"}
    )
    assert [d["identity"]["source"] for d in typed["documents"]] == ["entrepot"]


async def test_lire_une_source(app: FastAPI, space: ApiSpace) -> None:
    """Critère 2 : le manifeste complet, son chemin et sa version ; une source
    inconnue → document_not_found ; un manifeste illisible se lit en texte."""
    error, read = await _call(
        app, KEY, "read_document", space, kind="source", identity={"source": "ventes"}
    )
    assert not error, read
    assert read["path"] == "sources/ventes/manifest.yaml" and read["version"]
    assert read["content"]["slug"] == "ventes"
    assert read["content"]["parameters"]["parsers"] == ["csharp"]
    error, missing = await _call(
        app, KEY, "read_document", space, kind="source", identity={"source": "inconnue"}
    )
    assert error and missing["error"] == "document_not_found"
    error, broken = await _call(
        app, KEY, "read_document", space, kind="source", identity={"source": "casse"}
    )
    assert not error and broken["content"] == "slug: casse\nlabel: [\n"


async def test_aucun_secret_dans_les_reponses(app: FastAPI, space: ApiSpace) -> None:
    """Critère 3 : la source de base ne porte que le nom de sa connexion."""
    _, listed = await _call(app, KEY, "list_documents", space, kind="source")
    _, read = await _call(
        app, KEY, "read_document", space, kind="source", identity={"source": "entrepot"}
    )
    assert read["content"]["connection"] == "pg-entrepot"
    assert SECRET not in json.dumps(listed) + json.dumps(read)


async def test_une_source_ne_s_ecrit_pas(app: FastAPI, space: ApiSpace) -> None:
    """Critère 4 : refus `write_kind_read_only`, qui nomme les trois actions."""
    document = {"kind": "source", "identity": {"source": "neuve"}, "content": {"slug": "neuve"}}
    error, body = await _call(
        app, AGENT, "write_documents", space, message="source", documents=[document]
    )
    assert error
    [refusal] = body["errors"]
    assert refusal["code"] == "write_kind_read_only"
    for action in ("declare_source", "update_source", "delete_source"):
        assert action in refusal["message"]
    error, body = await _call(
        app,
        AGENT,
        "patch_document",
        space,
        kind="source",
        identity={"source": "ventes"},
        version="x",
        operations=[{"op": "set", "path": "$.label", "value": "Autre"}],
    )
    assert error and "declare_source" in json.dumps(body)


async def test_source_du_brouillon(app: FastAPI, space: ApiSpace) -> None:
    """Critère 5 : une source déclarée dans le brouillon apparaît avec branch='draft',
    marquée non publiée ; une clé d'API ne la voit pas."""
    await _draft_text(app, space, "sources/achats/manifest.yaml", _manifest("achats"))
    _, draft = await _call(app, AGENT, "list_documents", space, kind="source", branch="draft")
    achats = next(d for d in draft["documents"] if d["identity"]["source"] == "achats")
    assert achats["unpublished"] is True and achats["unpublished_changes"] == 1
    _, published = await _call(app, KEY, "list_documents", space, kind="source")
    assert "achats" not in {d["identity"]["source"] for d in published["documents"]}


async def test_list_sources_retiree(app: FastAPI, space: ApiSpace) -> None:
    """Critères 6 et 7 : plus au catalogue ; le genre est listé ; les profils migrés."""
    assert "list_sources" not in {p.id for p in CATALOG}
    result = await call_tool(app, KEY, "list_write_kinds", {})
    kinds = result.structured_content
    source = next(k for k in kinds["kinds"] if k["kind"] == "source")
    assert source["writable"] is False
    assert source["produced_by"] == ["declare_source", "update_source", "delete_source"]
    assert [f["name"] for f in source["list_filters"]] == ["type"]
    migration = _migration("0047_list_sources_removed.sql")
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives) VALUES ('ancien', $1) RETURNING id",
            ["list_sources", "list_spaces"],
        )
        await conn.execute(migration)
        primitives = await conn.fetchval(
            "SELECT primitives FROM agent_profiles WHERE id = $1", profile
        )
    assert sorted(primitives) == ["list_documents", "list_spaces", "read_document"]
