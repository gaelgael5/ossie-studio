from __future__ import annotations

import uuid

import asyncpg
import structlog

from ossie_studio.models import ROLE_USER, OidcConnector
from ossie_studio.oidc.claims import VerifiedClaims
from ossie_studio.oidc.linking import LinkDecision, LinkOutcome, decide_link
from ossie_studio.users.service import db_touch_login, search_key

log = structlog.get_logger(__name__)


class IssuerAlreadyDeclaredError(Exception):
    """Un connecteur existe déjà sur cet issuer."""


class ConnectorSlugTakenError(Exception):
    """Le slug désigne déjà un autre connecteur."""


async def db_list_connectors(conn: asyncpg.Connection) -> list[OidcConnector]:
    rows = await conn.fetch("SELECT * FROM oidc_connectors ORDER BY label")
    return [OidcConnector.from_row(row) for row in rows]


async def db_get_connector(
    conn: asyncpg.Connection, connector_id: uuid.UUID
) -> OidcConnector | None:
    row = await conn.fetchrow("SELECT * FROM oidc_connectors WHERE id = $1", connector_id)
    return OidcConnector.from_row(row) if row is not None else None


async def db_create_connector(
    conn: asyncpg.Connection,
    label: str,
    issuer: str,
    client_id: str,
    client_secret_slug: str,
    scopes: str,
    slug: str | None = None,
) -> OidcConnector:
    try:
        row = await conn.fetchrow(
            "INSERT INTO oidc_connectors (label, issuer, client_id, client_secret_slug, "
            "scopes, slug) VALUES ($1, $2, $3, $4, $5, coalesce($6, gen_random_uuid()::text)) "
            "RETURNING *",
            label,
            issuer,
            client_id,
            client_secret_slug,
            scopes,
            slug,
        )
    except asyncpg.UniqueViolationError as exc:
        if exc.constraint_name == "uq_oidc_connector_slug":
            raise ConnectorSlugTakenError from exc
        raise IssuerAlreadyDeclaredError from exc
    return OidcConnector.from_row(row)


async def db_set_login_routing(
    conn: asyncpg.Connection, connector_id: uuid.UUID, email_domains: list[str], login_prompt: str
) -> None:
    await conn.execute(
        "UPDATE oidc_connectors SET email_domains = $2, login_prompt = $3 WHERE id = $1",
        connector_id,
        email_domains,
        login_prompt,
    )


async def db_connectors_for_domain(conn: asyncpg.Connection, domain: str) -> list[OidcConnector]:
    """Connecteurs qui déclarent ce domaine d'email. Plusieurs sont possibles
    (deux annuaires pour une même société) : l'écran de connexion les propose
    tous plutôt que d'en choisir un arbitrairement."""
    if not domain:
        return []
    rows = await conn.fetch(
        "SELECT * FROM oidc_connectors WHERE $1 = ANY(email_domains) ORDER BY label", domain
    )
    return [OidcConnector.from_row(row) for row in rows]


async def db_delete_connector(conn: asyncpg.Connection, connector_id: uuid.UUID) -> None:
    await conn.execute("DELETE FROM oidc_connectors WHERE id = $1", connector_id)


async def db_count_identities_for_issuer(conn: asyncpg.Connection, issuer: str) -> int:
    value = await conn.fetchval("SELECT count(*) FROM user_identities WHERE issuer = $1", issuer)
    return int(value)


async def db_get_identity_user_id(
    conn: asyncpg.Connection, issuer: str, subject: str
) -> uuid.UUID | None:
    user_id: uuid.UUID | None = await conn.fetchval(
        "SELECT user_id FROM user_identities WHERE issuer = $1 AND subject = $2",
        issuer,
        subject,
    )
    return user_id


async def db_find_user_ids_by_email(conn: asyncpg.Connection, email: str) -> list[uuid.UUID]:
    rows = await conn.fetch("SELECT id FROM users WHERE email = $1", email)
    return [row["id"] for row in rows]


async def resolve_federated_login(
    conn: asyncpg.Connection, issuer: str, claims: VerifiedClaims
) -> LinkDecision:
    """Rattache une identité fédérée à un compte et applique l'effet de bord.

    Pour LINKED_EXISTING et CREATED_NEW, `user_id` pointe le compte connecté,
    le couple (issuer, sub) étant désormais épinglé.
    """
    pinned_user_id = await db_get_identity_user_id(conn, issuer, claims.subject)
    matched = (
        await db_find_user_ids_by_email(conn, claims.email)
        if claims.email and claims.email_verified
        else []
    )
    decision = decide_link(pinned_user_id, matched, claims)

    if decision.outcome is LinkOutcome.LINKED_EXISTING and pinned_user_id is None:
        assert decision.user_id is not None
        await _db_pin_identity(conn, decision.user_id, issuer, claims.subject)
    elif decision.outcome is LinkOutcome.CREATED_NEW:
        new_id = await _db_provision_user(conn, issuer, claims)
        await db_touch_login(conn, new_id)
        return LinkDecision(LinkOutcome.CREATED_NEW, new_id)
    if decision.outcome is LinkOutcome.LINKED_EXISTING and decision.user_id is not None:
        await db_touch_login(conn, decision.user_id)
        await _db_refresh_display_name(conn, decision.user_id, claims)
    return decision


async def _db_refresh_display_name(
    conn: asyncpg.Connection, user_id: uuid.UUID, claims: VerifiedClaims
) -> None:
    """Le nom affiché suit l'annuaire à chaque connexion : c'est lui qui signe
    les commits publiés. Un claim absent ne l'efface pas."""
    name = (claims.name or "").strip()
    if not name:
        return
    await conn.execute(
        "UPDATE users SET display_name = $2, search_key = $3 "
        "WHERE id = $1 AND display_name IS DISTINCT FROM $2",
        user_id,
        name,
        search_key(name, claims.email or ""),
    )


async def _db_pin_identity(
    conn: asyncpg.Connection, user_id: uuid.UUID, issuer: str, subject: str
) -> None:
    await conn.execute(
        "INSERT INTO user_identities (user_id, issuer, subject) VALUES ($1, $2, $3)",
        user_id,
        issuer,
        subject,
    )
    log.info("oidc_identity_pinned", user_id=str(user_id), issuer=issuer)


async def _db_provision_user(
    conn: asyncpg.Connection, issuer: str, claims: VerifiedClaims
) -> uuid.UUID:
    assert claims.email is not None
    async with conn.transaction():
        # Un compte créé à la première connexion reçoit le profil par défaut
        # (« Sans accès » à l'installation). Le rattachement d'un compte existant
        # garde son profil (il ne passe pas par ici). Le nom d'affichage vient
        # des claims OIDC (s'il y en a un).
        display_name = (claims.name or "").strip()
        user_id = await conn.fetchval(
            "INSERT INTO users (email, password_hash, role, profile_id, display_name, search_key) "
            "VALUES ($1, NULL, $2, (SELECT id FROM profiles WHERE is_default), $3, $4) "
            "RETURNING id",
            claims.email,
            ROLE_USER,
            display_name,
            search_key(display_name, claims.email),
        )
        await conn.execute(
            "INSERT INTO user_identities (user_id, issuer, subject) VALUES ($1, $2, $3)",
            user_id,
            issuer,
            claims.subject,
        )
    log.info("oidc_user_provisioned", user_id=str(user_id), issuer=issuer)
    assert isinstance(user_id, uuid.UUID)
    return user_id
