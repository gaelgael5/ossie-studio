from __future__ import annotations

from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.app import create_app
from ossie_studio.models import ROLE_ADMIN
from tests.conftest import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    bootstrap_admin,
    db_fetchrow,
    db_fetchval,
    make_settings,
)


async def test_base_vide_session_demande_amorcage(client: AsyncClient) -> None:
    body = (await client.get("/api/session")).json()
    assert body["bootstrap_required"] is True
    assert body["authenticated"] is False


async def test_creation_reussie_connecte_et_donne_role_admin(
    client: AsyncClient, app: FastAPI
) -> None:
    response = await client.post(
        "/api/setup", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200
    assert response.json()["user"] == {"email": ADMIN_EMAIL, "role": ROLE_ADMIN}
    session = (await client.get("/api/session")).json()
    assert session["authenticated"] is True
    assert session["bootstrap_required"] is False
    row = await db_fetchrow(app, "SELECT role, is_bootstrap FROM users")
    assert row is not None
    assert row["role"] == ROLE_ADMIN
    assert row["is_bootstrap"] is True


async def test_mot_de_passe_stocke_hache_jamais_en_clair(client: AsyncClient, app: FastAPI) -> None:
    await bootstrap_admin(client)
    password_hash = await db_fetchval(app, "SELECT password_hash FROM users")
    assert isinstance(password_hash, str)
    assert password_hash.startswith("$argon2id$")
    assert ADMIN_PASSWORD not in password_hash


async def test_setup_refuse_apres_creation(client: AsyncClient) -> None:
    await bootstrap_admin(client)
    rejoue = await client.post(
        "/api/setup", json={"email": "autre@example.org", "password": "un-autre-mot-de-passe"}
    )
    assert rejoue.status_code == 409
    assert rejoue.json()["error"] == "admin_already_exists"


async def test_rejeu_apres_succes_aucun_doublon(client: AsyncClient, app: FastAPI) -> None:
    await bootstrap_admin(client)
    await client.post("/api/setup", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    count = await db_fetchval(app, "SELECT count(*) FROM users")
    assert count == 1


async def test_course_deux_creations_une_seule_reussit(app: FastAPI) -> None:
    """Les deux appels ont passé le contrôle « base vide » : la base tranche."""
    from ossie_studio.auth.service import BootstrapConflictError, db_create_bootstrap_admin

    pool = app.state.pool
    async with pool.acquire() as first, pool.acquire() as second:
        await db_create_bootstrap_admin(first, "a@example.org", "hash-a")
        with pytest.raises(BootstrapConflictError):
            await db_create_bootstrap_admin(second, "b@example.org", "hash-b")
    count = await db_fetchval(app, "SELECT count(*) FROM users")
    assert count == 1


@pytest.mark.parametrize(
    ("email", "password", "field"),
    [
        ("", ADMIN_PASSWORD, "email_required"),
        ("pas-un-email", ADMIN_PASSWORD, "email_invalid"),
        (ADMIN_EMAIL, "", "password_required"),
        (ADMIN_EMAIL, "court", "password_too_short"),
    ],
)
async def test_entree_invalide_refusee_code_champ(
    client: AsyncClient, app: FastAPI, email: str, password: str, field: str
) -> None:
    response = await client.post("/api/setup", json={"email": email, "password": password})
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_request"
    assert field in body["error_args"]["fields"]
    count = await db_fetchval(app, "SELECT count(*) FROM users")
    assert count == 0


async def test_redemarrage_compte_persiste(tmp_path: Path, test_database: str) -> None:
    settings = make_settings(tmp_path, test_database)

    first_app = create_app(settings)
    async with LifespanManager(first_app):
        transport = ASGITransport(app=first_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await bootstrap_admin(client)

    second_app = create_app(settings)
    async with LifespanManager(second_app):
        transport = ASGITransport(app=second_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            session = (await client.get("/api/session")).json()
            assert session["bootstrap_required"] is False
            login = await client.post(
                "/api/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
            )
            assert login.status_code == 200


async def test_course_perdue_rend_409(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """La requête a vu une base vide, mais l'insert perd la course : refus 409."""
    from ossie_studio.auth.service import BootstrapConflictError

    async def _conflit(*_args: object, **_kwargs: object) -> None:
        raise BootstrapConflictError

    monkeypatch.setattr("ossie_studio.api.session.resolve_bootstrap_admin", _conflit)
    response = await client.post(
        "/api/setup", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 409
