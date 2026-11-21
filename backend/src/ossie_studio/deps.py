from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

import asyncpg
import httpx
from fastapi import Depends, Request
from starlette.applications import Starlette

from ossie_studio.auth.service import db_count_users, db_get_user_by_id
from ossie_studio.config import Settings
from ossie_studio.crypto import CryptoService
from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.models import User
from ossie_studio.sources.collect import SourceCollector
from ossie_studio.sources.download import SourceDownloader
from ossie_studio.spaces.git_probe import ProbeResult
from ossie_studio.spaces.workdir import WorkdirManager

GitProbe = Callable[..., Awaitable[ProbeResult]]


async def get_conn(request: Request) -> AsyncIterator[asyncpg.Connection]:
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        yield conn


async def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


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


def get_git_probe(request: Request) -> GitProbe:
    # Sonde des cinq droits git. Injectable : les tests la remplacent par un
    # stub qui ne touche à aucun dépôt réel.
    probe: GitProbe = request.app.state.git_probe
    return probe


def get_workdir_manager(request: Request) -> WorkdirManager:
    manager: WorkdirManager = request.app.state.workdir_manager
    return manager


def get_source_downloader(request: Request) -> SourceDownloader:
    downloader: SourceDownloader = request.app.state.source_downloader
    return downloader


def get_source_collector(request: Request) -> SourceCollector:
    collector: SourceCollector = request.app.state.source_collector
    return collector


async def get_draft_workdir(request: Request) -> DraftWorkdir:
    """Copie de travail de la personne connectée sur la branche de la route
    (`branch_id`) : lectures avec son brouillon, écritures dans son brouillon."""
    from ossie_studio.errors import ApiError

    async with request.app.state.pool.acquire() as conn:
        user = await get_current_user(request, conn)
    if user is None:
        raise ApiError("unauthenticated", status_code=401)
    try:
        branch_id = uuid.UUID(str(request.path_params["branch_id"]))
    except (KeyError, ValueError):
        raise ApiError("branch_not_found", status_code=404) from None
    return DraftWorkdir(
        request.app.state.workdir_manager,
        request.app.state.draft_views,
        request.app.state.pool,
        user.id,
        branch_id,
        published_only=request.query_params.get("view") == "published",
    )
