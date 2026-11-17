from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.app import create_app
from ossie_studio.config import Settings

TEST_SESSION_SECRET = "0123456789abcdef0123456789abcdef"
ADMIN_EMAIL = "gael@example.org"
ADMIN_PASSWORD = "mot-de-passe-solide"


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        session_secret=TEST_SESSION_SECRET,
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


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
    """Crée le premier administrateur via l'écran de bootstrap."""
    response = await client.post("/setup", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert response.status_code == 303, response.text
