from __future__ import annotations

import json

from ossie_studio.databases.snowflake import build_catalog
from ossie_studio.parsers.conventions import validate_schema
from ossie_studio.sources.sql_extract import SqlExtractor, object_to_source, source_key


def _col(schema, table, name, data_type, position, *, nullable="YES", char_len=None,
         precision=None, scale=None, default=None, comment=""):
    return {
        "schema": schema, "table": table, "name": name, "position": position,
        "is_nullable": nullable, "default": default, "data_type": data_type,
        "char_len": char_len, "precision": precision, "scale": scale, "comment": comment,
    }


def _objects(read):
    return {f"{o.schema}.{o.name}": o for o in read.objects}


def test_types_snowflake() -> None:
    cols = [
        _col("APP", "T", "NOM", "TEXT", 1, nullable="NO", char_len=50),
        _col("APP", "T", "LIBRE", "TEXT", 2, char_len=16777216),  # VARCHAR sans limite
        _col("APP", "T", "QTE", "NUMBER", 3, precision=38, scale=0),
        _col("APP", "T", "PRIX", "NUMBER", 4, precision=10, scale=2),
        _col("APP", "T", "RATIO", "FLOAT", 5),
        _col("APP", "T", "ACTIF", "BOOLEAN", 6),
        _col("APP", "T", "JOUR", "DATE", 7),
        _col("APP", "T", "MOMENT", "TIMESTAMP_NTZ", 8),
        _col("APP", "T", "DOC", "VARIANT", 9),
        _col("APP", "T", "LISTE", "ARRAY", 10),
    ]
    read = build_catalog(tables=[{"schema": "APP", "name": "T", "table_type": "BASE TABLE"}],
                         columns=cols)
    frags = {c.name: c.schema for c in read.objects[0].columns}
    assert frags["NOM"]["maxLength"] == 50
    assert "maxLength" not in frags["LIBRE"]  # longueur « sans limite »
    assert frags["QTE"]["type"] == "integer"
    assert frags["PRIX"]["type"] == "number"
    assert frags["RATIO"]["type"] == "number"
    assert frags["ACTIF"]["type"] == "boolean"
    assert frags["JOUR"]["format"] == "date"
    assert frags["MOMENT"]["format"] == "date-time"
    assert frags["MOMENT"]["x-db-type"] == "TIMESTAMP_NTZ"
    # VARIANT → object sans structure, semi-structuré (critère 2).
    assert frags["DOC"] == {"type": "object", "x-semi-structured": True, "x-db-type": "VARIANT"}
    assert frags["LISTE"]["type"] == "array" and "items" not in frags["LISTE"]


def test_genre_objets_et_transient() -> None:
    tables = [
        {"schema": "APP", "name": "T", "table_type": "BASE TABLE", "is_transient": "YES"},
        {"schema": "APP", "name": "V", "table_type": "VIEW", "comment": "une vue"},
        {"schema": "APP", "name": "M", "table_type": "MATERIALIZED VIEW"},
        {"schema": "APP", "name": "D", "table_type": "DYNAMIC TABLE"},
    ]
    cols = [_col("APP", t["name"], "ID", "NUMBER", 1, precision=38, scale=0) for t in tables]
    objs = _objects(build_catalog(tables=tables, columns=cols))
    assert objs["APP.T"].kind == "table" and objs["APP.T"].extra["x-table-type"] == "transient"
    assert objs["APP.V"].kind == "view" and objs["APP.V"].description == "une vue"
    assert objs["APP.M"].kind == "materialized-view"
    assert objs["APP.D"].kind == "dynamic-table"


def test_clefs_non_appliquees() -> None:
    tables = [{"schema": "APP", "name": "CMD", "table_type": "BASE TABLE"}]
    cols = [
        _col("APP", "CMD", "ID", "NUMBER", 1, nullable="NO", precision=38, scale=0),
        _col("APP", "CMD", "REF", "TEXT", 2, char_len=20),
        _col("APP", "CMD", "CLIENT_ID", "NUMBER", 3, precision=38, scale=0),
    ]
    pk = [{"schema": "APP", "table": "CMD", "column": "ID", "seq": 1, "constraint": "pk"}]
    uq = [{"schema": "APP", "table": "CMD", "column": "REF", "seq": 1, "constraint": "u"}]
    fk = [{"schema": "APP", "table": "CMD", "column": "CLIENT_ID", "ref_schema": "APP",
           "ref_table": "CLIENT", "ref_column": "ID", "seq": 1, "fk_name": "fk"}]
    obj = build_catalog(tables=tables, columns=cols, primary_keys=pk,
                        unique_keys=uq, foreign_keys=fk).objects[0]
    assert obj.primary_key == ("ID",)
    assert obj.unique_keys == (("REF",),)
    assert obj.foreign_keys[0].enforced is False
    assert obj.extra["x-enforced"] is False


def test_table_sans_clef_ninvente_rien() -> None:
    obj = build_catalog(
        tables=[{"schema": "APP", "name": "T", "table_type": "BASE TABLE"}],
        columns=[_col("APP", "T", "ID", "NUMBER", 1, precision=38, scale=0)],
    ).objects[0]
    assert obj.primary_key == () and obj.unique_keys == () and obj.foreign_keys == ()
    assert "x-enforced" not in obj.extra


def test_dependances_de_vue() -> None:
    tables = [{"schema": "APP", "name": "V", "table_type": "VIEW"}]
    cols = [_col("APP", "V", "ID", "NUMBER", 1, precision=38, scale=0)]
    deps = [{"schema": "APP", "name": "V", "ref_schema": "APP", "ref_name": "CMD"}]
    obj = build_catalog(tables=tables, columns=cols, dependencies=deps).objects[0]
    assert obj.depends_on == ("APP.CMD",)


def test_schemas_inclus_exclus() -> None:
    tables = [
        {"schema": "APP", "name": "T", "table_type": "BASE TABLE"},
        {"schema": "PRIVE", "name": "S", "table_type": "BASE TABLE"},
    ]
    cols = [_col(t["schema"], t["name"], "ID", "NUMBER", 1, precision=38, scale=0) for t in tables]
    only = build_catalog(tables=tables, columns=cols, schemas=["APP"])
    assert {o.schema for o in only.objects} == {"APP"}
    sans = build_catalog(tables=tables, columns=cols, exclusions=["PRIVE"])
    assert {o.schema for o in sans.objects} == {"APP"}


def test_pipeline_complet_passe_le_validateur() -> None:
    tables = [{"schema": "APP", "name": "CMD", "table_type": "BASE TABLE", "comment": "Commandes."}]
    cols = [
        _col("APP", "CMD", "ID", "NUMBER", 1, nullable="NO", precision=38, scale=0),
        _col("APP", "CMD", "DOC", "VARIANT", 2),
        _col("APP", "CMD", "CLIENT_ID", "NUMBER", 3, precision=38, scale=0),
    ]
    fk = [{"schema": "APP", "table": "CMD", "column": "CLIENT_ID", "ref_schema": "APP",
           "ref_table": "CLIENT", "ref_column": "ID", "seq": 1, "fk_name": "fk"}]
    pk = [{"schema": "APP", "table": "CMD", "column": "ID", "seq": 1, "constraint": "pk"}]
    read = build_catalog(tables=tables, columns=cols, primary_keys=pk, foreign_keys=fk)
    files = {source_key(o): object_to_source(o) for o in read.objects}
    objects = SqlExtractor("snowflake").parse(files)
    schema = json.loads(objects[0].schema)
    validate_schema(schema)
    assert schema["x-kind"] == "table"
    assert schema["description"] == "Commandes."
    assert schema["x-enforced"] is False
    assert schema["properties"]["DOC"]["x-semi-structured"] is True
    assert schema["properties"]["CLIENT_ID"]["x-references"] == {
        "table": "APP.CLIENT", "column": "ID", "enforced": False,
    }
    assert schema["x-primary-key"] == ["ID"]
