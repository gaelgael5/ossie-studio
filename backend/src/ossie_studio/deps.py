from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.applications import Starlette

from ossie_studio.auth.service import db_count_users, db_get_user_by_id
from ossie_studio.models import User

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
