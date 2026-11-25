from __future__ import annotations

from ossie_studio.databases.snowflake import build_catalog
from ossie_studio.parsers.conventions import load_schema, validate_schema
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
    schema = load_schema(objects[0].schema)
    validate_schema(schema)
    assert schema["x-kind"] == "table"
    assert schema["description"] == "Commandes."
    assert schema["x-enforced"] is False
    assert schema["properties"]["DOC"]["x-semi-structured"] is True
    assert schema["properties"]["CLIENT_ID"]["x-references"] == {
        "table": "APP.CLIENT", "column": "ID", "enforced": False,
    }
    assert schema["x-primary-key"] == ["ID"]


# --- Tags Snowflake (6a8c1828) ------------------------------------------------

def _tag(domain, tag, value, *, schema="", name="", column=None, tag_db="GOV", tag_schema="TAGS"):
    return {
        "tag_database": tag_db, "tag_schema": tag_schema, "tag_name": tag, "tag_value": value,
        "domain": domain, "object_schema": schema, "object_name": name, "column_name": column,
    }


def _tagged_catalog(tags, **kw):
    tables = [
        {"schema": "APP", "name": "CLIENT", "table_type": "BASE TABLE"},
        {"schema": "APP", "name": "V_CLIENT", "table_type": "VIEW"},
        {"schema": "PRIVE", "name": "SECRET", "table_type": "BASE TABLE"},
    ]
    cols = [
        _col("APP", "CLIENT", "EMAIL", "TEXT", 1),
        _col("APP", "CLIENT", "NOM", "TEXT", 2),
        _col("APP", "V_CLIENT", "EMAIL", "TEXT", 1),
        _col("PRIVE", "SECRET", "X", "TEXT", 1),
    ]
    return _objects(build_catalog(tables=tables, columns=cols, tags=tags, **kw))


def test_tags_objet_et_colonne() -> None:
    objs = _tagged_catalog([
        _tag("TABLE", "OWNER", "crm", schema="APP", name="CLIENT"),
        _tag("COLUMN", "PRIVACY_CATEGORY", "IDENTIFIER", schema="APP", name="CLIENT",
             column="EMAIL", tag_db="SNOWFLAKE", tag_schema="CORE"),
    ])
    client = objs["APP.CLIENT"]
    assert client.extra["x-db-tags"] == {"GOV.TAGS.OWNER": "crm"}
    cols = {c.name: c.schema for c in client.columns}
    assert cols["EMAIL"]["x-db-tags"] == {"SNOWFLAKE.CORE.PRIVACY_CATEGORY": "IDENTIFIER"}
    # Sans tag, aucune clé : les schémas existants ne bougent pas.
    assert "x-db-tags" not in cols["NOM"]
    assert "x-db-tags" not in objs["APP.V_CLIENT"].extra


def test_tags_herites_de_la_base_et_du_schema_le_plus_proche_gagne() -> None:
    objs = _tagged_catalog([
        _tag("DATABASE", "ENV", "prod"),
        _tag("SCHEMA", "DOMAINE", "client", name="APP"),
        _tag("SCHEMA", "ENV", "recette", name="PRIVE"),
        _tag("TABLE", "DOMAINE", "vue", schema="APP", name="V_CLIENT"),
    ])
    assert objs["APP.CLIENT"].extra["x-db-tags"] == {
        "GOV.TAGS.DOMAINE": "client", "GOV.TAGS.ENV": "prod",
    }
    assert objs["APP.V_CLIENT"].extra["x-db-tags"]["GOV.TAGS.DOMAINE"] == "vue"
    assert objs["PRIVE.SECRET"].extra["x-db-tags"] == {"GOV.TAGS.ENV": "recette"}


def test_tags_schema_exclu_ignore_et_schema_raw_valide() -> None:
    objs = _tagged_catalog(
        [_tag("TABLE", "OWNER", "x", schema="PRIVE", name="SECRET"),
         _tag("COLUMN", "PII", "oui", schema="APP", name="CLIENT", column="EMAIL")],
        exclusions=["PRIVE"],
    )
    assert "PRIVE.SECRET" not in objs
    files = {source_key(o): object_to_source(o) for o in objs.values()}
    parsed = {o.key: load_schema(o.schema) for o in SqlExtractor("snowflake").parse(files)}
    for schema in parsed.values():
        validate_schema(schema)
    client = next(s for k, s in parsed.items() if s["title"] == "CLIENT")
    assert client["properties"]["EMAIL"]["x-db-tags"] == {"GOV.TAGS.PII": "oui"}


def test_tags_droits_insuffisants_signales_sans_bloquer() -> None:
    read = build_catalog(
        tables=[{"schema": "APP", "name": "T", "table_type": "BASE TABLE"}],
        columns=[_col("APP", "T", "A", "TEXT", 1)],
        errors=[("tags", "insufficient_privilege")],
    )
    assert len(read.objects) == 1
    assert ("tags", "insufficient_privilege") in read.errors
