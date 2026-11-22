from __future__ import annotations

import json

import yaml

from ossie_studio.parsers.base import FileRow, PluginContext, PluginResult
from ossie_studio.parsers.conventions import validate_schema
from ossie_studio.parsers.sqlserver_project import SqlServerProjectParser, split_batches


class FakeRaw:
    def __init__(self, files: dict[str, str] | None = None) -> None:
        self._files = files or {}

    def read_index(self, name: str) -> str | None:
        return self._files.get(name)

    def read_output(self, path: str) -> str | None:
        return self._files.get(path)

    def list_outputs(self) -> dict[str, str]:
        return dict(self._files)

    def find_schema(self, name: str) -> str | None:
        return None


def _run(
    files: dict[str, str | bytes], *, force: bool = False, raw: FakeRaw | None = None
) -> PluginResult:
    contents = {p: (c.encode("utf-8") if isinstance(c, str) else c) for p, c in files.items()}
    ctx = PluginContext(
        files=[FileRow(path=p, crc="x") for p in contents],
        contents=contents,
        raw=raw or FakeRaw(),
        params={},
        force=force,
        branch="main",
    )
    return SqlServerProjectParser().run(ctx)


def _schemas(result: PluginResult) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for w in result.writes:
        if w.path.endswith(".json"):
            schema = json.loads(w.content)
            validate_schema(schema)  # critère 11 : tout passe le validateur
            out[w.path] = schema
    return out


_PROJ = '<Project Sdk="Microsoft.Build.Sql/1.0.0"></Project>'


def test_table_complete() -> None:
    sql = """CREATE TABLE [ventes].[Commande] (
  [Id] BIGINT NOT NULL IDENTITY(1,1),
  [Ref] NVARCHAR(20) NOT NULL,
  [Prix] DECIMAL(18,2) NULL,
  [Total] AS ([Prix] * 2),
  [Cree] DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  CONSTRAINT [PK] PRIMARY KEY ([Id]),
  CONSTRAINT [UQ] UNIQUE ([Ref])
);"""
    schemas = _run({"db/db.sqlproj": _PROJ, "db/Commande.sql": sql})
    s = _schemas(schemas)["ventes-commande.json"]
    assert s["x-kind"] == "table" and s["x-engine"] == "sqlserver"
    props = s["properties"]
    assert props["Id"]["x-identity"] is True and props["Id"]["format"] == "int64"
    assert props["Ref"]["maxLength"] == 20
    assert props["Prix"]["type"] == "number" and props["Prix"]["x-db-type"] == "DECIMAL(18,2)"
    assert props["Total"]["x-computed"] == "([Prix] * 2)"
    assert props["Cree"]["x-default"] == "SYSUTCDATETIME()"
    assert set(s["required"]) == {"Id", "Ref", "Cree"}
    assert s["x-primary-key"] == ["Id"] and s["x-unique-keys"] == [["Ref"]]


def test_contrainte_et_description_fichiers_separes() -> None:
    files = {
        "db/db.sqlproj": _PROJ,
        "db/Commande.sql":
            "CREATE TABLE ventes.Commande ([Id] INT NOT NULL, [ClientId] INT NOT NULL);",
        "db/fk.sql": "ALTER TABLE ventes.Commande ADD CONSTRAINT fk FOREIGN KEY ([ClientId]) "
                     "REFERENCES ventes.Client([Id]);",
        "db/desc.sql": "EXEC sp_addextendedproperty @name=N'MS_Description', "
                       "@value=N'Les commandes', @level0type=N'SCHEMA', @level0name=N'ventes', "
                       "@level1type=N'TABLE', @level1name=N'Commande';",
    }
    s = _schemas(_run(files))["ventes-commande.json"]
    assert s["description"] == "Les commandes"
    assert s["properties"]["ClientId"]["x-references"] == {"table": "ventes.Client", "column": "Id"}


def test_vue_derivee_et_calculee() -> None:
    files = {
        "db/db.sqlproj": _PROJ,
        "db/V.sql": "CREATE VIEW ventes.Vue AS SELECT c.[Id], c.[Ref] AS R, c.[Prix]*2 AS D "
                    "FROM ventes.Commande c;",
    }
    s = _schemas(_run(files))["ventes-vue.json"]
    assert s["x-kind"] == "view"
    assert s["properties"]["Id"]["x-derived-from"] == "ventes.Commande.Id"
    assert s["properties"]["R"]["x-derived-from"] == "ventes.Commande.Ref"
    assert "x-expression" in s["properties"]["D"]
    assert "type" not in s["properties"]["Id"]  # on ne recopie pas le type


def test_db_type_tel_quecrit() -> None:
    files = {
        "db/db.sqlproj": _PROJ,
        "db/T.sql": "CREATE TABLE dbo.T ([a] DECIMAL(18,2) NULL, [b] REAL NULL, [c] NTEXT NULL);",
    }
    props = _schemas(_run(files))["dbo-t.json"]["properties"]
    assert props["a"]["x-db-type"] == "DECIMAL(18,2)"
    assert props["b"]["x-db-type"] == "REAL"
    assert props["c"]["x-db-type"] == "NTEXT"


def test_go_crochets_sqlcmd_commentaires() -> None:
    sql = """-- commentaire
CREATE TABLE [dbo].[A] ([x] INT NOT NULL);
GO
/* bloc */
CREATE TABLE [dbo].[B] ([y] NVARCHAR($(Taille)) NULL);
GO"""
    schemas = _schemas(_run({"db/db.sqlproj": _PROJ, "db/f.sql": sql}))
    assert "dbo-a.json" in schemas and "dbo-b.json" in schemas


def test_colonne_json_convention() -> None:
    files = {
        "db/db.sqlproj": _PROJ,
        "db/Commande.sql": "CREATE TABLE ventes.Commande ([Details] NVARCHAR(MAX) NULL);",
        "db/ventes.Commande.Details.schema.json": '{"type":"object"}',
    }
    schemas = _schemas(_run(files))
    col = schemas["ventes-commande.json"]["properties"]["Details"]
    assert col["type"] == "string"
    assert col["contentMediaType"] == "application/json"
    assert col["contentSchema"] == {"$ref": "ventes-commande-details.json"}
    content = schemas["ventes-commande-details.json"]
    assert content["x-kind"] == "json-content"
    assert content["x-json-column"] == "ventes.Commande.Details"


def test_colonne_json_dbo_et_insensible_casse() -> None:
    files = {
        "db/db.sqlproj": _PROJ,
        "db/Commande.sql": "CREATE TABLE Commande ([Details] NVARCHAR(MAX) NULL);",
        "db/commande.details.schema.json": '{"type":"object"}',  # casse différente, dbo
    }
    schemas = _schemas(_run(files))
    assert "contentSchema" in schemas["dbo-commande.json"]["properties"]["Details"]
    assert "dbo-commande-details.json" in schemas


def test_json_schema_draft07_converti() -> None:
    draft = json.dumps({
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "definitions": {"l": {"type": "object"}},
        "properties": {
            "items": {"type": "array", "items": {"$ref": "#/definitions/l"}},
            "ext": {"$ref": "autre.json#/X"},
        },
    })
    files = {
        "db/db.sqlproj": _PROJ,
        "db/T.sql": "CREATE TABLE dbo.T ([d] NVARCHAR(MAX) NULL);",
        "db/T.d.schema.json": draft,
    }
    content = _schemas(_run(files))["dbo-t-d.json"]
    assert "$defs" in content and "definitions" not in content
    assert content["properties"]["items"]["items"]["$ref"] == "#/$defs/l"
    assert content["properties"]["ext"]["x-unresolved-type"] == "autre.json#/X"


def test_signalements() -> None:
    files = {
        "db/db.sqlproj": _PROJ,
        "db/T.sql": "CREATE TABLE dbo.T ([n] INT NOT NULL, [d] NVARCHAR(MAX) NULL);",
        "db/dbo.T.d.schema.json": '{"type":"object"}',        # ok
        "db/dbo.T.n.schema.json": '{"type":"object"}',        # colonne non chaîne
        "db/dbo.Absente.c.schema.json": '{"type":"object"}',  # orphelin
    }
    result = _run(files)
    causes = {(e.source, e.cause) for e in result.errors}
    assert ("db/dbo.T.n.schema.json", "json_schema_not_string") in causes
    assert ("db/dbo.Absente.c.schema.json", "json_schema_orphan") in causes


def test_doublon_colonne_json() -> None:
    files = {
        "db/db.sqlproj": _PROJ,
        "db/T.sql": "CREATE TABLE dbo.T ([d] NVARCHAR(MAX) NULL);",
        "db/dbo.T.d.schema.json": '{"type":"object"}',
        "db/sub/T.d.schema.json": '{"type":"object"}',  # même colonne, deux fichiers
    }
    result = _run(files)
    causes = {e.cause for e in result.errors}
    assert "json_schema_duplicate" in causes
    # Aucun des deux n'est retenu : la colonne garde son type simple.
    schemas = _schemas(result)
    assert "contentSchema" not in schemas["dbo-t.json"]["properties"]["d"]


def test_json_invalide_signale() -> None:
    files = {
        "db/db.sqlproj": _PROJ,
        "db/T.sql": "CREATE TABLE dbo.T ([d] NVARCHAR(MAX) NULL);",
        "db/dbo.T.d.schema.json": "{pas du json",
    }
    result = _run(files)
    assert any(e.cause == "json_schema_invalid" for e in result.errors)


def test_sqlproj_exclusions_et_ancien_format() -> None:
    sdk = {
        "db/db.sqlproj": '<Project Sdk="Microsoft.Build.Sql"><ItemGroup>'
                         '<Build Remove="Retire.sql" /><None Include="Donnees.sql" />'
                         "</ItemGroup></Project>",
        "db/Gardee.sql": "CREATE TABLE dbo.Gardee ([x] INT NOT NULL);",
        "db/Retire.sql": "CREATE TABLE dbo.Retiree ([x] INT NOT NULL);",
        "db/Donnees.sql": "CREATE TABLE dbo.Donnees ([x] INT NOT NULL);",
    }
    schemas = _schemas(_run(sdk))
    assert "dbo-gardee.json" in schemas
    assert "dbo-retiree.json" not in schemas and "dbo-donnees.json" not in schemas

    old = {
        "db/db.sqlproj":
            '<Project><ItemGroup><Build Include="Incluse.sql" /></ItemGroup></Project>',
        "db/Incluse.sql": "CREATE TABLE dbo.Incluse ([x] INT NOT NULL);",
        "db/Ignoree.sql": "CREATE TABLE dbo.Ignoree ([x] INT NOT NULL);",
    }
    schemas = _schemas(_run(old))
    assert "dbo-incluse.json" in schemas and "dbo-ignoree.json" not in schemas


def test_sans_sqlproj_signale() -> None:
    result = _run({"db/T.sql": "CREATE TABLE dbo.T ([x] INT NOT NULL);"})
    assert any(e.cause == "no_sqlproj" for e in result.errors)
    assert "dbo-t.json" in {w.path for w in result.writes}


def test_sans_projet_ne_revendique_rien() -> None:
    # Dépôt avec un *.schema.json mais aucun .sql/.sqlproj (cas du parseur de démo).
    result = _run({"a.schema.json": '{"type":"object"}'})
    assert result.writes == [] and result.statuses == {}


def test_incrementalite() -> None:
    files = {"db/db.sqlproj": _PROJ, "db/T.sql": "CREATE TABLE dbo.T ([x] INT NOT NULL);"}
    first = _run(files)
    raw = FakeRaw({w.path: w.content for w in first.writes})
    # 1. Rien n'a changé → aucune écriture.
    second = _run(files, raw=raw)
    assert second.writes == [] and second.unchanged > 0
    # 2. Une colonne ajoutée → la table est retransformée.
    changed = {**files, "db/T.sql": "CREATE TABLE dbo.T ([x] INT NOT NULL, [y] INT NULL);"}
    third = _run(changed, raw=raw)
    assert any(w.path == "dbo-t.json" for w in third.writes)
    # 3. Une table supprimée (fichier retiré) → son schéma disparaît.
    gone = {"db/db.sqlproj": _PROJ, "db/U.sql": "CREATE TABLE dbo.U ([x] INT NOT NULL);"}
    fourth = _run(gone, raw=raw)
    assert "dbo-t.json" in fourth.deletes


def test_sortie_deterministe() -> None:
    files = {"db/db.sqlproj": _PROJ,
             "db/T.sql": "CREATE TABLE dbo.T ([x] INT NOT NULL, [y] NVARCHAR(10) NULL);"}
    a = {w.path: w.content for w in _run(files).writes}
    b = {w.path: w.content for w in _run(files).writes}
    assert a == b


def test_ordre_non_reconnu_signale() -> None:
    files = {"db/db.sqlproj": _PROJ,
             "db/bad.sql": "CREATE TABLE dbo.Ok ([x] INT NOT NULL);\nGO\nTHIS IS NOT SQL;"}
    result = _run(files)
    assert any(e.source == "db/bad.sql" for e in result.errors)


def test_split_batches() -> None:
    batches = split_batches("a\nGO\nb\ngo\nc")
    assert [b for b, _ in batches] == ["a", "b", "c"]
    assert [line for _, line in batches] == [1, 3, 5]


def test_version_inclut_sqlglot() -> None:
    assert SqlServerProjectParser().version.startswith("1.1-sqlglot")


def test_index_ecrit_en_anglais_et_version_1_relue() -> None:
    files = {"db/db.sqlproj": _PROJ, "db/T.sql": "CREATE TABLE dbo.T ([x] INT NOT NULL);"}
    first = _run(files)
    index_name, index_text = next(
        (w.path, w.content) for w in first.writes if w.path.endswith(".yaml")
    )
    index = yaml.safe_load(index_text)
    assert index["version"] == 2
    assert set(index) == {"version", "parser", "files", "outputs"}
    assert index["outputs"] == ["dbo-t.json"]
    # `x-source` de chaque schéma : clés anglaises.
    schema = _schemas(first)["dbo-t.json"]
    assert all("file" in s and "fichier" not in s for s in schema["x-source"])
    # Un index version 1 (clés françaises) reste lisible : ses anciennes sorties
    # sont retirées quand elles ne sont plus produites.
    legacy = yaml.safe_dump(
        {"version": 1, "parseur": "0.9", "fichiers": {}, "sorties": ["dbo-ancienne.json"]}
    )
    raw = FakeRaw({index_name: legacy, "dbo-ancienne.json": "{}"})
    second = _run(files, raw=raw)
    assert "dbo-ancienne.json" in second.deletes
