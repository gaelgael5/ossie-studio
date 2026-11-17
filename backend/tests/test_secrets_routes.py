from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.conftest import bootstrap_admin, create_plain_user, db_fetchrow, db_fetchval, login

VALEUR = "ghp_un-jeton-github-discriminant"


@pytest.fixture
async def admin_client(client: AsyncClient) -> AsyncClient:
    await bootstrap_admin(client)
    return client


async def _count_secrets(app: FastAPI) -> int:
    return int(await db_fetchval(app, "SELECT count(*) FROM secrets"))  # type: ignore[arg-type]


async def _create(client: AsyncClient, slug: str = "github-ci", value: str = VALEUR) -> None:
    response = await client.post(
        "/api/secrets",
        json={"slug": slug, "label": "Jeton CI", "category": "github", "value": value},
    )
    assert response.status_code == 201, response.text


async def test_creation_la_base_contient_un_chiffre_jamais_la_valeur(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    await _create(admin_client)
    row = await db_fetchrow(app, "SELECT slug, category, value_encrypted FROM secrets")
    assert row is not None
    assert VALEUR.encode() not in row["value_encrypted"]
    assert row["slug"] == "github-ci"
    assert row["category"] == "github"


async def test_liste_ne_renvoie_jamais_la_valeur(admin_client: AsyncClient, app: FastAPI) -> None:
    await _create(admin_client)
    body = (await admin_client.get("/api/secrets")).json()
    assert [s["slug"] for s in body["secrets"]] == ["github-ci"]
    serialized = str(body)
    assert VALEUR not in serialized
    assert "value" not in body["secrets"][0]


async def test_remplacer_la_valeur_conserve_le_slug(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    await _create(admin_client)
    before = await db_fetchval(app, "SELECT value_encrypted FROM secrets")
    response = await admin_client.put("/api/secrets/github-ci/value", json={"value": "nouvelle"})
    assert response.status_code == 200
    row = await db_fetchrow(app, "SELECT slug, value_encrypted FROM secrets")
    assert row is not None
    assert row["slug"] == "github-ci"
    assert row["value_encrypted"] != before
    assert app.state.crypto.decrypt(row["value_encrypted"]).reveal() == "nouvelle"


async def test_slug_deja_pris_refuse(admin_client: AsyncClient, app: FastAPI) -> None:
    await _create(admin_client)
    response = await admin_client.post(
        "/api/secrets",
        json={"slug": "github-ci", "label": "Doublon", "category": "github", "value": "x-valeur"},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "secret_slug_taken"
    assert await _count_secrets(app) == 1


@pytest.mark.parametrize(
    ("slug", "category", "value", "field"),
    [
        ("github-ci", "", VALEUR, "category_required"),
        ("github-ci", "inconnue", VALEUR, "secret_category_unknown"),
        ("github-ci", "github", "", "secret_value_required"),
        ("Slug Invalide", "github", VALEUR, "slug_invalid"),
    ],
)
async def test_entree_invalide_rien_enregistre(
    admin_client: AsyncClient, app: FastAPI, slug: str, category: str, value: str, field: str
) -> None:
    response = await admin_client.post(
        "/api/secrets",
        json={"slug": slug, "label": "Libellé", "category": category, "value": value},
    )
    assert response.status_code == 400
    assert field in response.json()["error_args"]["fields"]
    assert await _count_secrets(app) == 0


async def test_modifier_le_libelle_fonctionne(admin_client: AsyncClient, app: FastAPI) -> None:
    await _create(admin_client)
    response = await admin_client.put("/api/secrets/github-ci/label", json={"label": "Nouveau"})
    assert response.status_code == 200
    label = await db_fetchval(app, "SELECT label FROM secrets")
    assert label == "Nouveau"


async def test_suppression_inexistant_introuvable_sans_erreur_serveur(
    admin_client: AsyncClient,
) -> None:
    first = await admin_client.delete("/api/secrets/fantome")
    assert first.status_code == 404
    assert first.json()["error"] == "secret_not_found"
    replayed = await admin_client.delete("/api/secrets/fantome")
    assert replayed.status_code == 404


async def test_suppression_puis_rejeu(admin_client: AsyncClient) -> None:
    await _create(admin_client)
    assert (await admin_client.delete("/api/secrets/github-ci")).status_code == 204
    assert (await admin_client.delete("/api/secrets/github-ci")).status_code == 404


@pytest.fixture
async def user_client(admin_client: AsyncClient, app: FastAPI) -> AsyncIterator[AsyncClient]:
    await create_plain_user(app, "simple@example.org", "mot-de-passe-simple")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as second:
        await login(second, "simple@example.org", "mot-de-passe-simple")
        yield second


async def test_non_admin_refus_403(user_client: AsyncClient) -> None:
    assert (await user_client.get("/api/secrets")).status_code == 403
    creation = await user_client.post(
        "/api/secrets",
        json={"slug": "intrus", "label": "Intrus", "category": "github", "value": "v-intrus"},
    )
    assert creation.status_code == 403
    assert (await user_client.delete("/api/secrets/github-ci")).status_code == 403


async def test_anonyme_non_authentifie_401(admin_client: AsyncClient, app: FastAPI) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as anonymous:
        response = await anonymous.get("/api/secrets")
        assert response.status_code == 401
        assert response.json()["error"] == "unauthenticated"
