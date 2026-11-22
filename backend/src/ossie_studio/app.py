from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from ossie_studio.api.agent_connections import router as api_agent_connections_router
from ossie_studio.api.api_keys import router as api_api_keys_router
from ossie_studio.api.branches import router as api_branches_router
from ossie_studio.api.classifications import router as api_classifications_router
from ossie_studio.api.connections import router as api_connections_router
from ossie_studio.api.credentials import router as api_credentials_router
from ossie_studio.api.curation import router as api_curation_router
from ossie_studio.api.db_connections import router as api_db_connections_router
from ossie_studio.api.drafts import router as api_drafts_router
from ossie_studio.api.governance import certifications_router as api_certifications_router
from ossie_studio.api.governance import router as api_governance_router
from ossie_studio.api.oauth import router as api_oauth_router
from ossie_studio.api.oidc import router as api_oidc_router
from ossie_studio.api.parsing import router as api_parsing_router
from ossie_studio.api.profiles import router as api_profiles_router
from ossie_studio.api.publish import router as api_publish_router
from ossie_studio.api.raw_explorer import router as api_raw_explorer_router
from ossie_studio.api.secrets import router as api_secrets_router
from ossie_studio.api.session import router as api_session_router
from ossie_studio.api.sources import router as api_sources_router
from ossie_studio.api.space_teams import router as api_space_teams_router
from ossie_studio.api.spaces import router as api_spaces_router
from ossie_studio.api.teams import router as api_teams_router
from ossie_studio.api.users import router as api_users_router
from ossie_studio.api_keys.service import resolve_api_key
from ossie_studio.config import Settings
from ossie_studio.crypto import CryptoService
from ossie_studio.db import create_pool
from ossie_studio.db.migrate import run_migrations
from ossie_studio.errors import ApiError, api_error_handler
from ossie_studio.governance import rules as _governance_rules  # noqa: F401 — enregistre les règles
from ossie_studio.health import resolve_health
from ossie_studio.logging import setup_logging
from ossie_studio.mcp_surface.caller import CallerResolver
from ossie_studio.mcp_surface.guard import McpSurfaceMiddleware
from ossie_studio.mcp_surface.server import build_session_manager
from ossie_studio.oauth.routes import OAuthEndpoints
from ossie_studio.oauth.service import resolve_oauth_token
from ossie_studio.oidc.login_routes import router as oidc_login_router
from ossie_studio.sources.collect import SourceCollector
from ossie_studio.sources.download import SourceDownloader
from ossie_studio.sources.jobs import CollectJobs
from ossie_studio.spaces.git_probe import prove_five_rights
from ossie_studio.spaces.views import DraftViewManager
from ossie_studio.spaces.workdir import WorkdirManager

log = structlog.get_logger(__name__)


def _default_http_client_factory() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10.0)


# Vues éphémères des brouillons : ramassées après 30 min d'inactivité, vérifiées
# toutes les 5 min. Un échec de purge ne doit jamais arrêter l'application.
_PURGE_EVERY_SECONDS = 300


async def _purge_draft_views_forever(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(_PURGE_EVERY_SECONDS)
        try:
            await app.state.draft_views.purge_idle()
        except Exception:
            log.warning("draft_views_purge_failed", exc_info=True)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()  # type: ignore[call-arg]
    setup_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = await create_pool(settings.asyncpg_dsn())
        app.state.pool = pool
        # Serveur d'autorisation de la surface MCP (a besoin du pool).
        app.state.oauth_endpoints = OAuthEndpoints(pool, app.state.crypto)
        await run_migrations(pool)
        purge = asyncio.create_task(_purge_draft_views_forever(app))
        async with app.state.mcp_session_manager.run():
            yield
        purge.cancel()
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
    app.state.draft_views = DraftViewManager(app.state.workdir_manager)
    app.state.source_downloader = SourceDownloader(settings.sources_volume_path)
    app.state.source_collector = SourceCollector(app.state.source_downloader)
    app.state.collect_jobs = CollectJobs()
    # Surface MCP : un résolveur par sorte de jeton (clés d'API, puis OAuth) ;
    # un jeton qu'aucun ne reconnaît est refusé (fermé par défaut).
    mcp_resolvers: list[CallerResolver] = [resolve_api_key, resolve_oauth_token]
    app.state.mcp_resolvers = mcp_resolvers
    app.state.mcp_session_manager = build_session_manager(app)

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
    app.include_router(api_db_connections_router)
    app.include_router(api_spaces_router)
    app.include_router(api_branches_router)
    app.include_router(api_sources_router)
    app.include_router(api_parsing_router)
    app.include_router(api_curation_router)
    app.include_router(api_raw_explorer_router)
    app.include_router(api_governance_router)
    app.include_router(api_certifications_router)
    app.include_router(api_classifications_router)
    app.include_router(api_profiles_router)
    app.include_router(api_users_router)
    app.include_router(api_teams_router)
    app.include_router(api_space_teams_router)
    app.include_router(api_credentials_router)
    app.include_router(api_drafts_router)
    app.include_router(api_publish_router)
    app.include_router(api_api_keys_router)
    app.include_router(api_oauth_router)
    app.include_router(api_agent_connections_router)
    # Flux OIDC : navigations du navigateur (redirections), pas de l'API JSON.
    app.include_router(oidc_login_router)
    # Ajouté en dernier, donc le plus à l'extérieur : `/mcp` est aiguillé avant
    # la session et le routeur de l'IHM (voir mcp_surface/guard.py).
    app.add_middleware(McpSurfaceMiddleware)
    return app
