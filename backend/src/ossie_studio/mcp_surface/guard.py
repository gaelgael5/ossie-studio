"""Aiguillage et garde d'authentification de la surface MCP.

Posé **devant** toute la pile de l'IHM : une requête vers `/mcp` ne traverse ni
le middleware de session ni le routeur `/api`. Conséquences voulues :

- un cookie de session de l'IHM n'ouvre pas la surface MCP (il n'est pas lu) ;
- un jeton MCP n'ouvre aucune route de l'IHM (elles ne lisent que la session).

Seul l'en-tête `Authorization: Bearer <jeton>` est lu ; jamais l'URL. Sans
jeton reconnu par un résolveur → 401 conforme à la spécification MCP, avec
`WWW-Authenticate` désignant le document de métadonnées de ressource protégée.
Inconnu, révoqué ou expiré : la même réponse, rien ne les distingue.
"""

from __future__ import annotations

import structlog
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ossie_studio.mcp_surface.caller import (
    CallerResolver,
    McpCaller,
    TokenRefusedError,
    current_caller,
)

log = structlog.get_logger(__name__)

MCP_PATH = "/mcp"
_MCP_PATHS = frozenset({MCP_PATH, f"{MCP_PATH}/"})
PROTECTED_RESOURCE_METADATA_PATH = f"/.well-known/oauth-protected-resource{MCP_PATH}"


def public_base(request: Request) -> str:
    """Adresse publique d'Ossie-studio : configurée, ou à défaut celle de la requête."""
    base = (getattr(request.app.state.settings, "public_base_url", "") or "").strip()
    return (base or str(request.base_url)).rstrip("/")


def resource_url(request: Request) -> str:
    """Adresse de la surface MCP : ce que les clients déclarent, et l'audience des jetons."""
    return f"{public_base(request)}{MCP_PATH}"


def _bearer(scope: Scope) -> tuple[bool, str | None]:
    """(un en-tête Authorization est présent ?, jeton Bearer ou None)."""
    values = [v for k, v in scope.get("headers", []) if k.lower() == b"authorization"]
    if not values:
        return False, None
    if len(values) > 1:
        return True, None
    scheme, _, token = values[0].decode("latin-1").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return True, None
    return True, token


def _unauthorized(request: Request, presented: bool, reason: str | None = None) -> JSONResponse:
    metadata = f"{public_base(request)}{PROTECTED_RESOURCE_METADATA_PATH}"
    # RFC 6750 §3.1 : sans jeton, pas de code d'erreur ; jeton refusé →
    # invalid_token, avec le motif quand le client peut y remédier.
    params = [f'resource_metadata="{metadata}"']
    body: dict[str, str] = {"error": "invalid_token" if presented else "unauthenticated"}
    if presented:
        params.insert(0, 'error="invalid_token"')
    if reason is not None:
        params.insert(1, f'error_description="{reason}"')
        body["error_description"] = reason
    return JSONResponse(
        body, status_code=401, headers={"WWW-Authenticate": "Bearer " + ", ".join(params)}
    )


async def _resolve(request: Request, token: str) -> McpCaller | None:
    resolvers: list[CallerResolver] = request.app.state.mcp_resolvers
    resource = resource_url(request)
    async with request.app.state.pool.acquire() as conn:
        for resolver in resolvers:
            caller = await resolver(conn, token, resource)
            if caller is not None:
                return caller
    return None


class McpSurfaceMiddleware:
    """Envoie `/mcp` à la surface MCP (derrière la garde), les points d'entrée
    OAuth à leur application, et le reste à l'IHM."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope["path"]
            if path in _MCP_PATHS:
                await self._mcp(scope, receive, send)
                return
            # Points d'entrée OAuth (découverte, autorisation, jetons) : publics,
            # servis eux aussi hors de la pile de l'IHM.
            oauth = getattr(scope["app"].state, "oauth_endpoints", None)
            if oauth is not None and path in oauth.paths:
                await oauth(scope, receive, send)
                return
        await self.app(scope, receive, send)

    async def _mcp(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope)
        presented, token = _bearer(scope)
        try:
            caller = await _resolve(request, token) if token is not None else None
        except TokenRefusedError as refused:
            log.info("mcp_unauthenticated", reason=refused.reason)
            await _unauthorized(request, True, refused.reason)(scope, receive, send)
            return
        if caller is None:
            log.info("mcp_unauthenticated", reason="invalid" if presented else "missing")
            await _unauthorized(request, presented)(scope, receive, send)
            return
        reset = current_caller.set(caller)
        try:
            await request.app.state.mcp_session_manager.handle_request(scope, receive, send)
        finally:
            current_caller.reset(reset)
