from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.app import create_app
from ossie_studio.config import Settings

TEST_SESSION_SECRET = "0123456789abcdef0123456789abcdef"
TEST_MASTER_KEY = "aa" * 32
OTHER_MASTER_KEY = "bb" * 32
ADMIN_EMAIL = "gael@example.org"
ADMIN_PASSWORD = "mot-de-passe-solide"

# Base d'admin pour créer/supprimer les bases jetables. Surchargée par
# l'environnement sur une machine de test ; défaut = Postgres local de dev.
ADMIN_DSN = os.environ.get(
    "OSSIE_TEST_ADMIN_DSN", "postgresql://ossie:ossie@127.0.0.1:5432/postgres"
)


def _volumes(tmp_path: Path) -> tuple[Path, Path]:
    work = tmp_path / "work"
    sources = tmp_path / "sources"
    work.mkdir(parents=True, exist_ok=True)
    sources.mkdir(parents=True, exist_ok=True)
    return work, sources


def make_settings(tmp_path: Path, database_url: str, master_key: str = TEST_MASTER_KEY) -> Settings:
    work, sources = _volumes(tmp_path)
    return Settings(
        database_url=database_url,
        work_volume_path=work,
        sources_volume_path=sources,
        session_secret=TEST_SESSION_SECRET,
        secrets_master_key=master_key,
        # Les tests modifient la forge entre deux lectures : réalignement à chaque fois.
        git_read_follows_forge=True,
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
async def test_database() -> AsyncIterator[str]:
    """Crée une base jetable, la rend, puis la supprime après le test."""
    dbname = f"test_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f'CREATE DATABASE "{dbname}"')
    await admin.close()
    base = ADMIN_DSN.rsplit("/", 1)[0]
    try:
        yield f"{base}/{dbname}"
    finally:
        admin = await asyncpg.connect(ADMIN_DSN)
        # FORCE déconnecte les sessions restantes au moment du drop, sans avoir
        # à terminer des backends que le rôle de test ne peut pas tuer.
        await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        await admin.close()


@pytest.fixture
def settings(tmp_path: Path, test_database: str) -> Settings:
    return make_settings(tmp_path, test_database)


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def bootstrap_admin(client: AsyncClient) -> None:
    """Crée le premier administrateur via l'API d'amorçage."""
    response = await client.post(
        "/api/setup", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200, response.text


async def create_plain_user(app: FastAPI, email: str, password: str) -> None:
    """Insère un utilisateur au rôle par défaut, directement en base."""
    from ossie_studio.models import ROLE_USER
    from ossie_studio.security import hash_password

    pool: asyncpg.Pool = app.state.pool
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (email, password_hash, role) VALUES ($1, $2, $3)",
            email,
            hash_password(password),
            ROLE_USER,
        )


async def login(client: AsyncClient, email: str, password: str) -> None:
    response = await client.post("/api/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text


def wire_fake_idp(app: FastAPI, idp_asgi: object) -> None:
    """Branche la fabrique de client HTTP de l'app sur un faux IdP ASGI."""
    from httpx import ASGITransport, AsyncClient

    def factory() -> AsyncClient:
        return AsyncClient(transport=ASGITransport(app=idp_asgi), base_url="http://idp.test")

    app.state.http_client_factory = factory


async def db_fetch(app: FastAPI, query: str, *args: object) -> list[asyncpg.Record]:
    pool: asyncpg.Pool = app.state.pool
    async with pool.acquire() as conn:
        return await conn.fetch(query, *args)


async def db_fetchrow(app: FastAPI, query: str, *args: object) -> asyncpg.Record | None:
    pool: asyncpg.Pool = app.state.pool
    async with pool.acquire() as conn:
        return await conn.fetchrow(query, *args)


async def db_fetchval(app: FastAPI, query: str, *args: object) -> object:
    pool: asyncpg.Pool = app.state.pool
    async with pool.acquire() as conn:
        return await conn.fetchval(query, *args)


def wire_git_probe(app: FastAPI, result: object) -> None:
    """Branche une sonde git factice qui rend `result` sans toucher au dépôt."""

    async def _probe(_repo_url: str, *, username: str, token: str | None) -> object:
        return result

    app.state.git_probe = _probe
