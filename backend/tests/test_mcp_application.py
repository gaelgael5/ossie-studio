"""La cartographie applicative lue par les primitives communes (epic « Primitives MCP
— lectures communes », tickets dcc52af7, 2c069134, 15c43ed1) : modules par le genre
`schema_raw`, méthodes et fonctions par `extension_raw`, points d'entrée par
`point_entree` ; par clé d'API (vue publiée) et par agent connecté (vue brouillon)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import yaml
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.mcp_surface import write_guide
from ossie_studio.mcp_surface.caller import McpCaller
from ossie_studio.mcp_surface.catalog import CATALOG
from ossie_studio.parsers.application import (
    dump_functions,
    module_key,
    module_schema,
    validate_functions,
)
from ossie_studio.parsers.conventions import dump_schema, schema_file
from ossie_studio.sources.collect_core import TechEntry, TechManifest, dump_tech_manifest
from ossie_studio.sources.manifest import SourceManifest, dump_manifest
from tests.mcp_helpers import call_tool
from tests.space_fixture import ApiSpace, draft_write, forge_edit, make_api_space

KEY = "app-key"
AGENT = "app-agent"
RAW = "sources/boutique/raw"
MIGRATIONS = Path(__file__).resolve().parents[1] / "src/ossie_studio/db/migrations"
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


# --- Les modules par le genre schema_raw (ticket dcc52af7) ---------------------------------


def _raw(space: ApiSpace, source: str = "boutique", **filters: Any) -> dict[str, Any]:
    ids = {"space_id": space.space_id, "branch_id": space.branch_id}
    return {
        **ids,
        "document_type": "schema_raw",
        "identity": {"source": source},
        "filters": filters,
    }


async def test_modules_par_schema_raw(app: FastAPI, space: ApiSpace) -> None:
    """Ce que rendait l'ancienne liste des modules : nature, cibles, langage,
    dépendances dans les deux sens (un projet hors de la source est externe),
    compteurs."""
    data = await _ok(app, KEY, "list_documents", _raw(space, kind="module"))
    assert data["view"] == "published"
    modules = {m["title"]: m for m in data["documents"]}
    assert len(modules) == 6
    api = modules["Boutique.Api"]
    name = schema_file(module_key(PROJECTS["Api"]))
    assert api["identity"] == {"source": "boutique", "name": name}
    assert api["path"] == f"{RAW}/{name}" and api["version"]
    assert (api["nature"], api["targets"], api["language"]) == ("web", ["net8.0"], "csharp")
    assert api["project"] == PROJECTS["Api"]
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
    # Un objet qui n'est pas un module garde son résumé.
    classes = await _ok(app, KEY, "list_documents", _raw(space, kind="class"))
    commande = next(c for c in classes["documents"] if c["title"] == "Commande")
    assert "counts" not in commande and commande["kind"] == "class"


async def test_les_objets_d_un_module(app: FastAPI, space: ApiSpace) -> None:
    """Le filtre « module », par titre ou par nom de schéma : les objets que
    l'Explorateur range dans ce module."""
    by_title = await _ok(app, KEY, "list_documents", _raw(space, module="Boutique.Api"))
    assert [o["title"] for o in by_title["documents"]] == ["CommandesController"]
    name = schema_file(module_key(PROJECTS["Domain"]))
    by_name = await _ok(app, KEY, "list_documents", _raw(space, module=name))
    assert [o["title"] for o in by_name["documents"]] == ["Commande"]
    unknown = await call_tool(app, KEY, "list_documents", _raw(space, module="Inconnu"))
    body = json.loads(unknown.content[0].text)
    assert body["error"] == "list_filter_invalid" and body["path"] == "$.filters.module"
    no_source = {**_raw(space, module="Boutique.Api"), "identity": {}}
    refused = await call_tool(app, KEY, "list_documents", no_source)
    assert json.loads(refused.content[0].text)["error"] == "list_filter_invalid"


def _migration(name: str) -> str:
    return (MIGRATIONS / name).read_text(encoding="utf-8")


async def test_list_modules_retiree(app: FastAPI, space: ApiSpace) -> None:
    """Plus au catalogue ; schema_raw déclare ses deux filtres ; profils migrés."""
    assert "list_modules" not in {p.id for p in CATALOG}
    kinds = (await call_tool(app, KEY, "list_document_types", {})).structured_content[
        "document_types"
    ]
    raw = next(k for k in kinds if k["document_type"] == "schema_raw")
    assert [f["name"] for f in raw["list_filters"]] == ["kind", "module"]
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives) VALUES ('ancien', $1) RETURNING id",
            ["list_modules", "get_module"],
        )
        await conn.execute(_migration("0049_list_modules_removed.sql"))
        primitives = await conn.fetchval(
            "SELECT primitives FROM agent_profiles WHERE id = $1", profile
        )
    assert sorted(primitives) == ["get_module", "list_documents", "read_document"]


# --- Un module en quatre lectures communes (ticket 15c43ed1, ex-get_module) ----------------


def _entries(space: ApiSpace, **filters: Any) -> dict[str, Any]:
    ids = {"space_id": space.space_id, "branch_id": space.branch_id}
    return {
        **ids,
        "document_type": "point_entree",
        "identity": {"source": "boutique"},
        "filters": filters,
    }


async def test_un_module_en_quatre_lectures(app: FastAPI, space: ApiSpace) -> None:
    """Tout ce que rendait get_module : le module, ses objets, ses fonctions libres,
    ses points d'entrée."""
    name = schema_file(module_key(PROJECTS["Api"]))
    draft = {"branch": "draft"}
    ids = {"space_id": space.space_id, "branch_id": space.branch_id, **draft}
    identity = {"source": "boutique", "name": name}
    module = await _ok(
        app, AGENT, "read_document", {**ids, "document_type": "schema_raw", "identity": identity}
    )
    assert module["view"] == "draft"
    assert module["content"]["x-module"]["project"] == PROJECTS["Api"]
    objects = await _ok(app, AGENT, "list_documents", {**_raw(space, module=name), **draft})
    assert [o["title"] for o in objects["documents"]] == ["CommandesController"]
    functions = await _ok(app, AGENT, "read_document", {**_ext(space, name), **draft})
    assert [f["name"] for f in functions["content"]["functions"]] == ["Vider"]
    points = await _ok(app, AGENT, "list_documents", {**_entries(space, module=name), **draft})
    routes = {(p["verb"], p["route"], p["contract_status"]) for p in points["documents"]}
    assert routes == {
        ("GET", "/api/commandes/{id}", "linked"),
        ("PUT", "/api/commandes/{id}/statut", "absent"),
        ("DELETE", "/paniers/{id}", "not_compared"),
    }


# --- Méthodes et fonctions : genre extension_raw (ticket 2c069134) -------------------------


def _ext(space: ApiSpace, name: str) -> dict[str, Any]:
    ids = {"space_id": space.space_id, "branch_id": space.branch_id}
    identity = {"source": "boutique", "name": name}
    return {**ids, "document_type": "extension_raw", "identity": identity}


async def test_methodes_d_une_classe(app: FastAPI, space: ApiSpace) -> None:
    """Toutes les méthodes, telles que le parseur les a écrites ; un objet sans
    méthode n'a pas d'extension (document_not_found)."""
    data = await _ok(app, KEY, "read_document", _ext(space, "commandescontroller.schema.yaml"))
    assert data["path"] == f"{RAW}/commandescontroller.extend.yaml" and data["version"]
    methods = {m["name"]: m for m in data["content"]["methods"]}
    assert methods["Lire"]["entryPoint"]["verb"] == "GET"
    assert methods["Lire"]["parameters"] == [{"name": "id", "type": "Guid"}]
    assert methods["Lire"]["entryPoint"]["contract"]["status"] == "linked"
    assert (methods["Calculer"]["visibility"], methods["Calculer"]["static"]) == ("internal", True)
    assert "entryPoint" not in methods["Calculer"]
    missing = await call_tool(app, KEY, "read_document", _ext(space, "commande.schema.yaml"))
    assert json.loads(missing.content[0].text)["error"] == "document_not_found"


async def test_fonctions_libres_d_un_module(app: FastAPI, space: ApiSpace) -> None:
    name = schema_file(module_key(PROJECTS["Api"]))
    data = await _ok(app, KEY, "read_document", _ext(space, name))
    assert [f["name"] for f in data["content"]["functions"]] == ["Vider"]


async def test_lister_les_extensions(app: FastAPI, space: ApiSpace) -> None:
    """Les objets qui ont une extension, et ceux d'un module."""
    args = {**_ext(space, ""), "identity": {"source": "boutique"}}
    every = await _ok(app, KEY, "list_documents", args)
    by_title = {d["title"]: d for d in every["documents"]}
    assert set(by_title) == {"CommandesController", "Boutique.Api"}
    controller = by_title["CommandesController"]
    assert (controller["methods"], controller["functions"], controller["entry_point"]) == (
        3,
        0,
        False,
    )
    in_api = await _ok(app, KEY, "list_documents", {**args, "filters": {"module": "Boutique.Api"}})
    assert [d["title"] for d in in_api["documents"]] == ["CommandesController"]


async def test_une_extension_ne_s_ecrit_pas(app: FastAPI, space: ApiSpace) -> None:
    async with app.state.pool.acquire() as conn:
        writer = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives, all_read) VALUES ('w', $1, true) "
            "RETURNING id",
            ["write_documents"],
        )
        admin_id = await conn.fetchval("SELECT id FROM users WHERE is_bootstrap")
    caller = McpCaller(kind="oauth", ref="w", agent_profile_id=writer, user_id=admin_id)

    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return caller if token == "writer" else None

    app.state.mcp_resolvers.append(resolver)
    document = {
        "document_type": "extension_raw",
        "identity": {"source": "boutique", "name": "commande.schema.yaml"},
        "content": {"methods": []},
    }
    ids = {"space_id": space.space_id, "branch_id": space.branch_id}
    result = await call_tool(
        app, "writer", "write_documents", {**ids, "message": "x", "documents": [document]}
    )
    [refusal] = json.loads(result.content[0].text)["errors"]
    assert refusal["code"] == "document_type_read_only" and "collect_source" in refusal["message"]


def test_l_exemple_du_standard_passe_le_schema_du_parseur() -> None:
    text = write_guide.standards()["extension-raw"]["markdown"]
    [block] = re.findall(r"```yaml content\n(.*?)```", text, re.DOTALL)
    validate_functions(yaml.safe_load(block))


async def test_get_functions_retiree(app: FastAPI, space: ApiSpace) -> None:
    assert "get_functions" not in {p.id for p in CATALOG}
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives) VALUES ('ancien', $1) RETURNING id",
            ["get_functions"],
        )
        await conn.execute(_migration("0050_get_functions_removed.sql"))
        primitives = await conn.fetchval(
            "SELECT primitives FROM agent_profiles WHERE id = $1", profile
        )
    assert sorted(primitives) == ["list_documents", "read_document"]


# --- Genre point_entree (ticket 15c43ed1, ex-list_entry_points) ----------------------------


async def test_routes_absentes_du_contrat(app: FastAPI, space: ApiSpace) -> None:
    """« Quelles routes du code manquent au contrat ? » en une lecture ; filtres par
    module, état au contrat (not_compared aussi) et nature."""
    absent = await _ok(app, KEY, "list_documents", _entries(space, contract_status="absent"))
    assert [(p["route"], p["method"], p["module"]) for p in absent["documents"]] == [
        ("/api/commandes/{id}/statut", "Changer", "Boutique.Api")
    ]
    every = await _ok(app, KEY, "list_documents", _entries(space))
    assert len(every["documents"]) == 3
    domain = await _ok(app, KEY, "list_documents", _entries(space, module="Boutique.Domain"))
    assert domain["documents"] == []
    never = await _ok(app, KEY, "list_documents", _entries(space, contract_status="not_compared"))
    assert [(p["route"], p["owner"]) for p in never["documents"]] == [("/paniers/{id}", None)]
    tasks = await _ok(app, KEY, "list_documents", _entries(space, kind="task"))
    assert tasks["documents"] == []


async def test_lire_un_point_d_entree(app: FastAPI, space: ApiSpace) -> None:
    """L'identité vient de la liste, sans chemin ; elle ne change pas d'une collecte à
    l'autre quand le code ne change pas."""
    every = await _ok(app, KEY, "list_documents", _entries(space, contract_status="linked"))
    [listed] = every["documents"]
    assert listed["identity"] == {
        "source": "boutique",
        "owner": "commandescontroller.schema.yaml",
        "method": "Lire",
        "endpoint": "GET /api/commandes/{id}",
    }
    ids = {"space_id": space.space_id, "branch_id": space.branch_id}
    read = await _ok(
        app,
        KEY,
        "read_document",
        {**ids, "document_type": "point_entree", "identity": listed["identity"]},
    )
    assert read["content"]["contract_operations"][0]["operationId"] == "lire"
    assert read["version"] == listed["version"]
    assert read["path"] == f"{RAW}/commandescontroller.extend.yaml"
    # Une nouvelle publication qui ne touche pas au code : même identité, même version.
    forge_edit(space, {f"{RAW}/autre.schema.yaml": _class("Autre")})
    again = await _ok(app, KEY, "list_documents", _entries(space, contract_status="linked"))
    assert again["documents"][0]["identity"] == listed["identity"]
    assert again["documents"][0]["version"] == listed["version"]
    module = {**listed["identity"], "owner": schema_file(module_key(PROJECTS["Api"]))}
    missing = await call_tool(
        app, KEY, "read_document", {**ids, "document_type": "point_entree", "identity": module}
    )
    assert json.loads(missing.content[0].text)["error"] == "document_not_found"


async def test_un_point_d_entree_ne_s_ecrit_pas_et_primitives_retirees(
    app: FastAPI, space: ApiSpace
) -> None:
    assert not {"list_entry_points", "get_module"} & {p.id for p in CATALOG}
    assert len(CATALOG) == 32
    kinds = (await call_tool(app, KEY, "list_document_types", {})).structured_content[
        "document_types"
    ]
    point = next(k for k in kinds if k["document_type"] == "point_entree")
    assert point["writable"] is False and point["produced_by"] == ["collect_source"]
    assert [f["name"] for f in point["list_filters"]] == ["module", "contract_status", "kind"]
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives) VALUES ('ancien', $1) RETURNING id",
            ["list_entry_points", "get_module", "list_spaces"],
        )
        await conn.execute(_migration("0051_entry_points_and_module_removed.sql"))
        primitives = await conn.fetchval(
            "SELECT primitives FROM agent_profiles WHERE id = $1", profile
        )
    assert sorted(primitives) == ["list_documents", "list_spaces", "read_document"]


# --- Critère 5 : erreurs nommées, source sans module ----------------------------------------


async def test_erreurs_nommees_et_source_sans_module(app: FastAPI, space: ApiSpace) -> None:
    unknown = await call_tool(app, KEY, "list_documents", _entries(space, module="Inconnu"))
    assert json.loads(unknown.content[0].text)["error"] == "list_filter_invalid"
    absent = await call_tool(app, KEY, "list_documents", _raw(space, "absente", kind="module"))
    assert json.loads(absent.content[0].text)["error"] == "write_identity_invalid"
    empty = await _ok(app, KEY, "list_documents", _raw(space, "vide", kind="module"))
    assert empty["documents"] == []


# --- Plus de troncature : toutes les méthodes se lisent -------------------------------------


async def test_toutes_les_methodes_sans_troncature(app: FastAPI, space: ApiSpace) -> None:
    many = {"methods": [{"name": f"M{i:03d}", "visibility": "public"} for i in range(205)]}
    forge_edit(space, {f"{RAW}/commande.extend.yaml": dump_functions(many)})
    data = await _ok(app, KEY, "read_document", _ext(space, "commande.schema.yaml"))
    assert len(data["content"]["methods"]) == 205


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
    draft_args = {**_raw(space, kind="module"), "branch": "draft"}
    draft = await _ok(app, AGENT, "list_documents", draft_args)
    published = await _ok(app, KEY, "list_documents", _raw(space, kind="module"))
    batch = next(m for m in draft["documents"] if m["title"] == "Boutique.Batch")
    assert batch["unpublished"] is True and batch["nature"] == "console"
    assert "Boutique.Batch" not in {m["title"] for m in published["documents"]}
    assert path  # le module Worker reste dans les deux vues
