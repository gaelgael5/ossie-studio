from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.applications import Starlette

from ossie_studio.auth.service import db_count_users, db_get_user_by_id
from ossie_studio.crypto import CryptoService
from ossie_studio.models import ROLE_ADMIN, User

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


async def bootstrap_is_done(app: Starlette) -> bool:
    """Vrai dès qu'un compte existe. L'état ne redevient jamais vide en
    fonctionnement normal, donc le résultat positif est mémorisé."""
    if app.state.bootstrap_done:
        return True
    async with app.state.session_factory() as session:
        count = await db_count_users(session)
    if count > 0:
        app.state.bootstrap_done = True
        return True
    return False


async def get_current_user(request: Request, session: AsyncSession) -> User | None:
    raw_id = request.session.get("user_id")
    if raw_id is None:
        return None
    try:
        user_id = uuid.UUID(raw_id)
    except ValueError:
        return None
    return await db_get_user_by_id(session, user_id)


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_crypto(request: Request) -> CryptoService:
    crypto: CryptoService = request.app.state.crypto
    return crypto


def get_http_client_factory(request: Request) -> Callable[[], httpx.AsyncClient]:
    factory: Callable[[], httpx.AsyncClient] = request.app.state.http_client_factory
    return factory


async def require_admin(request: Request, session: SessionDep) -> User:
    """Réservé à l'administrateur : anonyme → login (GET) ou 403, autre rôle → 403."""
    user = await get_current_user(request, session)
    if user is None:
        if request.method == "GET":
            raise HTTPException(status_code=303, headers={"Location": "/login"})
        raise HTTPException(status_code=403)
    if user.role != ROLE_ADMIN:
        raise HTTPException(status_code=403)
    return user
