"""Modules C# : les `.csproj` deviennent des objets raw avec leurs dépendances
(epic « Cartographie applicative dans raw », ticket e0fa8519)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ossie_studio.models import GitSpace
from ossie_studio.parsers.application import module_key, module_of
from ossie_studio.parsers.conventions import load_schema, schema_file
from ossie_studio.sources.collect import SourceCollector
from ossie_studio.sources.collect_core import parse_tech_manifest
from ossie_studio.sources.csharp_modules import nature_of
from ossie_studio.sources.schema_resolve import read_project_map
from tests.test_collect_service import _base_branch, _collect, _make_bare_with, _push, _raw
from tests.test_collect_service import setup as setup  # fixture réutilisée


def _csproj(sdk: str = "Microsoft.NET.Sdk", *, refs: tuple[str, ...] = (), extra: str = "") -> str:
    items = "".join(f'<ProjectReference Include="{r}" />' for r in refs)
    return (
        f'<Project Sdk="{sdk}">\n'
        f"  <PropertyGroup><TargetFramework>net8.0</TargetFramework>{extra}</PropertyGroup>\n"
        f"  <ItemGroup>{items}</ItemGroup>\n"
        "</Project>\n"
    )


API = "src/Boutique.Api/Boutique.Api.csproj"
WORKER = "src/Boutique.Worker/Boutique.Worker.csproj"
DOMAIN = "src/Boutique.Domain/Boutique.Domain.csproj"
INFRA = "src/Boutique.Infra/Boutique.Infra.csproj"
CONSOLE = "tools/Boutique.Outil/Boutique.Outil.csproj"
TESTS = "tests/Boutique.Tests/Boutique.Tests.csproj"

SOURCE: dict[str, str] = {
    API: _csproj(
        "Microsoft.NET.Sdk.Web",
        refs=(
            "..\\Boutique.Domain\\Boutique.Domain.csproj",
            "../Boutique.Infra/Boutique.Infra.csproj",
            "../../../Externe/Outils.csproj",
        ),
    ),
    WORKER: _csproj(
        "Microsoft.NET.Sdk.Worker", refs=("../Boutique.Domain/Boutique.Domain.csproj",)
    ),
    DOMAIN: _csproj(),
    INFRA: _csproj(
        refs=("../Boutique.Domain/Boutique.Domain.csproj",),
        extra="<TargetFrameworks>net8.0;netstandard2.0</TargetFrameworks>",
    ),
    CONSOLE: _csproj(extra="<OutputType>Exe</OutputType>"),
    TESTS: (
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
        "</PropertyGroup><ItemGroup>"
        '<PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.11.0" />'
        '<PackageReference Include="xunit" Version="2.9.0" />'
        '<ProjectReference Include="../../src/Boutique.Api/Boutique.Api.csproj" />'
        "</ItemGroup></Project>"
    ),
    "src/Boutique.Api/Controllers/CommandesController.cs": (
        "namespace Boutique.Api.Controllers;\n"
        "public class CommandesController { public int Total { get; set; } }\n"
    ),
}


def _module(raw: Path, project: str) -> dict[str, Any]:
    data = load_schema((raw / schema_file(module_key(project))).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _modules(raw: Path) -> dict[str, dict[str, Any]]:
    out = {}
    for file in raw.glob("module-*.schema.yaml"):
        data = load_schema(file.read_text(encoding="utf-8"))
        out[data["x-module"]["project"]] = data
    return out


# --- Lecture unique des .csproj : nature et cibles ------------------------------------------


def test_nature_et_cibles_lues_dans_le_projet() -> None:
    projects = read_project_map({p: t.encode() for p, t in SOURCE.items()}).projects
    natures = {path: nature_of(project) for path, project in projects.items()}
    assert natures == {
        API: "web",
        WORKER: "worker",
        DOMAIN: "library",
        INFRA: "library",
        CONSOLE: "console",
        TESTS: "test",
    }
    assert projects[INFRA].targets == ("net8.0", "netstandard2.0")
    # La carte des homonymes ne change pas : seules les références internes comptent.
    refs = read_project_map({p: t.encode() for p, t in SOURCE.items()}).refs
    assert refs["src/Boutique.Api"] == {"src/Boutique.Domain", "src/Boutique.Infra"}


# --- Critères 1 à 6 : à la collecte -----------------------------------------------------------


async def test_six_projets_six_modules(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    src, work = _make_bare_with(tmp_path, "src", SOURCE)
    report = await _collect(collector, space, src)
    raw = _raw(tmp_path, space)

    # Critère 1 : six objets module, avec la bonne nature.
    modules = _modules(raw)
    assert {p: m["x-module"]["nature"] for p, m in modules.items()} == {
        API: "web",
        WORKER: "worker",
        DOMAIN: "library",
        INFRA: "library",
        CONSOLE: "console",
        TESTS: "test",
    }
    api = modules[API]
    assert (api["title"], api["x-language"], api["x-module"]["targets"]) == (
        "Boutique.Api",
        "csharp",
        ["net8.0"],
    )
    assert api["x-source"] == [{"file": API}]

    # Critère 2 : dépendances reliées, la référence hors de la source non résolue.
    assert api["x-module"]["dependencies"] == [
        {"project": "../Externe/Outils.csproj"},
        {"project": DOMAIN, "module": schema_file(module_key(DOMAIN))},
        {"project": INFRA, "module": schema_file(module_key(INFRA))},
    ]

    # Critère 3 : la classe du contrôleur appartient au module Boutique.Api.
    manifest = parse_tech_manifest((raw / "manifest-csharp.yaml").read_text(encoding="utf-8"))
    origin = next(
        file
        for file, entry in manifest.entries.items()
        if any("commandes" in d for d in entry.destinations)
    )
    projects = {module_key(p): p for p in modules}
    assert module_of(origin, projects) == module_key(API)

    # Le compte rendu de collecte : chaque .csproj traité ; les vues créées.
    statuses = {f.path: f.statut for f in report.files}
    assert statuses[API] == "traité"
    assert (raw / f"{module_key(API)}.view.yaml").is_file()

    # Critère 6 : collecte sans changement, aucun module réécrit.
    again = await _collect(collector, space, src)
    assert again.local_commit is None
    assert {f.path: f.statut for f in again.files}[API] == "inchangé"

    # Un projet modifié : seul son module est réécrit.
    (work / DOMAIN).write_text(_csproj(extra="<OutputType>Exe</OutputType>"), encoding="utf-8")
    _push(work)
    changed = await _collect(collector, space, src)
    assert changed.modified == 1
    assert _module(raw, DOMAIN)["x-module"]["nature"] == "console"

    # Un projet supprimé : son module part, la dépendance d'Api vers lui devient externe.
    (work / INFRA).unlink()
    _push(work)
    await _collect(collector, space, src)
    assert INFRA not in _modules(raw)
    assert {"project": INFRA} in _module(raw, API)["x-module"]["dependencies"]


async def test_csproj_illisible_signale_les_autres_produits(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    src, _ = _make_bare_with(
        tmp_path,
        "src",
        {DOMAIN: _csproj(), "src/Casse/Casse.csproj": "ceci n'est pas un projet", INFRA: _csproj()},
    )
    report = await _collect(collector, space, src)
    files = {f.path: f for f in report.files}
    assert (files["src/Casse/Casse.csproj"].statut, files["src/Casse/Casse.csproj"].message) == (
        "erreur",
        "csproj_unreadable",
    )
    assert set(_modules(_raw(tmp_path, space))) == {DOMAIN, INFRA}


async def test_source_sans_csproj_aucun_module(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    foo = "namespace App; public class Foo { public int A { get; set; } }"
    src, _ = _make_bare_with(tmp_path, "src", {"foo.cs": foo})
    await _collect(collector, space, src)
    raw = _raw(tmp_path, space)
    assert _modules(raw) == {}
    assert not list(raw.glob("*.view.yaml"))
    assert (raw / "app-foo.schema.yaml").is_file()
