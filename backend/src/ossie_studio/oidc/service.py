from __future__ import annotations

import uuid

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ossie_studio.models import ROLE_USER, OidcConnector, User, UserIdentity
from ossie_studio.oidc.claims import VerifiedClaims
from ossie_studio.oidc.linking import LinkDecision, LinkOutcome, decide_link

log = structlog.get_logger(__name__)


class IssuerAlreadyDeclaredError(Exception):
    """Un connecteur existe déjà sur cet issuer."""


async def db_list_connectors(session: AsyncSession) -> list[OidcConnector]:
    result = await session.execute(select(OidcConnector).order_by(OidcConnector.label))
    return list(result.scalars())


async def db_get_connector(session: AsyncSession, connector_id: uuid.UUID) -> OidcConnector | None:
    return await session.get(OidcConnector, connector_id)


async def db_create_connector(
    session: AsyncSession,
    label: str,
    issuer: str,
    client_id: str,
    client_secret_encrypted: bytes,
    scopes: str,
) -> OidcConnector:
    connector = OidcConnector(
        label=label,
        issuer=issuer,
        client_id=client_id,
        client_secret_encrypted=client_secret_encrypted,
        scopes=scopes,
    )
    session.add(connector)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise IssuerAlreadyDeclaredError from exc
    return connector


async def db_delete_connector(session: AsyncSession, connector: OidcConnector) -> None:
    await session.delete(connector)
    await session.commit()


async def db_count_identities_for_issuer(session: AsyncSession, issuer: str) -> int:
    result = await session.execute(
        select(func.count()).select_from(UserIdentity).where(UserIdentity.issuer == issuer)
    )
    return int(result.scalar_one())


async def db_get_identity(session: AsyncSession, issuer: str, subject: str) -> UserIdentity | None:
    result = await session.execute(
        select(UserIdentity).where(UserIdentity.issuer == issuer, UserIdentity.subject == subject)
    )
    return result.scalar_one_or_none()


async def db_find_user_ids_by_email(session: AsyncSession, email: str) -> list[uuid.UUID]:
    result = await session.execute(select(User.id).where(User.email == email))
    return list(result.scalars())


async def resolve_federated_login(
    session: AsyncSession, issuer: str, claims: VerifiedClaims
) -> LinkDecision:
    """Rattache une identité fédérée à un compte et applique l'effet de bord.

    Retourne la décision ; pour LINKED_EXISTING et CREATED_NEW, `user_id` pointe
    le compte connecté, le couple (issuer, sub) étant désormais épinglé.
    """
    identity = await db_get_identity(session, issuer, claims.subject)
    pinned_user_id = identity.user_id if identity is not None else None
    matched = (
        await db_find_user_ids_by_email(session, claims.email)
        if claims.email and claims.email_verified
        else []
    )
    decision = decide_link(pinned_user_id, matched, claims)

    if decision.outcome is LinkOutcome.LINKED_EXISTING and identity is None:
        # Pont email réussi : on épingle le couple (issuer, sub) sur le compte.
        assert decision.user_id is not None
        await _db_pin_identity(session, decision.user_id, issuer, claims.subject)
    elif decision.outcome is LinkOutcome.CREATED_NEW:
        new_id = await _db_provision_user(session, issuer, claims)
        return LinkDecision(LinkOutcome.CREATED_NEW, new_id)
    return decision


async def _db_pin_identity(
    session: AsyncSession, user_id: uuid.UUID, issuer: str, subject: str
) -> None:
    session.add(UserIdentity(user_id=user_id, issuer=issuer, subject=subject))
    await session.commit()
    log.info("oidc_identity_pinned", user_id=str(user_id), issuer=issuer)


async def _db_provision_user(
    session: AsyncSession, issuer: str, claims: VerifiedClaims
) -> uuid.UUID:
    assert claims.email is not None
    user = User(email=claims.email, password_hash=None, role=ROLE_USER)
    session.add(user)
    await session.flush()
    session.add(UserIdentity(user_id=user.id, issuer=issuer, subject=claims.subject))
    await session.commit()
    log.info("oidc_user_provisioned", user_id=str(user.id), issuer=issuer)
    return user.id
