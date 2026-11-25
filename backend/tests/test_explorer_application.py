"""Explorateur raw : objets module et vue « Application » (epic « Cartographie
applicative dans raw », ticket 0fa620be) — routes de la vue et de l'aperçu d'un
module, calculées sur l'index de l'Explorateur."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.parsers.application import dump_functions, module_key, module_schema
from ossie_studio.parsers.conventions import dump_schema, schema_file
from ossie_studio.sources.explorer import clear_caches
from tests.space_fixture import ApiSpace, forge_edit, make_api_space
from tests.test_raw_explorer import _manifest, _tech

RAW = "sources/app/raw"
API = "src/Boutique.Api/Boutique.Api.csproj"
DOMAIN = "src/Boutique.Domain/Boutique.Domain.csproj"
TESTS = "tests/Boutique.Tests/Boutique.Tests.csproj"
KNOWN = [API, DOMAIN, TESTS]


def _module(project: str, nature: str, deps: tuple[str, ...] = ()) -> tuple[str, str]:
    schema = module_schema(
        project=project,
        name=project.rsplit("/", 1)[-1].removesuffix(".csproj"),
        language="csharp",
        nature=nature,
        targets=["net8.0"],
        dependencies=deps,
        known_projects=KNOWN,
    )
    return schema_file(module_key(project)), dump_schema(schema)


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


def _files() -> dict[str, str]:
    api_file, api = _module(API, "web", (DOMAIN, "../Externe/Outils.csproj"))
    domain_file, domain = _module(DOMAIN, "library")
    tests_file, tests = _module(TESTS, "test", (API, DOMAIN))
    controller = {
        "methods": [
            {
                "name": "Lire",
                "visibility": "public",
                "parameters": [{"name": "id", "type": "Guid"}],
                "returns": {"type": "Commande"},
                "entryPoint": {"kind": "http", "verb": "GET", "route": "/api/commandes/{id}"},
            },
            {"name": "Calculer", "visibility": "internal"},
            {"name": "Aide", "visibility": "public"},
        ]
    }
    return {
        "sources/app/manifest.yaml": _manifest("app"),
        f"{RAW}/{api_file}": api,
        f"{RAW}/{domain_file}": domain,
        f"{RAW}/{tests_file}": tests,
        f"{RAW}/commandescontroller.schema.yaml": _class("CommandesController"),
        f"{RAW}/commandescontroller.extend.yaml": dump_functions(controller),
        f"{RAW}/nettoyage.schema.yaml": _class("Nettoyage"),
        f"{RAW}/nettoyage.extend.yaml": dump_functions({"entryPoint": {"kind": "task"}}),
        f"{RAW}/commande.schema.yaml": _class("Commande"),
        f"{RAW}/{module_key(API)}.extend.yaml": dump_functions(
            {
                "functions": [
                    {
                        "name": "Vider",
                        "visibility": "public",
                        "entryPoint": {"kind": "http", "verb": "DELETE", "route": "/paniers/{id}"},
                    }
                ]
            }
        ),
        f"{RAW}/manifest-csharp.yaml": _tech(
            {
                "src/Boutique.Api/Controllers/CommandesController.cs": (
                    ["commandescontroller.schema.yaml"],
                    "csharp",
                ),
                "src/Boutique.Api/Nettoyage.cs": (["nettoyage.schema.yaml"], "csharp"),
                "src/Boutique.Domain/Commande.cs": (["commande.schema.yaml"], "csharp"),
            }
        ),
    }


@pytest.fixture(autouse=True)
def _fresh_caches() -> None:
    clear_caches()


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    space = await make_api_space(client, app, tmp_path)
    forge_edit(space, _files())
    return space


def _by_title(view: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["title"]: m for m in view["modules"]}


async def test_vue_application_modules_dependances_compteurs(sp: ApiSpace) -> None:
    r = await sp.client.get(f"{sp.base}/raw-explorer/app/application")
    assert r.status_code == 200, r.text
    modules = _by_title(r.json())
    assert list(modules) == ["Boutique.Api", "Boutique.Domain", "Boutique.Tests"]
    api = modules["Boutique.Api"]
    assert (api["nature"], api["targets"], api["folder"]) == ("web", ["net8.0"], "src/Boutique.Api")
    assert api["dependencies"] == [
        {"id": None, "project": "../Externe/Outils.csproj"},
        {"id": schema_file(module_key(DOMAIN)), "project": DOMAIN},
    ]
    assert api["dependents"] == [schema_file(module_key(TESTS))]
    # Deux classes (contrôleur, service) ; trois points d'entrée (GET, tâche, DELETE
    # minimal API), deux routes HTTP ; deux méthodes publiques (Lire, Aide).
    assert api["counts"] == {
        "classes": 2,
        "entry_points": 3,
        "http": 2,
        "methods": 2,
        "functions": 1,
    }
    assert modules["Boutique.Domain"]["counts"]["classes"] == 1
    assert sorted(modules["Boutique.Domain"]["dependents"]) == sorted(
        [schema_file(module_key(API)), schema_file(module_key(TESTS))]
    )


async def test_le_module_dans_l_arborescence_et_le_compteur_de_l_arbre(sp: ApiSpace) -> None:
    tree = (await sp.client.get(f"{sp.base}/raw-explorer/app/tree")).json()
    assert [(m["title"], m["folder"]) for m in tree["modules"]] == [
        ("Boutique.Api", "src/Boutique.Api"),
        ("Boutique.Domain", "src/Boutique.Domain"),
        ("Boutique.Tests", "tests/Boutique.Tests"),
    ]
    folder = (
        await sp.client.get(
            f"{sp.base}/raw-explorer/app/folder", params={"path": "src/Boutique.Api"}
        )
    ).json()
    # Le module se range dans le dossier de son fichier de projet.
    assert ("Boutique.Api", "module") in [(o["title"], o["kind"]) for o in folder["objects"]]


async def test_apercu_d_un_module(sp: ApiSpace) -> None:
    r = await sp.client.get(
        f"{sp.base}/raw-explorer/app/module", params={"file": schema_file(module_key(API))}
    )
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["project"] == API
    assert [d["title"] for d in detail["dependencies"]] == [None, "Boutique.Domain"]
    assert [d["title"] for d in detail["dependents"]] == ["Boutique.Tests"]
    points = {(p["verb"], p["route"], p["method"], p["owner"]) for p in detail["entry_points"]}
    assert points == {
        (None, None, None, "Nettoyage"),
        ("GET", "/api/commandes/{id}", "Lire", "CommandesController"),
        ("DELETE", "/paniers/{id}", "Vider", None),
    }
    # Pas encore comparé au contrat (ticket « Relier les points d'entrée… »).
    assert {p["contract"] for p in detail["entry_points"]} == {None}
    [controller] = detail["classes"]
    assert controller["title"] == "CommandesController"
    assert controller["methods"] == [
        {"name": "Aide", "signature": "Aide()", "entry": False},
        {"name": "Lire", "signature": "Lire(id: Guid): Commande", "entry": True},
    ]


async def test_module_inconnu_404_et_droits(app: FastAPI, sp: ApiSpace) -> None:
    missing = await sp.client.get(
        f"{sp.base}/raw-explorer/app/module", params={"file": "commande.schema.yaml"}
    )
    assert missing.status_code == 404
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anonymous:
        refused = await anonymous.get(f"{sp.base}/raw-explorer/app/application")
    assert refused.status_code == 401
