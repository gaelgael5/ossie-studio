from __future__ import annotations

from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from ossie_studio.app import create_app
from ossie_studio.auth.service import (
    BootstrapConflictError,
    db_count_users,
    db_create_bootstrap_admin,
)
from ossie_studio.models import ROLE_ADMIN, User
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, bootstrap_admin, make_settings


@pytest.mark.parametrize("path", ["/", "/login", "/nimporte/quoi"])
async def test_base_vide_toute_url_redirige_vers_setup(client: AsyncClient, path: str) -> None:
    response = await client.get(path)
    assert response.status_code == 302
    assert response.headers["location"] == "/setup"


async def test_base_vide_setup_accessible(client: AsyncClient) -> None:
    response = await client.get("/setup")
    assert response.status_code == 200
    assert "administrateur" in response.text


async def test_creation_reussie_connecte_et_donne_role_admin(
    client: AsyncClient, app: FastAPI
) -> None:
    await bootstrap_admin(client)
    home = await client.get("/")
    assert home.status_code == 200
    assert ADMIN_EMAIL in home.text
    async with app.state.session_factory() as session:
        user = (await session.execute(select(User))).scalar_one()
    assert user.role == ROLE_ADMIN
    assert user.is_bootstrap is True


async def test_mot_de_passe_stocke_hache_jamais_en_clair(client: AsyncClient, app: FastAPI) -> None:
    await bootstrap_admin(client)
    async with app.state.session_factory() as session:
        user = (await session.execute(select(User))).scalar_one()
    assert user.password_hash.startswith("$argon2id$")
    assert ADMIN_PASSWORD not in user.password_hash


async def test_setup_repond_inexistant_apres_creation(client: AsyncClient) -> None:
    await bootstrap_admin(client)
    assert (await client.get("/setup")).status_code == 404
    rejoue = await client.post(
        "/setup", data={"email": "autre@example.org", "password": "un-autre-mot-de-passe"}
    )
    assert rejoue.status_code == 404


async def test_rejeu_apres_succes_aucun_doublon(client: AsyncClient, app: FastAPI) -> None:
    await bootstrap_admin(client)
    await client.post("/setup", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    async with app.state.session_factory() as session:
        assert await db_count_users(session) == 1


async def test_course_deux_creations_une_seule_reussit(app: FastAPI) -> None:
    """Les deux appels ont passé le contrôle « base vide » : la base tranche."""
    factory = app.state.session_factory
    async with factory() as first, factory() as second:
        await db_create_bootstrap_admin(first, "a@example.org", "hash-a")
        with pytest.raises(BootstrapConflictError):
            await db_create_bootstrap_admin(second, "b@example.org", "hash-b")
    async with factory() as session:
        assert await db_count_users(session) == 1


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("", ADMIN_PASSWORD),
        ("pas-un-email", ADMIN_PASSWORD),
        (ADMIN_EMAIL, ""),
        (ADMIN_EMAIL, "court"),
    ],
)
async def test_entree_invalide_refusee_aucun_compte_cree(
    client: AsyncClient, app: FastAPI, email: str, password: str
) -> None:
    response = await client.post("/setup", data={"email": email, "password": password})
    assert response.status_code == 400
    assert "<li>" in response.text  # un message d'erreur est affiché
    async with app.state.session_factory() as session:
        assert await db_count_users(session) == 0


async def test_redemarrage_compte_persiste_et_setup_ferme(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    first_app = create_app(settings)
    async with LifespanManager(first_app):
        transport = ASGITransport(app=first_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await bootstrap_admin(client)

    second_app = create_app(settings)
    async with LifespanManager(second_app):
        transport = ASGITransport(app=second_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/setup")).status_code == 404
            login = await client.post(
                "/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
            )
            assert login.status_code == 303
            assert (await client.get("/")).status_code == 200


async def test_course_perdue_rend_409(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """La requête a vu une base vide, mais l'insert perd la course : refus 409."""

    async def _conflit(*_args: object, **_kwargs: object) -> None:
        raise BootstrapConflictError

    monkeypatch.setattr("ossie_studio.auth.routes.resolve_bootstrap_admin", _conflit)
    response = await client.post("/setup", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert response.status_code == 409
