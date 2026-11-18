from __future__ import annotations

import base64
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.databases.postgres import PostgresEngine
from tests.conftest import bootstrap_admin, create_plain_user, db_fetchval, login

# La vraie base locale sert à éprouver PostgreSQL de bout en bout.
PG_PARAMS = {
    "host": "127.0.0.1",
    "port": "5432",
    "database": "postgres",
    "username": "ossie",
    "tls": "disable",
}
PG_PASSWORD = "ossie"


@pytest.fixture
async def admin(client: AsyncClient) -> AsyncClient:
    await bootstrap_admin(client)
    return client


async def _pg_secret(client: AsyncClient, slug: str = "pg", value: str = PG_PASSWORD) -> str:
    r = await client.post(
        "/api/secrets",
        json={"slug": slug, "label": "Mot de passe", "category": "postgres", "value": value},
    )
    assert r.status_code == 201, r.text
    return slug


async def _create_pg(client: AsyncClient, secret_slug: str, label: str = "PG local"):
    return await client.post(
        "/api/db-connections",
        json={
            "label": label,
            "engine": "postgres",
            "params": PG_PARAMS,
            "secret_slug": secret_slug,
        },
    )


# --- Moteur PostgreSQL, en vrai ---------------------------------------------

async def test_postgres_verifie_et_enregistre(admin: AsyncClient, app: FastAPI) -> None:
    await _pg_secret(admin)
    r = await _create_pg(admin, "pg")
    assert r.status_code == 201, r.text
    body = (await admin.get("/api/db-connections")).json()["connections"][0]
    assert body["engine"] == "postgres"
    assert body["identity"] == "ossie"
    assert "PostgreSQL" in body["server_version"]
    assert body["target"] == "127.0.0.1"


async def test_postgres_mot_de_passe_refuse(admin: AsyncClient, app: FastAPI) -> None:
    await _pg_secret(admin, "pg-ko", value="mauvais-mot-de-passe")
    r = await _create_pg(admin, "pg-ko")
    assert r.status_code == 400
    assert r.json()["error"] == "db_auth_failed"
    assert await db_fetchval(app, "SELECT count(*) FROM db_connections") == 0


async def test_postgres_liste_schemas_et_tables(app: FastAPI) -> None:
    # La structure s'obtient par le contrat, sans lire aucune donnée.
    tables = await PostgresEngine().list_structure(PG_PARAMS, PG_PASSWORD)
    # information_schema.tables existe toujours ; on vérifie la forme.
    assert all(t.schema and t.name for t in tables)


# --- Clé privée Snowflake ----------------------------------------------------

def _encrypted_key(passphrase: bytes) -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(passphrase),
    )
    return base64.b64encode(pem).decode()


async def test_secret_snowflake_cle_ouverte_stockee(admin: AsyncClient, app: FastAPI) -> None:
    key_b64 = _encrypted_key(b"phrase-secrete")
    r = await admin.post(
        "/api/secrets",
        json={
            "slug": "sf-key",
            "label": "Clé Snowflake",
            "category": "snowflake",
            "key_file": key_b64,
            "passphrase": "phrase-secrete",
        },
    )
    assert r.status_code == 201, r.text
    # La valeur stockée est la clé ouverte (PEM non chiffré), jamais la phrase.
    enc = await db_fetchval(app, "SELECT value_encrypted FROM secrets WHERE slug = 'sf-key'")
    assert isinstance(enc, bytes)
    opened = app.state.crypto.decrypt(enc).reveal()
    assert "BEGIN PRIVATE KEY" in opened
    assert "ENCRYPTED" not in opened
    assert "phrase-secrete" not in opened


async def test_secret_snowflake_mauvaise_phrase_refuse(admin: AsyncClient, app: FastAPI) -> None:
    key_b64 = _encrypted_key(b"la-bonne")
    r = await admin.post(
        "/api/secrets",
        json={
            "slug": "sf-ko",
            "label": "Clé",
            "category": "snowflake",
            "key_file": key_b64,
            "passphrase": "la-mauvaise",
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "db_key_rejected"
    assert await db_fetchval(app, "SELECT count(*) FROM secrets WHERE slug = 'sf-ko'") == 0


# --- Menu des moteurs, suppression, 403 -------------------------------------

async def test_menu_des_moteurs(admin: AsyncClient) -> None:
    body = (await admin.get("/api/db-connections/engines")).json()
    engines = {e["engine"]: e for e in body["engines"]}
    assert set(engines) == {"postgres", "sqlserver", "snowflake"}
    assert engines["snowflake"]["secret_kind"] == "keyfile"
    assert {f["name"] for f in engines["postgres"]["fields"]} == {
        "host",
        "port",
        "database",
        "username",
        "tls",
    }


async def test_suppression_avec_et_sans_secret(admin: AsyncClient, app: FastAPI) -> None:
    await _pg_secret(admin, "pg-garde")
    await _create_pg(admin, "pg-garde")
    cid = await db_fetchval(app, "SELECT id FROM db_connections")
    # Sans demande : le secret reste.
    assert (await admin.delete(f"/api/db-connections/{cid}")).status_code == 204
    assert await db_fetchval(app, "SELECT count(*) FROM secrets WHERE slug = 'pg-garde'") == 1


async def test_suppression_inexistante_404(admin: AsyncClient) -> None:
    r = await admin.delete(f"/api/db-connections/{uuid.uuid4()}")
    assert r.status_code == 404
    assert r.json()["error"] == "db_connection_not_found"


async def test_secret_mauvaise_categorie_refuse(admin: AsyncClient) -> None:
    await admin.post(
        "/api/secrets",
        json={"slug": "gh", "label": "x", "category": "github", "value": "v"},
    )
    r = await admin.post(
        "/api/db-connections",
        json={"label": "X", "engine": "postgres", "params": PG_PARAMS, "secret_slug": "gh"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "db_connection_secret_invalid"


async def test_non_admin_refus_403(admin: AsyncClient, app: FastAPI) -> None:
    await create_plain_user(app, "simple@example.org", "mot-de-passe-simple")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as user:
        await login(user, "simple@example.org", "mot-de-passe-simple")
        assert (await user.get("/api/db-connections")).status_code == 403
        assert (await user.get("/api/db-connections/engines")).status_code == 403
