from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from ossie_studio.models import Secret
from tests.conftest import bootstrap_admin, create_plain_user, login

VALEUR = "ghp_un-jeton-github-discriminant"


@pytest.fixture
async def admin_client(client: AsyncClient) -> AsyncClient:
    await bootstrap_admin(client)
    return client


async def _count_secrets(app: FastAPI) -> int:
    async with app.state.session_factory() as session:
        return len(list((await session.execute(select(Secret))).scalars()))


async def _create(client: AsyncClient, slug: str = "github-ci", value: str = VALEUR) -> None:
    response = await client.post(
        "/secrets",
        data={"slug": slug, "label": "Jeton CI", "category": "github", "value": value},
    )
    assert response.status_code == 303, response.text


async def test_creation_la_base_contient_un_chiffre_jamais_la_valeur(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    await _create(admin_client)
    async with app.state.session_factory() as session:
        secret = (await session.execute(select(Secret))).scalar_one()
    assert VALEUR.encode() not in secret.value_encrypted
    assert secret.slug == "github-ci"
    assert secret.category == "github"


async def test_liste_ne_renvoie_jamais_la_valeur(admin_client: AsyncClient, app: FastAPI) -> None:
    await _create(admin_client)
    page = await admin_client.get("/secrets")
    assert page.status_code == 200
    assert "github-ci" in page.text
    assert VALEUR not in page.text
    async with app.state.session_factory() as session:
        secret = (await session.execute(select(Secret))).scalar_one()
    assert secret.value_encrypted.hex() not in page.text


async def test_remplacer_la_valeur_conserve_le_slug(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    await _create(admin_client)
    async with app.state.session_factory() as session:
        before = (await session.execute(select(Secret))).scalar_one().value_encrypted
    response = await admin_client.post(
        "/secrets/github-ci/value", data={"value": "nouvelle-valeur"}
    )
    assert response.status_code == 303
    async with app.state.session_factory() as session:
        secret = (await session.execute(select(Secret))).scalar_one()
    assert secret.slug == "github-ci"
    assert secret.value_encrypted != before
    assert app.state.crypto.decrypt(secret.value_encrypted).reveal() == "nouvelle-valeur"


async def test_slug_deja_pris_refuse(admin_client: AsyncClient, app: FastAPI) -> None:
    await _create(admin_client)
    response = await admin_client.post(
        "/secrets",
        data={"slug": "github-ci", "label": "Doublon", "category": "github", "value": "x-valeur"},
    )
    assert response.status_code == 409
    assert "déjà pris" in response.text
    assert await _count_secrets(app) == 1


@pytest.mark.parametrize(
    ("slug", "category", "value"),
    [
        ("github-ci", "", VALEUR),  # catégorie absente
        ("github-ci", "inconnue", VALEUR),  # catégorie inconnue
        ("github-ci", "github", ""),  # valeur vide
        ("Slug Invalide", "github", VALEUR),  # slug mal formé
    ],
)
async def test_entree_invalide_rien_enregistre(
    admin_client: AsyncClient, app: FastAPI, slug: str, category: str, value: str
) -> None:
    response = await admin_client.post(
        "/secrets", data={"slug": slug, "label": "Libellé", "category": category, "value": value}
    )
    assert response.status_code == 400
    assert await _count_secrets(app) == 0


async def test_modifier_le_slug_refuse(admin_client: AsyncClient, app: FastAPI) -> None:
    await _create(admin_client)
    response = await admin_client.post(
        "/secrets/github-ci/label", data={"label": "Nouveau libellé", "slug": "autre-slug"}
    )
    assert response.status_code == 400
    assert "pas modifiable" in response.text
    async with app.state.session_factory() as session:
        secret = (await session.execute(select(Secret))).scalar_one()
    assert secret.slug == "github-ci"


async def test_modifier_le_libelle_fonctionne(admin_client: AsyncClient, app: FastAPI) -> None:
    await _create(admin_client)
    response = await admin_client.post(
        "/secrets/github-ci/label", data={"label": "Nouveau libellé"}
    )
    assert response.status_code == 303
    async with app.state.session_factory() as session:
        secret = (await session.execute(select(Secret))).scalar_one()
    assert secret.label == "Nouveau libellé"


async def test_suppression_inexistant_introuvable_sans_erreur_serveur(
    admin_client: AsyncClient,
) -> None:
    first = await admin_client.post("/secrets/fantome/delete")
    assert first.status_code == 404
    replayed = await admin_client.post("/secrets/fantome/delete")
    assert replayed.status_code == 404


async def test_suppression_puis_rejeu_meme_reponse(admin_client: AsyncClient) -> None:
    await _create(admin_client)
    assert (await admin_client.post("/secrets/github-ci/delete")).status_code == 303
    assert (await admin_client.post("/secrets/github-ci/delete")).status_code == 404


@pytest.fixture
async def user_client(admin_client: AsyncClient, app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Client authentifié avec un compte au rôle par défaut (non admin)."""
    await create_plain_user(app, "simple@example.org", "mot-de-passe-simple")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as second:
        await login(second, "simple@example.org", "mot-de-passe-simple")
        yield second


async def test_non_admin_refus_403(user_client: AsyncClient) -> None:
    assert (await user_client.get("/secrets")).status_code == 403
    creation = await user_client.post(
        "/secrets",
        data={"slug": "intrus", "label": "Intrus", "category": "github", "value": "v-intrus"},
    )
    assert creation.status_code == 403
    assert (await user_client.post("/secrets/github-ci/delete")).status_code == 403


async def test_anonyme_redirige_vers_login(admin_client: AsyncClient, app: FastAPI) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as anonymous:
        response = await anonymous.get("/secrets")
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
        assert (await anonymous.post("/secrets/github-ci/delete")).status_code == 403
