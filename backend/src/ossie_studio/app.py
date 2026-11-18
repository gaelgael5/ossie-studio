from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from ossie_studio.api.branches import router as api_branches_router
from ossie_studio.api.classifications import router as api_classifications_router
from ossie_studio.api.connections import router as api_connections_router
from ossie_studio.api.governance import certifications_router as api_certifications_router
from ossie_studio.api.governance import router as api_governance_router
from ossie_studio.api.oidc import router as api_oidc_router
from ossie_studio.api.profiles import router as api_profiles_router
from ossie_studio.api.secrets import router as api_secrets_router
from ossie_studio.api.session import router as api_session_router
from ossie_studio.api.spaces import router as api_spaces_router
from ossie_studio.config import Settings
from ossie_studio.crypto import CryptoService
from ossie_studio.db import create_pool
from ossie_studio.db.migrate import run_migrations
from ossie_studio.errors import ApiError, api_error_handler
from ossie_studio.health import resolve_health
from ossie_studio.logging import setup_logging
from ossie_studio.oidc.login_routes import router as oidc_login_router
from ossie_studio.spaces.git_probe import prove_five_rights
from ossie_studio.spaces.workdir import WorkdirManager


def _default_http_client_factory() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10.0)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()  # type: ignore[call-arg]
    setup_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = await create_pool(settings.asyncpg_dsn())
        app.state.pool = pool
        await run_migrations(pool)
        yield
        await pool.close()

    app = FastAPI(title="Ossie Studio", lifespan=lifespan)
    # L'API renvoie des codes d'erreur traduits par l'interface, jamais un
    # message rédigé côté backend.
    app.add_exception_handler(ApiError, api_error_handler)
    app.state.settings = settings
    app.state.bootstrap_done = False
    app.state.crypto = CryptoService(settings.master_key_bytes())
    # Fabrique de client HTTP sortant (découverte OIDC, JWKS, échange de jeton).
    # Injectable : les tests la remplacent par un client branché sur un faux IdP.
    app.state.http_client_factory = _default_http_client_factory
    app.state.git_probe = prove_five_rights
    app.state.workdir_manager = WorkdirManager(settings.work_volume_path)

    @app.get("/api/health")
    async def health(request: Request) -> Response:
        report = await resolve_health(request.app.state.pool, settings)
        status_code = 200 if report.ok else 503
        return JSONResponse(report.to_dict(), status_code=status_code)

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret.get_secret_value(),
        same_site="lax",
        https_only=settings.session_cookie_secure,
    )
    # API JSON consommée par le frontend React.
    app.include_router(api_session_router)
    app.include_router(api_secrets_router)
    app.include_router(api_oidc_router)
    app.include_router(api_connections_router)
    app.include_router(api_spaces_router)
    app.include_router(api_branches_router)
    app.include_router(api_governance_router)
    app.include_router(api_certifications_router)
    app.include_router(api_classifications_router)
    app.include_router(api_profiles_router)
    # Flux OIDC : navigations du navigateur (redirections), pas de l'API JSON.
    app.include_router(oidc_login_router)
    return app
