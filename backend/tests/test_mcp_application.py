"""Primitives MCP de la cartographie applicative (ticket 72ce32b3) : modules,
fonctions et points d'entrée, par clé d'API (vue publiée) et par agent connecté
(vue brouillon), sur l'index de l'Explorateur raw."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.mcp_surface.caller import McpCaller
from ossie_studio.parsers.application import dump_functions, module_key, module_schema
from ossie_studio.parsers.conventions import dump_schema, schema_file
from ossie_studio.sources.collect_core import TechEntry, TechManifest, dump_tech_manifest
from ossie_studio.sources.manifest import SourceManifest, dump_manifest
from tests.mcp_helpers import call_tool
from tests.space_fixture import ApiSpace, draft_write, forge_edit, make_api_space

KEY = "app-key"
AGENT = "app-agent"
RAW = "sources/boutique/raw"
NAMES = ["Api", "Application", "Domain", "Infra", "Worker", "Tests"]
PROJECTS = {n: f"src/Boutique.{n}/Boutique.{n}.csproj" for n in NAMES}
DEPS = {
    "Api": ["Application", "Domain"],
    "Application": ["Domain", "Infra"],
    "Infra": ["Domain"],
    "Worker": ["Domain"],
    "Tests": ["Api"],
}
NATURES = {"Api": "web", "Worker": "worker", "Tests": "test"}


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


def _module(name: str) -> tuple[str, str]:
    deps = [PROJECTS[d] for d in DEPS.get(name, [])]
    if name == "Api":
        deps.append("../Partage/Outils.csproj")
    schema = module_schema(
        project=PROJECTS[name],
        name=f"Boutique.{name}",
        language="csharp",
        nature=NATURES.get(name, "library"),
        targets=["net8.0"],
        dependencies=deps,
        known_projects=PROJECTS.values(),
    )
    return schema_file(module_key(PROJECTS[name])), dump_schema(schema)


def _class(name: str) -> str:
    return dump_schema(
        {
            "$id": f"{name.lower()}.schema.yaml",
            "title": name,
            "type": "object",
            "x-kind": "class",
            "x-language": "csharp",
            "x-visibility": "public",
        }
    )


def _http(verb: str, route: str, status: str | None = None) -> dict[str, Any]:
    point: dict[str, Any] = {"kind": "http", "verb": verb, "route": route}
    if status == "linked":
        operation = {"file": "contrat.yaml", "operationId": "lire", "verb": verb, "path": route}
        point["contract"] = {"status": "linked", "operations": [operation]}
    elif status == "absent":
        point["contract"] = {"status": "absent"}
    return point


LIRE = {
    "name": "Lire",
    "visibility": "public",
    "parameters": [{"name": "id", "type": "Guid"}],
    "returns": {"type": "Commande"},
    "entryPoint": _http("GET", "/api/commandes/{id}", "linked"),
}
CHANGER = {
    "name": "Changer",
    "visibility": "public",
    "entryPoint": _http("PUT", "/api/commandes/{id}/statut", "absent"),
}
CONTROLLER = {
    "methods": [LIRE, CHANGER, {"name": "Calculer", "visibility": "internal", "static": True}]
}


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    api_space = await make_api_space(client, app, tmp_path)
    files = {"sources/boutique/manifest.yaml": _manifest("boutique")}
    for name in NAMES:
        path, text = _module(name)
        files[f"{RAW}/{path}"] = text
    files[f"{RAW}/commandescontroller.schema.yaml"] = _class("CommandesController")
    files[f"{RAW}/commandescontroller.extend.yaml"] = dump_functions(CONTROLLER)
    files[f"{RAW}/commande.schema.yaml"] = _class("Commande")
    vider = {
        "name": "Vider",
        "visibility": "public",
        "entryPoint": _http("DELETE", "/paniers/{id}"),
    }
    files[f"{RAW}/{module_key(PROJECTS['Api'])}.extend.yaml"] = dump_functions(
        {"functions": [vider]}
    )
    files[f"{RAW}/manifest-csharp.yaml"] = dump_tech_manifest(
        TechManifest(
            entries={
                "src/Boutique.Api/Controllers/CommandesController.cs": TechEntry(
                    "x", ["commandescontroller.schema.yaml"], "main", "csharp"
                ),
                "src/Boutique.Domain/Commande.cs": TechEntry(
                    "x", ["commande.schema.yaml"], "main", "csharp"
                ),
            }
        )
    )
    # Une source sans module : liste vide, pas d'erreur.
    files["sources/vide/manifest.yaml"] = _manifest("vide")
    files["sources/vide/raw/seul.schema.yaml"] = _class("Seul")
    forge_edit(api_space, files)
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
    return {"space_id": space.space_id, "branch_id": space.branch_id, "source": "boutique", **extra}


async def _ok(app: FastAPI, token: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await call_tool(app, token, tool, args)
    assert not result.is_error, result.content[0].text
    data: dict[str, Any] = result.structured_content
    return data


async def _error(app: FastAPI, tool: str, args: dict[str, Any]) -> str:
    result = await call_tool(app, KEY, tool, args)
    assert result.is_error
    code: str = json.loads(result.content[0].text)["error"]
    return code


# --- Critère 1 : list_modules, dépendances dans les deux sens -------------------------------


async def test_list_modules(app: FastAPI, space: ApiSpace) -> None:
    data = await _ok(app, KEY, "list_modules", _args(space))
    assert data["view"] == "published" and data["truncated"] is False
    modules = {m["title"]: m for m in data["modules"]}
    assert len(modules) == 6
    api = modules["Boutique.Api"]
    assert (api["nature"], api["targets"], api["language"]) == ("web", ["net8.0"], "csharp")
    assert {d.get("title") or d["project"] for d in api["depends_on"]} == {
        "Boutique.Application",
        "Boutique.Domain",
        "../Partage/Outils.csproj",
    }
    assert {"project": "../Partage/Outils.csproj", "external": True} in api["depends_on"]
    assert [d["title"] for d in api["used_by"]] == ["Boutique.Tests"]
    assert {d["title"] for d in modules["Boutique.Domain"]["used_by"]} == {
        "Boutique.Api",
        "Boutique.Application",
        "Boutique.Infra",
        "Boutique.Worker",
    }
    assert api["counts"] == {"classes": 1, "entry_points": 3, "public_methods": 2}


# --- Critère 2 : get_module ---------------------------------------------------------------


async def test_get_module(app: FastAPI, space: ApiSpace) -> None:
    data = await _ok(app, AGENT, "get_module", _args(space, branch="draft", module="Boutique.Api"))
    assert data["view"] == "draft"
    assert data["module"]["project"] == PROJECTS["Api"]
    assert [o["title"] for o in data["objects"]] == ["CommandesController"]
    assert [(f["name"], f["entry_point"]["route"]) for f in data["free_functions"]] == [
        ("Vider", "/paniers/{id}")
    ]
    routes = {(p["verb"], p["route"], p["contract_status"]) for p in data["entry_points"]}
    assert routes == {
        ("GET", "/api/commandes/{id}", "linked"),
        ("PUT", "/api/commandes/{id}/statut", "absent"),
        ("DELETE", "/paniers/{id}", "not_compared"),
    }
    # Par son chemin aussi.
    by_path = await _ok(
        app, KEY, "get_module", _args(space, module=schema_file(module_key(PROJECTS["Api"])))
    )
    assert by_path["module"]["title"] == "Boutique.Api"


# --- Critère 3 : get_functions --------------------------------------------------------------


async def test_get_functions(app: FastAPI, space: ApiSpace) -> None:
    data = await _ok(
        app, KEY, "get_functions", _args(space, path="commandescontroller.schema.yaml")
    )
    methods = {m["name"]: m for m in data["methods"]}
    assert methods["Lire"]["entry_point"]["verb"] == "GET"
    assert methods["Lire"]["parameters"] == [{"name": "id", "type": "Guid"}]
    assert (methods["Calculer"]["visibility"], methods["Calculer"]["static"]) == ("internal", True)
    assert "entry_point" not in methods["Calculer"]
    # Un objet sans fonction : liste vide.
    empty = await _ok(app, KEY, "get_functions", _args(space, path="commande.schema.yaml"))
    assert empty["methods"] == []


# --- Critère 4 : list_entry_points filtré par état au contrat ------------------------------


async def test_routes_absentes_du_contrat(app: FastAPI, space: ApiSpace) -> None:
    absent = await _ok(app, KEY, "list_entry_points", _args(space, contract_status="absent"))
    assert [(p["route"], p["method"], p["module"]) for p in absent["entry_points"]] == [
        ("/api/commandes/{id}/statut", "Changer", "Boutique.Api")
    ]
    every = await _ok(app, KEY, "list_entry_points", _args(space))
    assert len(every["entry_points"]) == 3
    one = await _ok(app, KEY, "list_entry_points", _args(space, module="Boutique.Domain"))
    assert one["entry_points"] == []


# --- Critère 5 : erreurs nommées, source sans module ----------------------------------------


async def test_erreurs_nommees_et_source_sans_module(app: FastAPI, space: ApiSpace) -> None:
    assert await _error(app, "get_module", _args(space, module="Inconnu")) == "module_not_found"
    assert (
        await _error(app, "get_functions", _args(space, path="absent.schema.yaml"))
        == "schema_not_found"
    )
    assert await _error(app, "list_modules", _args(space, source="absente")) == "source_not_found"
    empty = await _ok(app, KEY, "list_modules", _args(space, source="vide"))
    assert empty["modules"] == []


# --- Critère 6 : troncature annoncée ------------------------------------------------------


async def test_reponse_tronquee(app: FastAPI, space: ApiSpace) -> None:
    many = {"methods": [{"name": f"M{i:03d}", "visibility": "public"} for i in range(205)]}
    forge_edit(space, {f"{RAW}/commande.extend.yaml": dump_functions(many)})
    data = await _ok(app, KEY, "get_functions", _args(space, path="commande.schema.yaml"))
    assert data["truncated"] is True
    assert len(data["methods"]) == 200


# --- Critère 7 : OAuth (vue brouillon) et clé d'API (vue publiée) ----------------------------


async def test_le_brouillon_de_la_personne_compte(app: FastAPI, space: ApiSpace) -> None:
    path, _text = _module("Worker")
    extra = module_schema(
        project="src/Boutique.Batch/Boutique.Batch.csproj",
        name="Boutique.Batch",
        language="csharp",
        nature="console",
    )
    await draft_write(app, space, f"{RAW}/{extra['$id']}", extra)
    draft = await _ok(app, AGENT, "list_modules", _args(space, branch="draft"))
    published = await _ok(app, KEY, "list_modules", _args(space))
    assert "Boutique.Batch" in {m["title"] for m in draft["modules"]}
    assert "Boutique.Batch" not in {m["title"] for m in published["modules"]}
    assert path  # le module Worker reste dans les deux vues
