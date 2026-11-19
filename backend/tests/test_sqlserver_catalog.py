from __future__ import annotations

import json

from ossie_studio.databases.sqlserver import build_catalog
from ossie_studio.parsers.conventions import validate_schema
from ossie_studio.sources.sql_extract import SqlExtractor, object_to_source, source_key


def _col(schema, table, name, data_type, position, *, nullable="YES", max_len=None,
         precision=None, scale=None, default=None, comment="", computed=None, identity=False):
    return {
        "schema": schema, "table": table, "name": name, "position": position,
        "is_nullable": nullable, "default": default, "data_type": data_type,
        "max_len": max_len, "precision": precision, "scale": scale, "comment": comment,
        "computed": computed, "identity": identity,
    }


def _objects(read):
    return {f"{o.schema}.{o.name}": o for o in read.objects}


def test_types_sqlserver() -> None:
    cols = [
        _col("dbo", "T", "nom", "nvarchar", 1, nullable="NO", max_len=50),
        _col("dbo", "T", "libre", "varchar", 2, max_len=-1),  # varchar(max)
        _col("dbo", "T", "n", "int", 3),
        _col("dbo", "T", "gros", "bigint", 4),
        _col("dbo", "T", "prix", "decimal", 5, precision=10, scale=2),
        _col("dbo", "T", "montant", "money", 6),
        _col("dbo", "T", "actif", "bit", 7),
        _col("dbo", "T", "ident", "uniqueidentifier", 8),
        _col("dbo", "T", "moment", "datetime2", 9),
        _col("dbo", "T", "blob", "varbinary", 10),
        _col("dbo", "T", "doc", "xml", 11),
    ]
    frags = {c.name: c.schema for c in build_catalog(
        tables=[{"schema": "dbo", "name": "T", "kind": "table"}], columns=cols
    ).objects[0].columns}
    assert frags["nom"]["maxLength"] == 50
    assert "maxLength" not in frags["libre"]  # varchar(max) : pas de limite
    assert frags["libre"]["x-db-type"] == "varchar(max)"
    assert frags["n"]["format"] == "int32"
    assert frags["gros"]["format"] == "int64"
    assert frags["prix"]["type"] == "number"
    assert frags["montant"]["type"] == "number"
    assert frags["actif"]["type"] == "boolean"
    assert frags["ident"]["format"] == "uuid"
    assert frags["moment"]["format"] == "date-time"
    assert frags["blob"] == {
        "type": "string", "contentEncoding": "base64", "x-db-type": "varbinary",
    }
    assert frags["doc"]["type"] == "string"


def test_colonne_calculee_et_identite() -> None:
    cols = [
        _col("dbo", "T", "id", "int", 1, nullable="NO", identity=True),
        _col("dbo", "T", "total", "int", 2, computed="([a]+[b])"),
    ]
    frags = {c.name: c.schema for c in build_catalog(
        tables=[{"schema": "dbo", "name": "T", "kind": "table"}], columns=cols
    ).objects[0].columns}
    assert frags["id"]["x-identity"] is True
    assert frags["total"]["x-computed"] == "([a]+[b])"


def test_vue_indexee() -> None:
    obj = build_catalog(
        tables=[{"schema": "dbo", "name": "V", "kind": "view", "indexed": True}],
        columns=[_col("dbo", "V", "id", "int", 1)],
    ).objects[0]
    assert obj.kind == "view"
    assert obj.extra["x-indexed"] is True


def test_commentaire_ms_description() -> None:
    obj = build_catalog(
        tables=[{"schema": "dbo", "name": "T", "kind": "table", "comment": "Une table."}],
        columns=[_col("dbo", "T", "id", "int", 1, comment="La clef.")],
    ).objects[0]
    assert obj.description == "Une table."
    assert obj.columns[0].description == "La clef."


def test_clefs_appliquees() -> None:
    tables = [{"schema": "dbo", "name": "cmd", "kind": "table"}]
    cols = [
        _col("dbo", "cmd", "id", "int", 1, nullable="NO"),
        _col("dbo", "cmd", "client_id", "int", 2, nullable="NO"),
    ]
    pk = [{"schema": "dbo", "table": "cmd", "column": "id", "seq": 1, "constraint": "pk"}]
    fk = [{"schema": "dbo", "table": "cmd", "column": "client_id", "ref_schema": "dbo",
           "ref_table": "client", "ref_column": "id", "seq": 1, "fk_name": "fk"}]
    obj = build_catalog(tables=tables, columns=cols, primary_keys=pk, foreign_keys=fk).objects[0]
    assert obj.primary_key == ("id",)
    assert obj.foreign_keys[0].enforced is True
    # SQL Server applique les clefs : pas de x-enforced:false au niveau objet.
    assert "x-enforced" not in obj.extra


def test_pipeline_complet_passe_le_validateur() -> None:
    tables = [{"schema": "dbo", "name": "cmd", "kind": "table", "comment": "Commandes."}]
    cols = [
        _col("dbo", "cmd", "id", "int", 1, nullable="NO", identity=True),
        _col("dbo", "cmd", "ref", "nvarchar", 2, max_len=20),
        _col("dbo", "cmd", "client_id", "int", 3, nullable="NO"),
    ]
    pk = [{"schema": "dbo", "table": "cmd", "column": "id", "seq": 1, "constraint": "pk"}]
    fk = [{"schema": "dbo", "table": "cmd", "column": "client_id", "ref_schema": "dbo",
           "ref_table": "client", "ref_column": "id", "seq": 1, "fk_name": "fk"}]
    read = build_catalog(tables=tables, columns=cols, primary_keys=pk, foreign_keys=fk)
    files = {source_key(o): object_to_source(o) for o in read.objects}
    schema = json.loads(SqlExtractor("sqlserver").parse(files)[0].schema)
    validate_schema(schema)
    assert schema["x-kind"] == "table"
    assert schema["properties"]["id"]["x-identity"] is True
    assert schema["properties"]["client_id"]["x-references"] == {
        "table": "dbo.client", "column": "id",
    }
    assert schema["x-primary-key"] == ["id"]


def test_schemas_inclus_exclus() -> None:
    tables = [
        {"schema": "dbo", "name": "T", "kind": "table"},
        {"schema": "audit", "name": "S", "kind": "table"},
    ]
    cols = [_col(t["schema"], t["name"], "id", "int", 1) for t in tables]
    only = build_catalog(tables=tables, columns=cols, schemas=["dbo"])
    assert {o.schema for o in only.objects} == {"dbo"}
    sans = build_catalog(tables=tables, columns=cols, exclusions=["audit"])
    assert {o.schema for o in sans.objects} == {"dbo"}
