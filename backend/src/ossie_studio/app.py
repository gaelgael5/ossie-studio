from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from ossie_studio.auth.routes import router as auth_router
from ossie_studio.config import Settings
from ossie_studio.crypto import CryptoService
from ossie_studio.db import create_engine, create_session_factory
from ossie_studio.deps import STATIC_DIR, bootstrap_is_done
from ossie_studio.logging import setup_logging
from ossie_studio.models import Base
from ossie_studio.oidc.admin_routes import router as oidc_admin_router
from ossie_studio.oidc.login_routes import router as oidc_login_router
from ossie_studio.pages import router as pages_router
from ossie_studio.secretstore.routes import router as secrets_router


def _default_http_client_factory() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10.0)


# Seules routes accessibles tant que la base utilisateurs est vide.
_PATHS_ALLOWED_WHEN_EMPTY = ("/setup", "/static/", "/healthz")


class BootstrapRedirectMiddleware(BaseHTTPMiddleware):
    """Base vide : toute URL de l'application mène à l'écran de bootstrap."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if path.startswith(_PATHS_ALLOWED_WHEN_EMPTY) or await bootstrap_is_done(request.app):
            return await call_next(request)
        return RedirectResponse("/setup", status_code=302)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()  # type: ignore[call-arg]
    setup_logging()
    engine = create_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Réconciliation additive du schéma (pas encore de migration à rejouer).
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield
        await engine.dispose()

    app = FastAPI(title="Ossie Studio", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.bootstrap_done = False
    app.state.crypto = CryptoService(settings.master_key_bytes())
    # Fabrique de client HTTP sortant (découverte OIDC, JWKS, échange de jeton).
    # Injectable : les tests la remplacent par un client branché sur un faux IdP.
    app.state.http_client_factory = _default_http_client_factory

    app.add_middleware(BootstrapRedirectMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret.get_secret_value(),
        same_site="lax",
        https_only=settings.session_cookie_secure,
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(auth_router)
    app.include_router(pages_router)
    app.include_router(secrets_router)
    app.include_router(oidc_admin_router)
    app.include_router(oidc_login_router)
    return app
