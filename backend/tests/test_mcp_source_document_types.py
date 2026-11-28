"""Les sources et ce qu'elles portent, lues par les primitives communes (epic
« Primitives MCP — lectures communes »). Genre `source` (ticket 88fd2cc5) : le
manifeste d'une source, en lecture seule, à la place de `list_sources`. Genre
`contrat` (ticket e806a6b4) : les contrats OpenAPI déposés, à la place de
`list_contracts`, avec en plus la lecture de leur contenu."""

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
CONTRACT = "openapi: 3.0.3\ninfo:\n  title: API boutique\n  version: 1.0.0\npaths: {}\n"
CONTRACT_INDEX = """version: 2
contracts:
- version: v1
  file: v1.yaml
  original_file: boutique.yaml
  openapi: 3.0.3
  declared_version: 1.0.0
  schema_count: 0
  operation_count: 0
  uploaded_at: '2026-11-27T10:00:00+00:00'
  author: gael@example.org
"""
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
            "sources/boutique/manifest.yaml": dump_manifest(
                SourceManifest("boutique", "Boutique", "openapi", "", {})
            ),
            "sources/boutique/contrats/contrats.yaml": CONTRACT_INDEX,
            "sources/boutique/contrats/v1.yaml": CONTRACT,
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
    error, listed = await _call(app, KEY, "list_documents", space, document_type="source")
    assert not error, listed
    by_slug = {d["identity"]["source"]: d for d in listed["documents"]}
    assert set(by_slug) == {"boutique", "casse", "entrepot", "ventes"}
    ventes = by_slug["ventes"]
    assert ventes["ok"] is True
    assert (ventes["label"], ventes["type"], ventes["connection"]) == ("Ventes", "git", "gh")
    assert ventes["parameters"]["repository"] == "o/r"
    assert ventes["path"] == "sources/ventes/manifest.yaml" and ventes["version"]
    assert ventes["unpublished_changes"] == 0
    assert by_slug["casse"]["ok"] is False and by_slug["casse"]["error"]
    error, typed = await _call(
        app, KEY, "list_documents", space, document_type="source", filters={"type": "postgres"}
    )
    assert [d["identity"]["source"] for d in typed["documents"]] == ["entrepot"]


async def test_lire_une_source(app: FastAPI, space: ApiSpace) -> None:
    """Critère 2 : le manifeste complet, son chemin et sa version ; une source
    inconnue → document_not_found ; un manifeste illisible se lit en texte."""
    error, read = await _call(
        app, KEY, "read_document", space, document_type="source", identity={"source": "ventes"}
    )
    assert not error, read
    assert read["path"] == "sources/ventes/manifest.yaml" and read["version"]
    assert read["content"]["slug"] == "ventes"
    assert read["content"]["parameters"]["parsers"] == ["csharp"]
    error, missing = await _call(
        app, KEY, "read_document", space, document_type="source", identity={"source": "inconnue"}
    )
    assert error and missing["error"] == "document_not_found"
    error, broken = await _call(
        app, KEY, "read_document", space, document_type="source", identity={"source": "casse"}
    )
    assert not error and broken["content"] == "slug: casse\nlabel: [\n"


async def test_aucun_secret_dans_les_reponses(app: FastAPI, space: ApiSpace) -> None:
    """Critère 3 : la source de base ne porte que le nom de sa connexion."""
    _, listed = await _call(app, KEY, "list_documents", space, document_type="source")
    _, read = await _call(
        app, KEY, "read_document", space, document_type="source", identity={"source": "entrepot"}
    )
    assert read["content"]["connection"] == "pg-entrepot"
    assert SECRET not in json.dumps(listed) + json.dumps(read)


async def test_une_source_ne_s_ecrit_pas(app: FastAPI, space: ApiSpace) -> None:
    """Critère 4 : refus `document_type_read_only`, qui nomme les trois actions."""
    document = {
        "document_type": "source",
        "identity": {"source": "neuve"},
        "content": {"slug": "neuve"},
    }
    error, body = await _call(
        app, AGENT, "write_documents", space, message="source", documents=[document]
    )
    assert error
    [refusal] = body["errors"]
    assert refusal["code"] == "document_type_read_only"
    for action in ("declare_source", "update_source", "delete_source"):
        assert action in refusal["message"]
    error, body = await _call(
        app,
        AGENT,
        "patch_document",
        space,
        document_type="source",
        identity={"source": "ventes"},
        version="x",
        operations=[{"op": "set", "path": "$.label", "value": "Autre"}],
    )
    assert error and "declare_source" in json.dumps(body)


async def test_source_du_brouillon(app: FastAPI, space: ApiSpace) -> None:
    """Critère 5 : une source déclarée dans le brouillon apparaît avec branch='draft',
    marquée non publiée ; une clé d'API ne la voit pas."""
    await _draft_text(app, space, "sources/achats/manifest.yaml", _manifest("achats"))
    _, draft = await _call(
        app, AGENT, "list_documents", space, document_type="source", branch="draft"
    )
    achats = next(d for d in draft["documents"] if d["identity"]["source"] == "achats")
    assert achats["unpublished"] is True and achats["unpublished_changes"] == 1
    _, published = await _call(app, KEY, "list_documents", space, document_type="source")
    assert "achats" not in {d["identity"]["source"] for d in published["documents"]}


async def test_list_sources_retiree(app: FastAPI, space: ApiSpace) -> None:
    """Critères 6 et 7 : plus au catalogue ; le genre est listé ; les profils migrés."""
    assert "list_sources" not in {p.id for p in CATALOG}
    result = await call_tool(app, KEY, "list_document_types", {})
    kinds = result.structured_content
    source = next(k for k in kinds["document_types"] if k["document_type"] == "source")
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


# --- Genre « contrat » (ticket e806a6b4) ----------------------------------------------------


async def test_lister_et_lire_les_contrats(app: FastAPI, space: ApiSpace) -> None:
    """Critères 1 et 2 : les champs de `list_contracts`, avec chemin et version ; le
    contenu d'un contrat se lit ; version inconnue → document_not_found ; source
    inconnue → refus nommé."""
    boutique = {"source": "boutique"}
    error, listed = await _call(
        app, KEY, "list_documents", space, document_type="contrat", identity=boutique
    )
    assert not error, listed
    [v1] = listed["documents"]
    assert v1["identity"] == {"source": "boutique", "version": "v1"}
    assert v1["path"] == "sources/boutique/contrats/v1.yaml" and v1["version"]
    assert (v1["original_file"], v1["openapi"], v1["declared_version"]) == (
        "boutique.yaml",
        "3.0.3",
        "1.0.0",
    )
    assert v1["author"] == "gael@example.org" and v1["collected"] is False
    error, read = await _call(
        app,
        KEY,
        "read_document",
        space,
        document_type="contrat",
        identity={**boutique, "version": "v1"},
    )
    assert not error, read
    assert read["content"]["info"]["title"] == "API boutique"
    assert read["version"] == v1["version"]
    error, missing = await _call(
        app,
        KEY,
        "read_document",
        space,
        document_type="contrat",
        identity={**boutique, "version": "v9"},
    )
    assert error and missing["error"] == "document_not_found"
    error, unknown = await _call(
        app, KEY, "list_documents", space, document_type="contrat", identity={"source": "inconnue"}
    )
    assert error and unknown["error"] == "write_identity_invalid"


async def test_source_non_openapi_sans_contrat(app: FastAPI, space: ApiSpace) -> None:
    """Critère 3."""
    _, listed = await _call(
        app, KEY, "list_documents", space, document_type="contrat", identity={"source": "ventes"}
    )
    assert listed["documents"] == []


async def test_un_contrat_ne_s_ecrit_pas(app: FastAPI, space: ApiSpace) -> None:
    """Critère 4 : refus qui nomme deposit_contract et remove_contract."""
    identity = {"source": "boutique", "version": "v2"}
    document = {"document_type": "contrat", "identity": identity, "content": {"openapi": "3.0.3"}}
    error, body = await _call(
        app, AGENT, "write_documents", space, message="contrat", documents=[document]
    )
    assert error
    [refusal] = body["errors"]
    assert refusal["code"] == "document_type_read_only"
    assert "deposit_contract" in refusal["message"] and "remove_contract" in refusal["message"]


async def test_contrat_du_brouillon(app: FastAPI, space: ApiSpace) -> None:
    """Critère 5 : un contrat déposé dans le brouillon apparaît, marqué non publié ;
    une clé d'API ne le voit pas."""
    index = CONTRACT_INDEX + CONTRACT_INDEX.split("contracts:\n", 1)[1].replace("v1", "v2")
    await _draft_text(app, space, "sources/boutique/contrats/contrats.yaml", index)
    await _draft_text(app, space, "sources/boutique/contrats/v2.yaml", CONTRACT)
    args = {"document_type": "contrat", "identity": {"source": "boutique"}}
    _, draft = await _call(app, AGENT, "list_documents", space, branch="draft", **args)
    by_version = {d["identity"]["version"]: d for d in draft["documents"]}
    assert set(by_version) == {"v1", "v2"} and by_version["v2"]["unpublished"] is True
    _, published = await _call(app, KEY, "list_documents", space, **args)
    assert [d["identity"]["version"] for d in published["documents"]] == ["v1"]


async def test_list_contracts_retiree(app: FastAPI, space: ApiSpace) -> None:
    """Critères 6 et 7."""
    assert "list_contracts" not in {p.id for p in CATALOG}
    result = await call_tool(app, KEY, "list_document_types", {})
    contrat = next(
        k for k in result.structured_content["document_types"] if k["document_type"] == "contrat"
    )
    assert contrat["writable"] is False
    assert contrat["produced_by"] == ["deposit_contract", "remove_contract"]
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives) VALUES ('ancien', $1) RETURNING id",
            ["list_contracts"],
        )
        await conn.execute(_migration("0048_list_contracts_removed.sql"))
        primitives = await conn.fetchval(
            "SELECT primitives FROM agent_profiles WHERE id = $1", profile
        )
    assert sorted(primitives) == ["list_documents", "read_document"]
