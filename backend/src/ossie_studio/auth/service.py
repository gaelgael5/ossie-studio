from __future__ import annotations

import asyncio
import uuid

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ossie_studio.models import ROLE_ADMIN, User
from ossie_studio.security import (
    hash_password,
    verify_password,
    waste_time_like_a_verification,
)

log = structlog.get_logger(__name__)


class BootstrapConflictError(Exception):
    """Un administrateur issu de l'écran de bootstrap existe déjà."""


async def db_count_users(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(User))
    return int(result.scalar_one())


async def db_get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def db_get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)


async def db_create_bootstrap_admin(session: AsyncSession, email: str, password_hash: str) -> User:
    """Insère l'administrateur de bootstrap ; la base tranche l'unicité.

    Deux créations concurrentes passent toutes les deux une lecture préalable :
    c'est l'index unique partiel sur `is_bootstrap` qui refuse la seconde.
    """
    user = User(email=email, password_hash=password_hash, role=ROLE_ADMIN, is_bootstrap=True)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise BootstrapConflictError from exc
    return user


async def resolve_bootstrap_admin(session: AsyncSession, email: str, password: str) -> User:
    """Crée le premier administrateur et rend le compte prêt à l'emploi."""
    # argon2 est un travail CPU volontairement coûteux : hors de la boucle async.
    password_hash = await asyncio.to_thread(hash_password, password)
    user = await db_create_bootstrap_admin(session, email, password_hash)
    log.info("bootstrap_admin_created", user_id=str(user.id))
    return user


async def resolve_login(session: AsyncSession, email: str, password: str) -> User | None:
    """Authentifie en local ; None si email inconnu ou mot de passe faux."""
    user = await db_get_user_by_email(session, email)
    if user is None:
        await asyncio.to_thread(waste_time_like_a_verification)
        return None
    if not await asyncio.to_thread(verify_password, user.password_hash, password):
        return None
    return user
