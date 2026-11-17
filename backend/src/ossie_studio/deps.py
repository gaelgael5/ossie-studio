from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Annotated

import asyncpg
import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from starlette.applications import Starlette

from ossie_studio.auth.service import db_count_users, db_get_user_by_id
from ossie_studio.crypto import CryptoService
from ossie_studio.models import ROLE_ADMIN, User

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


async def get_conn(request: Request) -> AsyncIterator[asyncpg.Connection]:
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        yield conn


ConnDep = Annotated[asyncpg.Connection, Depends(get_conn)]


async def bootstrap_is_done(app: Starlette) -> bool:
    """Vrai dès qu'un compte existe. L'état ne redevient jamais vide en
    fonctionnement normal, donc le résultat positif est mémorisé."""
    if app.state.bootstrap_done:
        return True
    pool: asyncpg.Pool = app.state.pool
    async with pool.acquire() as conn:
        count = await db_count_users(conn)
    if count > 0:
        app.state.bootstrap_done = True
        return True
    return False


async def get_current_user(request: Request, conn: asyncpg.Connection) -> User | None:
    raw_id = request.session.get("user_id")
    if raw_id is None:
        return None
    try:
        user_id = uuid.UUID(raw_id)
    except ValueError:
        return None
    return await db_get_user_by_id(conn, user_id)


def get_crypto(request: Request) -> CryptoService:
    crypto: CryptoService = request.app.state.crypto
    return crypto


def get_http_client_factory(request: Request) -> Callable[[], httpx.AsyncClient]:
    factory: Callable[[], httpx.AsyncClient] = request.app.state.http_client_factory
    return factory


async def require_admin(request: Request, conn: ConnDep) -> User:
    """Réservé à l'administrateur : anonyme → login (GET) ou 403, autre rôle → 403."""
    user = await get_current_user(request, conn)
    if user is None:
        if request.method == "GET":
            raise HTTPException(status_code=303, headers={"Location": "/login"})
        raise HTTPException(status_code=403)
    if user.role != ROLE_ADMIN:
        raise HTTPException(status_code=403)
    return user
