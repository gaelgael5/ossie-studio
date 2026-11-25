from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

import asyncpg
import pytest

from ossie_studio.databases.base import DbEngineError
from ossie_studio.databases.postgres import PostgresEngine
from ossie_studio.parsers.conventions import load_schema, validate_schema
from ossie_studio.sources.sql_extract import SqlExtractor, object_to_source, source_key

ADMIN_DSN = os.environ.get("OSSIE_TEST_ADMIN_DSN", "postgresql://ossie:ossie@127.0.0.1:5432/postgres")

SEED = """
CREATE SCHEMA app;
CREATE SCHEMA prive;
CREATE TYPE app.statut AS ENUM ('actif', 'inactif');
CREATE TABLE app.client (
  id uuid PRIMARY KEY,
  nom varchar(50) NOT NULL,
  age integer,
  solde numeric(10,2),
  etat app.statut NOT NULL DEFAULT 'actif',
  tags text[],
  cree timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE app.client IS 'Un client.';
COMMENT ON COLUMN app.client.nom IS 'Nom complet.';
CREATE TABLE app.commande (
  id bigint PRIMARY KEY,
  client_id uuid NOT NULL REFERENCES app.client(id),
  ref varchar(20) UNIQUE
);
CREATE VIEW app.vue_cmd AS SELECT c.id, c.ref FROM app.commande c;
CREATE TABLE prive.secret (id int PRIMARY KEY);
"""


def _params(dsn: str, dbname: str) -> tuple[dict[str, str], str]:
    parts = urlsplit(dsn)
    params = {
        "host": parts.hostname or "127.0.0.1",
        "port": str(parts.port or 5432),
        "database": dbname,
        "username": parts.username or "",
        "tls": "disable",
    }
    return params, parts.password or ""


@pytest.fixture
async def catalog_db() -> AsyncIterator[str]:
    dbname = f"test_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f'CREATE DATABASE "{dbname}"')
    await admin.close()
    base = ADMIN_DSN.rsplit("/", 1)[0]
    try:
        conn = await asyncpg.connect(f"{base}/{dbname}")
        await conn.execute(SEED)
        await conn.close()
        yield dbname
    finally:
        admin = await asyncpg.connect(ADMIN_DSN)
        await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        await admin.close()


async def _read(dbname: str, *, schemas=None, exclusions=None):
    params, password = _params(ADMIN_DSN, dbname)
    return await PostgresEngine().read_catalog(
        params, password, schemas=schemas, exclusions=exclusions
    )


async def test_lit_tables_vues_et_types(catalog_db: str) -> None:
    read = await _read(catalog_db)
    by_name = {f"{o.schema}.{o.name}": o for o in read.objects}
    client = by_name["app.client"]
    assert client.kind == "table"
    assert client.description == "Un client."
    assert client.primary_key == ("id",)
    cols = {c.name: c for c in client.columns}
    assert cols["id"].schema == {"type": "string", "format": "uuid", "x-db-type": "uuid"}
    assert cols["nom"].schema["maxLength"] == 50 and cols["nom"].required
    assert cols["nom"].description == "Nom complet."
    assert cols["age"].required is False
    assert cols["etat"].schema["enum"] == ["actif", "inactif"]
    assert cols["tags"].schema["type"] == "array"
    assert cols["cree"].schema["format"] == "date-time"
    # Vue + dépendance.
    vue = by_name["app.vue_cmd"]
    assert vue.kind == "view"
    assert "app.commande" in vue.depends_on


async def test_clefs_primaire_unique_etrangere(catalog_db: str) -> None:
    read = await _read(catalog_db)
    commande = next(o for o in read.objects if o.name == "commande")
    assert commande.primary_key == ("id",)
    assert ("ref",) in commande.unique_keys
    fk = commande.foreign_keys[0]
    assert fk.columns == ("client_id",)
    assert (fk.ref_schema, fk.ref_object, fk.ref_columns) == ("app", "client", ("id",))


async def test_schemas_inclus_et_exclus(catalog_db: str) -> None:
    only_app = await _read(catalog_db, schemas=["app"])
    assert {o.schema for o in only_app.objects} == {"app"}
    sans_prive = await _read(catalog_db, exclusions=["prive"])
    assert "prive" not in {o.schema for o in sans_prive.objects}
    assert "app" in {o.schema for o in sans_prive.objects}


async def test_ajout_de_lignes_ne_change_pas_le_checksum(catalog_db: str) -> None:
    before = {source_key(o): object_to_source(o) for o in (await _read(catalog_db)).objects}
    base = ADMIN_DSN.rsplit("/", 1)[0]
    conn = await asyncpg.connect(f"{base}/{catalog_db}")
    await conn.execute("INSERT INTO app.client (id, nom) VALUES (gen_random_uuid(), 'x')")
    await conn.close()
    after = {source_key(o): object_to_source(o) for o in (await _read(catalog_db)).objects}
    assert before == after  # structure seule : les données ne comptent pas


async def test_session_en_lecture_seule(catalog_db: str) -> None:
    params, password = _params(ADMIN_DSN, catalog_db)
    conn = await PostgresEngine()._connect(params, password)
    try:
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute("CREATE TABLE app.interdit (id int)")
    finally:
        await conn.close()


async def test_schemas_passent_le_validateur(catalog_db: str) -> None:
    read = await _read(catalog_db)
    files = {source_key(o): object_to_source(o) for o in read.objects}
    for obj in SqlExtractor("postgres").parse(files):
        validate_schema(load_schema(obj.schema))


async def test_base_injoignable_leve_db_engine_error() -> None:
    engine = PostgresEngine()
    params = {"host": "127.0.0.1", "port": "5432", "database": "nexistepas_xyz",
              "username": "ossie", "tls": "disable"}
    with pytest.raises(DbEngineError):
        await engine.read_catalog(params, "ossie", schemas=None, exclusions=None)
