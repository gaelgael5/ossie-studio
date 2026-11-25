"""Fournisseur du serveur d'autorisation, branché sur la boîte à outils OAuth du
SDK MCP (`create_auth_routes`) : le SDK porte le protocole (découverte RFC 8414,
enregistrement dynamique RFC 7591, PKCE S256, vérification du `redirect_uri`,
échange et rafraîchissement, révocation RFC 7009) ; ce fournisseur porte les
données et les règles d'Ossie-studio.

Le consentement n'est pas ici : `/authorize` renvoie la personne vers l'écran de
consentement de l'IHM, qui émet le code (voir `api/oauth.py`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import asyncpg
import structlog
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    IdentityAssertionParams,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from ossie_studio.crypto import CryptoService, SecretValue
from ossie_studio.oauth.service import (
    ACCESS_PREFIX,
    REFRESH_PREFIX,
    IssuedTokens,
    db_create_request,
    db_issue_tokens,
    db_revoke_connection,
    db_upsert_connection,
    digest,
    same_resource,
)

log = structlog.get_logger(__name__)

CONSENT_PATH = "/oauth/consent"


class OssieAuthorizationCode(AuthorizationCode):
    user_id: uuid.UUID
    agent_profile_id: uuid.UUID


class OssieRefreshToken(RefreshToken):
    connection_id: uuid.UUID


class OssieAccessToken(AccessToken):
    connection_id: uuid.UUID


def _epoch(value: datetime) -> int:
    return int(value.timestamp())


def _oauth_token(issued: IssuedTokens, scopes: list[str]) -> OAuthToken:
    return OAuthToken(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        refresh_token=issued.refresh_token,
        scope=" ".join(scopes) or None,
    )


class OssieOAuthProvider:
    """Un fournisseur par adresse publique (émetteur et audience en dépendent)."""

    def __init__(self, pool: asyncpg.Pool, crypto: CryptoService, base_url: str) -> None:
        self._pool = pool
        self._crypto = crypto
        self.base_url = base_url.rstrip("/")
        self.resource = f"{self.base_url}/mcp"

    # --- Clients (enregistrement dynamique) ----------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT metadata, client_secret_encrypted FROM oauth_clients WHERE client_id = $1",
                client_id,
            )
        if row is None:
            return None
        data = dict(row["metadata"])
        if row["client_secret_encrypted"] is not None:
            data["client_secret"] = self._crypto.decrypt(row["client_secret_encrypted"]).reveal()
        return OAuthClientInformationFull.model_validate(data)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.redirect_uris:
            raise RegistrationError("invalid_redirect_uri", "redirect_uris is required")
        metadata = client_info.model_dump(mode="json", exclude={"client_secret"}, exclude_none=True)
        secret = client_info.client_secret
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO oauth_clients (client_id, client_name, metadata, "
                "client_secret_encrypted) VALUES ($1, $2, $3, $4)",
                client_info.client_id,
                (client_info.client_name or "")[:200],
                metadata,
                self._crypto.encrypt(SecretValue(secret)) if secret else None,
            )
        log.info(
            "oauth_client_registered",
            client_id=client_info.client_id,
            client_name=client_info.client_name,
        )

    # --- Autorisation : renvoi vers l'écran de consentement ---------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        # Audience : la surface MCP d'Ossie-studio, et elle seule.
        resource = params.resource or self.resource
        if not same_resource(resource, self.resource):
            raise AuthorizeError("invalid_target", "unknown resource")
        async with self._pool.acquire() as conn:
            request_id = await db_create_request(
                conn,
                client_id=client.client_id,
                redirect_uri=str(params.redirect_uri),
                redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                code_challenge=params.code_challenge,
                state=params.state,
                scopes=params.scopes or [],
                resource=self.resource,
            )
        return f"{self.base_url}{CONSENT_PATH}?request={request_id}"

    # --- Code → jetons --------------------------------------------------------------

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> OssieAuthorizationCode | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM oauth_codes WHERE code_hash = $1 AND client_id = $2 "
                "AND used_at IS NULL",
                digest(authorization_code),
                client.client_id,
            )
        if row is None:
            return None
        return OssieAuthorizationCode(
            code=authorization_code,
            scopes=list(row["scopes"]),
            expires_at=row["expires_at"].timestamp(),
            client_id=row["client_id"],
            code_challenge=row["code_challenge"],
            redirect_uri=AnyUrl(row["redirect_uri"]),
            redirect_uri_provided_explicitly=row["redirect_uri_provided_explicitly"],
            resource=row["resource"],
            subject=str(row["user_id"]),
            user_id=row["user_id"],
            agent_profile_id=row["agent_profile_id"],
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: OssieAuthorizationCode
    ) -> OAuthToken:
        resource = authorization_code.resource or self.resource
        async with self._pool.acquire() as conn, conn.transaction():
            # Usage unique, même sous deux échanges concurrents.
            used = await conn.fetchval(
                "UPDATE oauth_codes SET used_at = now() WHERE code_hash = $1 AND used_at IS NULL "
                "RETURNING 1",
                digest(authorization_code.code),
            )
            if used is None:
                raise TokenError("invalid_grant", "authorization code already used")
            connection_id = await db_upsert_connection(
                conn,
                authorization_code.user_id,
                client.client_id,
                authorization_code.agent_profile_id,
            )
            issued = await db_issue_tokens(conn, connection_id, authorization_code.scopes, resource)
        log.info(
            "oauth_tokens_issued",
            connection_id=str(connection_id),
            client_id=client.client_id,
            grant="authorization_code",
        )
        return _oauth_token(issued, authorization_code.scopes)

    # --- Rafraîchissement -------------------------------------------------------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> OssieRefreshToken | None:
        if not refresh_token.startswith(REFRESH_PREFIX):
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT t.*, c.client_id, c.user_id FROM oauth_tokens t "
                "JOIN oauth_connections c ON c.id = t.connection_id "
                "WHERE t.token_hash = $1 AND t.kind = 'refresh' AND t.revoked_at IS NULL "
                "AND c.revoked_at IS NULL AND c.client_id = $2",
                digest(refresh_token),
                client.client_id,
            )
        if row is None:
            return None
        return OssieRefreshToken(
            token=refresh_token,
            client_id=row["client_id"],
            scopes=list(row["scopes"]),
            expires_at=_epoch(row["expires_at"]),
            resource=row["resource"],
            subject=str(row["user_id"]),
            connection_id=row["connection_id"],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: OssieRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        resource = refresh_token.resource or self.resource
        async with self._pool.acquire() as conn, conn.transaction():
            # Rotation : l'ancien jeton de rafraîchissement ne sert qu'une fois.
            rotated = await conn.fetchval(
                "UPDATE oauth_tokens SET revoked_at = now() WHERE token_hash = $1 "
                "AND revoked_at IS NULL RETURNING 1",
                digest(refresh_token.token),
            )
            if rotated is None:
                raise TokenError("invalid_grant", "refresh token already used")
            issued = await db_issue_tokens(conn, refresh_token.connection_id, scopes, resource)
        log.info(
            "oauth_tokens_issued",
            connection_id=str(refresh_token.connection_id),
            client_id=client.client_id,
            grant="refresh_token",
        )
        return _oauth_token(issued, scopes)

    # --- Jetons d'accès et révocation ---------------------------------------------------

    async def load_access_token(self, token: str) -> OssieAccessToken | None:
        if not token.startswith(ACCESS_PREFIX):
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT t.*, c.client_id, c.user_id FROM oauth_tokens t "
                "JOIN oauth_connections c ON c.id = t.connection_id "
                "WHERE t.token_hash = $1 AND t.kind = 'access' AND t.revoked_at IS NULL "
                "AND c.revoked_at IS NULL AND t.expires_at > now()",
                digest(token),
            )
        if row is None:
            return None
        return OssieAccessToken(
            token=token,
            client_id=row["client_id"],
            scopes=list(row["scopes"]),
            expires_at=_epoch(row["expires_at"]),
            resource=row["resource"],
            subject=str(row["user_id"]),
            connection_id=row["connection_id"],
        )

    async def revoke_token(self, token: OssieAccessToken | OssieRefreshToken) -> None:
        """Jeton d'accès : lui seul. Jeton de rafraîchissement : le client se
        déconnecte, la connexion et tous ses jetons sont révoqués."""
        async with self._pool.acquire() as conn:
            if isinstance(token, OssieRefreshToken):
                await db_revoke_connection(conn, token.connection_id, None)
                log.info(
                    "oauth_connection_revoked",
                    connection_id=str(token.connection_id),
                    by="client",
                )
            else:
                await conn.execute(
                    "UPDATE oauth_tokens SET revoked_at = now() WHERE token_hash = $1",
                    digest(token.token),
                )

    async def exchange_identity_assertion(
        self, client: OAuthClientInformationFull, params: IdentityAssertionParams
    ) -> OAuthToken:
        raise TokenError("unsupported_grant_type", "identity assertion is not supported")
