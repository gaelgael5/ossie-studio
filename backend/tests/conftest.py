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
TEST_MASTER_KEY = "aa" * 32
OTHER_MASTER_KEY = "bb" * 32
ADMIN_EMAIL = "gael@example.org"
ADMIN_PASSWORD = "mot-de-passe-solide"


def make_settings(tmp_path: Path, master_key: str = TEST_MASTER_KEY) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        session_secret=TEST_SESSION_SECRET,
        secrets_master_key=master_key,
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


async def create_plain_user(app: FastAPI, email: str, password: str) -> None:
    """Insère un utilisateur au rôle par défaut, directement en base."""
    from ossie_studio.models import ROLE_USER, User
    from ossie_studio.security import hash_password

    async with app.state.session_factory() as session:
        session.add(User(email=email, password_hash=hash_password(password), role=ROLE_USER))
        await session.commit()


async def login(client: AsyncClient, email: str, password: str) -> None:
    response = await client.post("/login", data={"email": email, "password": password})
    assert response.status_code == 303, response.text


def wire_fake_idp(app: FastAPI, idp_asgi: object) -> None:
    """Branche la fabrique de client HTTP de l'app sur un faux IdP ASGI."""
    from httpx import ASGITransport, AsyncClient

    def factory() -> AsyncClient:
        return AsyncClient(transport=ASGITransport(app=idp_asgi), base_url="http://idp.test")

    app.state.http_client_factory = factory
