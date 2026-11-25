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


async def test_secret_snowflake_mot_de_passe_sans_cle(admin: AsyncClient, app: FastAPI) -> None:
    # dc22b381 : la clé privée est facultative ; sans fichier, la valeur saisie
    # (mot de passe ou jeton d'accès programmatique) est stockée telle quelle.
    r = await admin.post(
        "/api/secrets",
        json={"slug": "sf-pw", "label": "SF mdp", "category": "snowflake", "value": "s3cret"},
    )
    assert r.status_code == 201, r.text
    enc = await db_fetchval(app, "SELECT value_encrypted FROM secrets WHERE slug = 'sf-pw'")
    assert app.state.crypto.decrypt(enc).reveal() == "s3cret"


async def test_secret_snowflake_ni_cle_ni_valeur_refuse(admin: AsyncClient) -> None:
    r = await admin.post(
        "/api/secrets", json={"slug": "sf-vide", "label": "SF", "category": "snowflake"}
    )
    assert r.status_code == 400
    assert "key_file_required" in r.json()["error_args"]["fields"]


def test_snowflake_methode_d_authentification_selon_le_secret() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from ossie_studio.databases.snowflake import auth_kwargs

    assert auth_kwargs("s3cret") == {"password": "s3cret"}
    pem = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )
    kw = auth_kwargs(pem)
    assert set(kw) == {"private_key"}
    assert isinstance(kw["private_key"], bytes)


def test_snowflake_mot_de_passe_refuse_n_est_pas_une_cle_refusee() -> None:
    from ossie_studio.databases.snowflake import _map_error

    exc = Exception("250001: Incorrect username or password was specified.")
    assert _map_error(None, exc, keypair=False).code == "db_auth_failed"
    assert _map_error(None, exc, keypair=True).code == "db_key_rejected"
    # Login qui n'aboutit pas (MFA en attente) : pas « hôte injoignable ».
    slow = Exception("000014 (HYT00): Failed to login: login timed out after 10s")
    assert _map_error(None, slow).code == "db_login_timeout"


def test_snowflake_base_inconnue_traduite() -> None:
    # 89752930 : Snowflake ouvre la session puis la première requête échoue ;
    # c'était une erreur 500 (« unknown » à l'écran).
    from ossie_studio.databases.snowflake import _query_error

    no_db = Exception(
        "090105 (22000): Cannot perform SELECT. This session does not have a current database."
    )
    assert _query_error(no_db).code == "db_not_found"
    denied = Exception("002003 (02000): Database 'X' does not exist or not authorized.")
    assert _query_error(denied).code == "db_not_found"
    assert _query_error(Exception("autre chose")).code == "db_query_failed"


async def test_secret_verifie_a_la_creation(admin: AsyncClient, app: FastAPI) -> None:
    # 0d575422 : la popup éprouve la connexion avant d'enregistrer le secret.
    r = await admin.post(
        "/api/secrets",
        json={
            "slug": "pg-ok", "label": "PG", "category": "postgres", "value": PG_PASSWORD,
            "verify": {"engine": "postgres", "params": PG_PARAMS},
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["identity"] == "ossie"
    assert r.json()["server_version"]


async def test_secret_faux_non_enregistre_si_le_test_echoue(
    admin: AsyncClient, app: FastAPI
) -> None:
    r = await admin.post(
        "/api/secrets",
        json={
            "slug": "pg-ko", "label": "PG", "category": "postgres", "value": "mauvais",
            "verify": {"engine": "postgres", "params": PG_PARAMS},
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "db_auth_failed"
    assert await db_fetchval(app, "SELECT count(*) FROM secrets WHERE slug = 'pg-ko'") == 0


async def test_secret_verify_moteur_incoherent_refuse(admin: AsyncClient) -> None:
    r = await admin.post(
        "/api/secrets",
        json={
            "slug": "pg-x", "label": "PG", "category": "postgres", "value": "x",
            "verify": {"engine": "snowflake", "params": {}},
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "db_connection_secret_invalid"


async def test_schemas_d_une_connexion(admin: AsyncClient, app: FastAPI) -> None:
    # eebe6994 : liste à cocher des schémas d'une source, lue avec le secret.
    await _pg_secret(admin)
    cid = (await _create_pg(admin, "pg")).json()["id"]
    # Le schéma est créé dans la base jetable du test (son propriétaire peut y
    # créer), pas dans « postgres », où ce droit dépend de la machine ; la lecture
    # vise cette base par le paramètre `database`.
    async with app.state.pool.acquire() as pg:
        database = await pg.fetchval("SELECT current_database()")
        await pg.execute("CREATE SCHEMA IF NOT EXISTS sch_test")
        await pg.execute("CREATE TABLE IF NOT EXISTS sch_test.t1 (id int)")
    try:
        r = await admin.get(f"/api/db-connections/{cid}/schemas", params={"database": database})
        assert r.status_code == 200, r.text
        names = [s["name"] for s in r.json()["schemas"]]
        assert names == sorted(names)
        assert {"name": "sch_test", "tables": 1} in r.json()["schemas"]
        assert "information_schema" not in names and "pg_catalog" not in names
    finally:
        async with app.state.pool.acquire() as pg:
            await pg.execute("DROP SCHEMA sch_test CASCADE")


async def test_schemas_connexion_inconnue(admin: AsyncClient) -> None:
    r = await admin.get(f"/api/db-connections/{uuid.uuid4()}/schemas")
    assert r.status_code == 404


async def test_schemas_base_injoignable(admin: AsyncClient) -> None:
    await _pg_secret(admin)
    cid = (await _create_pg(admin, "pg")).json()["id"]
    r = await admin.get(f"/api/db-connections/{cid}/schemas", params={"database": "nexiste_pas"})
    assert r.status_code == 400
    assert r.json()["error"].startswith("db_")


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


# --- Slug de la connexion : saisi (déduit du libellé côté interface) -------------


async def test_slug_fourni_est_enregistre(admin: AsyncClient, app: FastAPI) -> None:
    await _pg_secret(admin, "pg-slug")
    r = await admin.post(
        "/api/db-connections",
        json={
            "label": "PG local",
            "slug": "entrepot",
            "engine": "postgres",
            "params": PG_PARAMS,
            "secret_slug": "pg-slug",
        },
    )
    assert r.status_code == 201, r.text
    assert await db_fetchval(app, "SELECT slug FROM db_connections") == "entrepot"


async def test_slug_invalide_refuse(admin: AsyncClient, app: FastAPI) -> None:
    await _pg_secret(admin, "pg-inv")
    r = await admin.post(
        "/api/db-connections",
        json={
            "label": "PG",
            "slug": "Pas Valide",
            "engine": "postgres",
            "params": PG_PARAMS,
            "secret_slug": "pg-inv",
        },
    )
    assert r.status_code == 400
    assert "connection_slug_invalid" in r.json()["error_args"]["fields"]
    assert await db_fetchval(app, "SELECT count(*) FROM db_connections") == 0


async def test_slug_deja_pris_refuse(admin: AsyncClient, app: FastAPI) -> None:
    await _pg_secret(admin, "pg-pris")
    assert (await _create_pg(admin, "pg-pris", label="PG local")).status_code == 201
    r = await admin.post(
        "/api/db-connections",
        json={
            "label": "Autre",
            "slug": "pg-local",
            "engine": "postgres",
            "params": PG_PARAMS,
            "secret_slug": "pg-pris",
        },
    )
    assert r.status_code == 409
    assert r.json()["error"] == "db_connection_slug_taken"
