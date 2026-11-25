"""Projet SQL Server comme module (epic « Cartographie applicative dans raw »,
ticket 30c55418) : chaque `.sqlproj` donne un objet module ; ses tables et vues
lui appartiennent ; ses procédures et fonctions T-SQL sont les fonctions du
module ; une `ProjectReference` donne une dépendance."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ossie_studio.models import GitSpace
from ossie_studio.parsers.application import module_key
from ossie_studio.parsers.base import FileRow, PluginContext, PluginResult
from ossie_studio.parsers.conventions import load_schema, schema_file, validate_schema
from ossie_studio.parsers.sqlserver_project import FUNCTIONS_INDEX, SqlServerProjectParser
from ossie_studio.parsers.tsql_routines import read_routine
from ossie_studio.sources.collect import SourceCollector
from ossie_studio.sources.explorer import clear_caches, collect_source_entries
from ossie_studio.sources.explorer_application import application_view, members_by_module
from tests.test_collect_service import _base_branch, _make_bare_with, _push, _raw
from tests.test_collect_service import setup as setup  # fixture réutilisée
from tests.test_sqlserver_project import FakeRaw

PROJECT = "db/Boutique.Database/Boutique.Database.sqlproj"
FOLDER = "db/Boutique.Database"
SQLPROJ = (
    '<Project Sdk="Microsoft.Build.Sql/1.0.0"><PropertyGroup>'
    "<DSP>Microsoft.Data.Tools.Schema.Sql.Sql160DatabaseSchemaProvider</DSP>"
    "</PropertyGroup></Project>"
)
TABLES = [f"T{i}" for i in range(1, 10)]

PROCEDURES = {
    "LireCommande": """CREATE PROCEDURE [dbo].[LireCommande]
    @Id UNIQUEIDENTIFIER,
    @Statut NVARCHAR(50) = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.T1 WHERE Id = @Id;
END""",
    "Purger": """CREATE OR ALTER PROC ventes.Purger @Avant DATETIME2
AS
BEGIN
  BEGIN TRY
    DECLARE c CURSOR FOR SELECT Id FROM dbo.T1;
  END TRY
  BEGIN CATCH
    THROW;
  END CATCH
END""",
    "Total": """CREATE FUNCTION dbo.Total(@CommandeId INT, @Remise AS DECIMAL(5, 2) = 0)
RETURNS DECIMAL(18,2)
AS
BEGIN
  RETURN (SELECT SUM(x) FROM dbo.T2 WHERE x = @CommandeId);
END""",
    "Lignes": """CREATE FUNCTION [dbo].[Lignes] (@CommandeId INT)
RETURNS @t TABLE (a INT, b NVARCHAR(10))
AS BEGIN INSERT @t SELECT 1, 'a'; RETURN; END""",
}


def _project_files() -> dict[str, str]:
    files = {PROJECT: SQLPROJ}
    for name in TABLES:
        files[f"{FOLDER}/Tables/{name}.sql"] = (
            f"CREATE TABLE [dbo].[{name}] ([Id] INT NOT NULL, [x] INT NULL);"
        )
    files[f"{FOLDER}/Views/V1.sql"] = "CREATE VIEW dbo.V1 AS SELECT Id FROM dbo.T1;"
    files[f"{FOLDER}/Views/V2.sql"] = "CREATE VIEW dbo.V2 AS SELECT Id, x FROM dbo.T2;"
    for name, text in PROCEDURES.items():
        files[f"{FOLDER}/Programmabilite/{name}.sql"] = text
    return files


def _run(files: dict[str, str], raw: FakeRaw | None = None) -> PluginResult:
    contents = {p: c.encode("utf-8") for p, c in files.items()}
    return SqlServerProjectParser().run(
        PluginContext(
            files=[FileRow(path=p, crc="x") for p in contents],
            contents=contents,
            raw=raw or FakeRaw(),
            params={},
            force=False,
            branch="main",
        )
    )


def _writes(result: PluginResult) -> dict[str, str]:
    return {w.path: w.content for w in result.writes}


def _functions(result: PluginResult) -> dict[str, dict[str, Any]]:
    index = load_schema(_writes(result)[FUNCTIONS_INDEX])
    assert index["language"] == "tsql"
    return {f["name"]: f for entry in index["files"].values() for f in entry["functions"]}


# --- Critère 1 : un module, ses objets, ses fonctions -------------------------------------


def test_module_et_fonctions_du_projet() -> None:
    result = _run(_project_files())
    assert result.errors == []
    writes = _writes(result)
    module = load_schema(writes[schema_file(module_key(PROJECT))])
    validate_schema(module)
    assert module["title"] == "Boutique.Database"
    assert (module["x-kind"], module["x-language"]) == ("module", "tsql")
    assert module["x-module"] == {
        "project": PROJECT,
        "nature": "sql",
        "targets": ["Sql160"],
        "dependencies": [],
    }
    functions = _functions(result)
    assert set(functions) == {"dbo.LireCommande", "ventes.Purger", "dbo.Total", "dbo.Lignes"}
    lire = functions["dbo.LireCommande"]
    assert lire["annotations"] == ["PROCEDURE"]
    assert lire["parameters"] == [
        {
            "name": "@Id",
            "type": "UNIQUEIDENTIFIER",
            "schema": {"type": "string", "format": "uuid", "x-db-type": "UNIQUEIDENTIFIER"},
        },
        {
            "name": "@Statut",
            "type": "NVARCHAR(50) OUTPUT",
            "schema": {"type": "string", "maxLength": 50, "x-db-type": "NVARCHAR(50)"},
            "optional": True,
        },
    ]
    assert lire["source"] == {"file": f"{FOLDER}/Programmabilite/LireCommande.sql", "line": 1}
    assert "returns" not in lire
    total = functions["dbo.Total"]
    assert total["annotations"] == ["FUNCTION"]
    assert total["returns"] == {
        "type": "DECIMAL(18,2)",
        "schema": {"type": "number", "x-db-type": "DECIMAL(18,2)"},
    }
    assert [p["type"] for p in total["parameters"]] == ["INT", "DECIMAL(5, 2)"]
    assert functions["dbo.Lignes"]["returns"] == {"type": "TABLE"}
    # Le corps n'est jamais lu : un corps que sqlglot ne sait pas lire ne gêne rien.
    assert functions["ventes.Purger"]["parameters"][0]["type"] == "DATETIME2"


# --- Critère 2 : une procédure mal formée signalée, les autres écrites ---------------------


def test_procedure_mal_formee_signalee() -> None:
    files = _project_files()
    files[f"{FOLDER}/Programmabilite/Casse.sql"] = (
        "CREATE PROCEDURE dbo.Casse @a INT @b INT\nAS SELECT 1"
    )
    result = _run(files)
    [error] = result.errors
    assert (error.source, error.cause) == (
        f"{FOLDER}/Programmabilite/Casse.sql",
        "sql_routine_unreadable",
    )
    assert error.detail.startswith("line 1: ")
    assert len(_functions(result)) == 4


# --- Critère 3 : tables et vues inchangées ------------------------------------------------


def test_tables_et_vues_inchangees() -> None:
    """Les procédures et le module ne changent rien aux schémas des tables et des
    vues (leur forme exacte est couverte par test_sqlserver_project)."""
    files = _project_files()
    without = {
        p: c for p, c in files.items() if "/Programmabilite/" not in p and not p.endswith("proj")
    }
    with_all = _writes(_run(files))
    alone = _writes(_run(without))
    objects = [schema_file(f"dbo-{n.lower()}") for n in [*TABLES, "V1", "V2"]]
    assert all(with_all[o] == alone[o] for o in objects)


# --- ProjectReference : dépendance entre modules --------------------------------------------


def test_reference_entre_projets() -> None:
    commun = "db/Commun/Commun.sqlproj"
    files = {
        commun: '<Project Sdk="Microsoft.Build.Sql"></Project>',
        "db/Commun/T.sql": "CREATE TABLE dbo.Pays ([Code] CHAR(2) NOT NULL);",
        PROJECT: (
            '<Project Sdk="Microsoft.Build.Sql"><ItemGroup>'
            '<ProjectReference Include="..\\Commun\\Commun.sqlproj" />'
            '<ProjectReference Include="..\\..\\..\\Ailleurs\\Ailleurs.sqlproj" />'
            "</ItemGroup></Project>"
        ),
        f"{FOLDER}/T.sql": "CREATE TABLE dbo.Commande ([Id] INT NOT NULL);",
    }
    writes = _writes(_run(files))
    module = load_schema(writes[schema_file(module_key(PROJECT))])
    assert module["x-module"]["dependencies"] == [
        {"project": "../Ailleurs/Ailleurs.sqlproj"},
        {"project": commun, "module": schema_file(module_key(commun))},
    ]
    assert "targets" not in module["x-module"]


# --- Conventions : l'exemple de la documentation est ce que produit le parseur ------------


def test_exemple_des_conventions() -> None:
    from tests.test_application_formats import _doc_example

    example = _doc_example("Projet SQL Server : module `sql`, procédures et fonctions")
    from ossie_studio.parsers.sqlserver_project import routine_function

    path = f"{FOLDER}/Programmabilite/LireCommande.sql"
    produced = routine_function(read_routine(PROCEDURES["LireCommande"]), path, 1)
    assert example == {"functions": [produced]}


# --- Collecte de bout en bout : le socle range les fonctions dans le module ---------------


async def _collect(collector: SourceCollector, space: GitSpace, url: str):  # type: ignore[no-untyped-def]
    return await collector.collect(
        space=space,
        branch_slug="work/principale",
        branch_label="principale",
        space_username="x",
        space_token=None,
        source_slug="api",
        source_clone_url=url,
        source_branch="main",
        source_username="x",
        source_token=None,
        author_name="a@a",
        author_email="a@a",
        force=False,
        parsers=["sqlserver-projet"],
    )


async def test_collecte_module_objets_et_fonctions(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    url, work = _make_bare_with(tmp_path, "src", _project_files())
    report = await _collect(collector, space, url)
    assert report.errors == []
    raw = _raw(tmp_path, space)
    extend = raw / f"{module_key(PROJECT)}.extend.yaml"
    functions = load_schema(extend.read_text("utf-8"))["functions"]
    assert [f["name"] for f in functions] == [
        "dbo.Lignes",
        "dbo.LireCommande",
        "dbo.Total",
        "ventes.Purger",
    ]
    # Onze objets (neuf tables, deux vues) appartiennent au module.
    clear_caches()
    entries = collect_source_entries(raw.parents[2], "api")
    module_id = schema_file(module_key(PROJECT))
    assert len(members_by_module(entries)[module_id]) == 11
    [view] = application_view(entries)["modules"]
    assert (view["nature"], view["counts"]["classes"], view["counts"]["functions"]) == (
        "sql",
        11,
        4,
    )
    # Sans changement : rien n'est réécrit.
    assert (await _collect(collector, space, url)).local_commit is None
    # Une procédure retirée : elle quitte les fonctions du module.
    (work / f"{FOLDER}/Programmabilite/Purger.sql").unlink()
    _push(work)
    await _collect(collector, space, url)
    functions = load_schema(extend.read_text("utf-8"))["functions"]
    assert "ventes.Purger" not in {f["name"] for f in functions}
    # Plus aucune procédure : le module n'a plus de `.extend.yaml`.
    for name in ("LireCommande", "Total", "Lignes"):
        (work / f"{FOLDER}/Programmabilite/{name}.sql").unlink()
    _push(work)
    await _collect(collector, space, url)
    assert not extend.exists()
    index = yaml.safe_load((raw / FUNCTIONS_INDEX).read_text("utf-8"))
    assert index["files"] == {}
