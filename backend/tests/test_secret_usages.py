from __future__ import annotations

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.crypto import CryptoService, SecretValue
from ossie_studio.secretstore.service import db_create_secret
from ossie_studio.secretstore.usages import (
    db_count_secret_usages,
    db_delete_secret_if_unused,
    db_list_secret_usages,
)
from tests.conftest import bootstrap_admin


async def _secret(app: FastAPI, slug: str, category: str = "github") -> None:
    crypto: CryptoService = app.state.crypto
    async with app.state.pool.acquire() as conn:
        await db_create_secret(
            conn, slug, f"Libellé {slug}", category, crypto.encrypt(SecretValue(f"v-{slug}"))
        )


async def _connection(app: FastAPI, secret_slug: str, label: str) -> None:
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO connections (label, forge, url, secret_slug, identity, last_status) "
            "VALUES ($1, 'github', $2, $3, 'moi', 'valid')",
            label,
            f"https://git/{label}",
            secret_slug,
        )


# --- Décompte et suppression --------------------------------------------------

async def test_decompte_et_liste_des_usages(app: FastAPI) -> None:
    await _secret(app, "s-partage")
    await _connection(app, "s-partage", "Alpha")
    await _connection(app, "s-partage", "Bravo")
    async with app.state.pool.acquire() as conn:
        assert await db_count_secret_usages(conn, "s-partage") == 2
        # « sauf tel objet » : écarte une connexion précise (pour sa propre suppression).
        one = await conn.fetchval("SELECT id FROM connections WHERE label = 'Alpha'")
        assert await db_count_secret_usages(conn, "s-partage", exclude=("connections", one)) == 1
        usages = await db_list_secret_usages(conn, "s-partage")
    assert {u["label"] for u in usages} == {"Alpha", "Bravo"}
    assert all(u["kind"] == "connection" for u in usages)


async def test_supprimer_si_inutilise(app: FastAPI) -> None:
    await _secret(app, "s-libre")
    await _secret(app, "s-pris")
    await _connection(app, "s-pris", "Charlie")
    async with app.state.pool.acquire() as conn:
        assert await db_delete_secret_if_unused(conn, "s-pris") == "in_use"
        assert await db_delete_secret_if_unused(conn, "s-libre") == "deleted"
        assert await db_delete_secret_if_unused(conn, "s-absent") == "not_found"
        assert await conn.fetchval("SELECT 1 FROM secrets WHERE slug = 's-pris'") == 1
        assert await conn.fetchval("SELECT 1 FROM secrets WHERE slug = 's-libre'") is None


async def test_fk_empeche_de_supprimer_un_secret_utilise(app: FastAPI) -> None:
    # Garde-fou d'intégrité : un secret encore désigné ne peut pas disparaître,
    # même par une suppression directe — c'est ce qui rend sûr « supprimer si
    # inutilisé » quand un usage apparaît entre-temps.
    await _secret(app, "s-fk")
    await _connection(app, "s-fk", "Delta")
    async with app.state.pool.acquire() as conn:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute("DELETE FROM secrets WHERE slug = 's-fk'")


async def test_usage_apparu_pendant_la_suppression_bloque(app: FastAPI) -> None:
    # Deux connexions distinctes : un usage est inséré puis validé avant que la
    # suppression ne s'engage → la contrainte la refuse, le secret reste.
    await _secret(app, "s-course")
    pool = app.state.pool
    async with pool.acquire() as a, pool.acquire() as b:
        async with a.transaction():
            # L'objet se met à utiliser le secret, sur une autre connexion.
            await b.execute(
                "INSERT INTO connections (label, forge, url, secret_slug, identity, last_status) "
                "VALUES ('Echo', 'github', 'https://git/echo', 's-course', 'moi', 'valid')"
            )
            with pytest.raises(asyncpg.ForeignKeyViolationError):
                await a.execute("DELETE FROM secrets WHERE slug = 's-course'")
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT 1 FROM secrets WHERE slug = 's-course'") == 1


# --- API ----------------------------------------------------------------------

@pytest.fixture
async def admin(client: AsyncClient) -> AsyncClient:
    await bootstrap_admin(client)
    return client


async def test_api_liste_par_categorie(admin: AsyncClient, app: FastAPI) -> None:
    await _secret(app, "gh-1", "github")
    await _secret(app, "oidc-1", "oidc")
    seulement_oidc = (await admin.get("/api/secrets?category=oidc")).json()["secrets"]
    assert [s["slug"] for s in seulement_oidc] == ["oidc-1"]
    # Catégorie inconnue → refus explicite.
    inconnue = await admin.get("/api/secrets?category=martien")
    assert inconnue.status_code == 400
    assert inconnue.json()["error"] == "secret_category_unknown"


async def test_api_used_by_et_suppression_refusee(admin: AsyncClient, app: FastAPI) -> None:
    await _secret(app, "s-api")
    await _connection(app, "s-api", "Foxtrot")
    secrets = (await admin.get("/api/secrets")).json()["secrets"]
    me = next(s for s in secrets if s["slug"] == "s-api")
    assert me["used_by"] == 1
    # Suppression refusée tant qu'un objet l'utilise.
    refus = await admin.delete("/api/secrets/s-api")
    assert refus.status_code == 409
    assert refus.json()["error"] == "secret_in_use"
    # On retire l'usage, la suppression redevient possible.
    async with app.state.pool.acquire() as conn:
        await conn.execute("DELETE FROM connections WHERE label = 'Foxtrot'")
    assert (await admin.delete("/api/secrets/s-api")).status_code == 204


async def test_api_renommer_et_remplacer_valeur(admin: AsyncClient, app: FastAPI) -> None:
    await _secret(app, "s-edit")
    renamed = await admin.put("/api/secrets/s-edit/label", json={"label": "Nouveau libellé"})
    assert renamed.status_code == 200
    assert renamed.json()["label"] == "Nouveau libellé"
    replaced = await admin.put("/api/secrets/s-edit/value", json={"value": "nouvelle-valeur"})
    assert replaced.status_code == 200
    # Le slug ne change pas ; la valeur n'est jamais renvoyée.
    assert replaced.json()["slug"] == "s-edit"
    assert "value" not in replaced.json()


async def test_api_oidc_est_une_categorie_connue(admin: AsyncClient) -> None:
    payload = (await admin.get("/api/secrets")).json()
    assert "oidc" in payload["categories"]
    assert payload["category_inputs"]["oidc"] == "masked"
