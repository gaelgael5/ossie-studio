"""Données de l'OAuth : clients, demandes en attente, codes, connexions, jetons.

Codes et jetons sont opaques, à haute entropie ; seule leur empreinte SHA-256
est gardée (Ossie-studio émet et consomme ses propres jetons : rien ne justifie
des jetons signés, et une révocation vaut immédiatement).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import asyncpg
import structlog

from ossie_studio.mcp_surface.caller import McpCaller, TokenRefusedError

log = structlog.get_logger(__name__)

ACCESS_PREFIX = "osa_"
REFRESH_PREFIX = "osr_"
# Les droits ne passent pas par les scopes (le profil agent les porte) : un
# scope unique, annoncé pour que les clients le demandent sans erreur.
SCOPE = "mcp"

# Une connexion révoquée, et les jetons expirés ou révoqués, restent un jour
# (visibles, journalisés), puis disparaissent de la base.
RETENTION = timedelta(hours=24)

REQUEST_TTL = timedelta(minutes=10)
CODE_TTL = timedelta(minutes=5)
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def same_resource(a: str, b: str) -> bool:
    """Deux adresses de ressource identiques, à la barre finale près."""
    return a.rstrip("/") == b.rstrip("/")


def _now() -> datetime:
    return datetime.now(UTC)


# --- Demandes d'autorisation en attente de consentement ------------------------


@dataclass(frozen=True)
class AuthorizationRequest:
    id: str
    client_id: str
    client_name: str
    redirect_uri: str
    redirect_uri_provided_explicitly: bool
    code_challenge: str
    state: str | None
    scopes: list[str]
    resource: str
    user_id: uuid.UUID | None


async def db_create_request(
    conn: asyncpg.Connection,
    *,
    client_id: str,
    redirect_uri: str,
    redirect_uri_provided_explicitly: bool,
    code_challenge: str,
    state: str | None,
    scopes: list[str],
    resource: str,
) -> str:
    request_id = secrets.token_urlsafe(32)
    await conn.execute(
        "INSERT INTO oauth_authorization_requests (id, client_id, redirect_uri, "
        "redirect_uri_provided_explicitly, code_challenge, state, scopes, resource, expires_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
        request_id,
        client_id,
        redirect_uri,
        redirect_uri_provided_explicitly,
        code_challenge,
        state,
        scopes,
        resource,
        _now() + REQUEST_TTL,
    )
    return request_id


async def db_claim_request(
    conn: asyncpg.Connection, request_id: str, user_id: uuid.UUID
) -> AuthorizationRequest | None:
    """La demande, liée à la première personne connectée qui l'ouvre. Expirée,
    inconnue ou déjà prise par une autre personne → None."""
    await conn.execute(
        "UPDATE oauth_authorization_requests SET user_id = $2 WHERE id = $1 AND user_id IS NULL",
        request_id,
        user_id,
    )
    row = await conn.fetchrow(
        "SELECT r.*, c.client_name FROM oauth_authorization_requests r "
        "JOIN oauth_clients c ON c.client_id = r.client_id "
        "WHERE r.id = $1 AND r.user_id = $2 AND r.expires_at > now()",
        request_id,
        user_id,
    )
    if row is None:
        return None
    return AuthorizationRequest(
        id=row["id"],
        client_id=row["client_id"],
        client_name=row["client_name"],
        redirect_uri=row["redirect_uri"],
        redirect_uri_provided_explicitly=row["redirect_uri_provided_explicitly"],
        code_challenge=row["code_challenge"],
        state=row["state"],
        scopes=list(row["scopes"]),
        resource=row["resource"],
        user_id=row["user_id"],
    )


async def db_delete_request(conn: asyncpg.Connection, request_id: str) -> None:
    await conn.execute("DELETE FROM oauth_authorization_requests WHERE id = $1", request_id)


# --- Codes d'autorisation ------------------------------------------------------------


async def db_create_code(
    conn: asyncpg.Connection, request: AuthorizationRequest, agent_profile_id: uuid.UUID
) -> str:
    """Code d'autorisation pour la demande consentie (rendu une fois, au client)."""
    assert request.user_id is not None
    code = secrets.token_urlsafe(32)
    await conn.execute(
        "INSERT INTO oauth_codes (code_hash, client_id, user_id, agent_profile_id, redirect_uri, "
        "redirect_uri_provided_explicitly, code_challenge, scopes, resource, expires_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
        digest(code),
        request.client_id,
        request.user_id,
        agent_profile_id,
        request.redirect_uri,
        request.redirect_uri_provided_explicitly,
        request.code_challenge,
        request.scopes,
        request.resource,
        _now() + CODE_TTL,
    )
    return code


# --- Connexions et jetons ------------------------------------------------------------------


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int


async def db_upsert_connection(
    conn: asyncpg.Connection, user_id: uuid.UUID, client_id: str, agent_profile_id: uuid.UUID
) -> uuid.UUID:
    """Connexion active de la personne pour ce client, créée ou mise au profil choisi."""
    connection_id: uuid.UUID = await conn.fetchval(
        "INSERT INTO oauth_connections (user_id, client_id, agent_profile_id) "
        "VALUES ($1, $2, $3) "
        "ON CONFLICT (user_id, client_id) WHERE revoked_at IS NULL "
        "DO UPDATE SET agent_profile_id = EXCLUDED.agent_profile_id, updated_at = now() "
        "RETURNING id",
        user_id,
        client_id,
        agent_profile_id,
    )
    return connection_id


async def db_issue_tokens(
    conn: asyncpg.Connection, connection_id: uuid.UUID, scopes: list[str], resource: str
) -> IssuedTokens:
    access = ACCESS_PREFIX + secrets.token_urlsafe(32)
    refresh = REFRESH_PREFIX + secrets.token_urlsafe(32)
    now = _now()
    await conn.executemany(
        "INSERT INTO oauth_tokens (token_hash, kind, connection_id, scopes, resource, expires_at) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        [
            (digest(access), "access", connection_id, scopes, resource, now + ACCESS_TTL),
            (digest(refresh), "refresh", connection_id, scopes, resource, now + REFRESH_TTL),
        ],
    )
    return IssuedTokens(access, refresh, int(ACCESS_TTL.total_seconds()))


async def db_revoke_connection(
    conn: asyncpg.Connection, connection_id: uuid.UUID, revoked_by: uuid.UUID | None
) -> bool:
    """Révoque la connexion et ses jetons ; False si elle l'était déjà."""
    async with conn.transaction():
        result: str = await conn.execute(
            "UPDATE oauth_connections SET revoked_at = now(), revoked_by = $2 "
            "WHERE id = $1 AND revoked_at IS NULL",
            connection_id,
            revoked_by,
        )
        await conn.execute(
            "UPDATE oauth_tokens SET revoked_at = now() "
            "WHERE connection_id = $1 AND revoked_at IS NULL",
            connection_id,
        )
    return result != "UPDATE 0"


async def db_purge_oauth(conn: asyncpg.Connection) -> dict[str, int]:
    """Ménage de l'OAuth ; rend le nombre de lignes supprimées par sorte.

    - connexions révoquées depuis plus de 24 h (leurs jetons partent avec) ;
    - demandes d'autorisation expirées (jamais consenties) ;
    - codes d'autorisation expirés (utilisés ou non) ;
    - jetons expirés ou révoqués depuis plus de 24 h.
    La trace des consentements et révocations reste dans le journal."""

    async def delete(query: str, *args: object) -> int:
        return len(await conn.fetch(query, *args))

    return {
        "connections": await delete(
            "DELETE FROM oauth_connections WHERE revoked_at IS NOT NULL "
            "AND revoked_at < now() - $1::interval RETURNING id",
            RETENTION,
        ),
        "requests": await delete(
            "DELETE FROM oauth_authorization_requests WHERE expires_at < now() RETURNING id"
        ),
        "codes": await delete(
            "DELETE FROM oauth_codes WHERE expires_at < now() RETURNING code_hash"
        ),
        "tokens": await delete(
            "DELETE FROM oauth_tokens "
            "WHERE coalesce(revoked_at, expires_at) < now() - $1::interval RETURNING token_hash",
            RETENTION,
        ),
    }


async def resolve_oauth_token(
    conn: asyncpg.Connection, token: str, resource: str
) -> McpCaller | None:
    """Résolveur de la surface MCP : un jeton d'accès valide → l'agent qui agit
    pour la personne, avec le profil agent de la connexion.

    Inconnu, révoqué, ou connexion révoquée : None (indistinguables). Expiré ou
    émis pour une autre audience : refus motivé (le client sait quoi faire)."""
    if not token.startswith(ACCESS_PREFIX):
        return None
    row = await conn.fetchrow(
        "SELECT c.id, c.user_id, c.client_id, c.agent_profile_id, t.resource, t.expires_at "
        "FROM oauth_tokens t JOIN oauth_connections c ON c.id = t.connection_id "
        "WHERE t.token_hash = $1 AND t.kind = 'access' "
        "AND t.revoked_at IS NULL AND c.revoked_at IS NULL",
        digest(token),
    )
    if row is None:
        return None
    if row["expires_at"] <= _now():
        raise TokenRefusedError("token_expired")
    if not same_resource(row["resource"], resource):
        raise TokenRefusedError("token_audience_mismatch")
    await conn.execute(
        "UPDATE oauth_connections SET last_used_at = now() WHERE id = $1 "
        "AND (last_used_at IS NULL OR last_used_at < now() - interval '1 minute')",
        row["id"],
    )
    return McpCaller(
        kind="oauth",
        ref=str(row["id"]),
        agent_profile_id=row["agent_profile_id"],
        user_id=row["user_id"],
        client_id=row["client_id"],
    )
