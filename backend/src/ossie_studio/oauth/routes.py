"""Points d'entrée OAuth, construits par le SDK MCP pour une adresse publique.

L'émetteur (RFC 8414) et l'audience des jetons dépendent de l'adresse publique
d'Ossie-studio : configurée (`PUBLIC_BASE_URL`), ou à défaut celle de la
requête. Le jeu de routes est donc construit par adresse, puis gardé.

L'émetteur doit être en HTTPS (sauf `localhost`, pour les essais) : sans
adresse HTTPS, ces points d'entrée répondent 404 et le journal le signale.
"""

from __future__ import annotations

import asyncpg
import structlog
from mcp.server.auth.routes import create_auth_routes, create_protected_resource_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from pydantic import AnyHttpUrl, ConfigDict, TypeAdapter
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, Router
from starlette.types import Receive, Scope, Send

from ossie_studio.crypto import CryptoService
from ossie_studio.mcp_surface.guard import MCP_PATH, public_base
from ossie_studio.oauth.provider import OssieOAuthProvider
from ossie_studio.oauth.service import SCOPE

log = structlog.get_logger(__name__)

OAUTH_PATHS = frozenset(
    {
        "/.well-known/oauth-authorization-server",
        f"/.well-known/oauth-protected-resource{MCP_PATH}",
        # Certains clients cherchent le document sans le chemin de la ressource.
        "/.well-known/oauth-protected-resource",
        "/authorize",
        "/token",
        "/register",
        "/revoke",
    }
)


# Adresses gardées telles quelles (sans barre finale ajoutée) : l'émetteur se
# compare caractère pour caractère (RFC 8414 / 9207).
_URL = TypeAdapter(AnyHttpUrl, config=ConfigDict(url_preserve_empty_path=True))


def _routes(provider: OssieOAuthProvider) -> list[Route]:
    issuer = _URL.validate_python(provider.base_url)
    routes = create_auth_routes(
        provider,
        issuer_url=issuer,
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]
        ),
        revocation_options=RevocationOptions(enabled=True),
    )
    metadata = create_protected_resource_routes(
        resource_url=_URL.validate_python(provider.resource),
        authorization_servers=[issuer],
        scopes_supported=[SCOPE],
        resource_name="Ossie-studio",
    )
    # Même document, aussi à l'adresse sans chemin de ressource.
    root_alias = [
        Route("/.well-known/oauth-protected-resource", r.endpoint, methods=r.methods)
        for r in metadata
    ]
    return [*routes, *metadata, *root_alias]


async def _not_available(scope: Scope, receive: Receive, send: Send) -> None:
    await JSONResponse({"error": "not_found"}, status_code=404)(scope, receive, send)


class OAuthEndpoints:
    """Application ASGI des points d'entrée OAuth, aiguillée par la garde MCP."""

    paths = OAUTH_PATHS

    def __init__(self, pool: asyncpg.Pool, crypto: CryptoService) -> None:
        self._pool = pool
        self._crypto = crypto
        self._routers: dict[str, Router] = {}

    def _router_for(self, base: str) -> Router | None:
        router = self._routers.get(base)
        if router is None:
            provider = OssieOAuthProvider(self._pool, self._crypto, base)
            try:
                router = Router(routes=_routes(provider))
            except ValueError:
                log.warning("oauth_issuer_invalid", base=base)
                return None
            self._routers[base] = router
        return router

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        router = self._router_for(public_base(Request(scope)))
        if router is None:
            await _not_available(scope, receive, send)
            return
        await router(scope, receive, send)
