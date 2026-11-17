from __future__ import annotations

import re

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ossie_studio.crypto import CryptoService, DecryptionFailedError, SecretValue
from ossie_studio.models import Secret

log = structlog.get_logger(__name__)

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class SecretSlugTakenError(Exception):
    """Un secret porte déjà ce slug."""


class SecretNotFoundError(Exception):
    def __init__(self, slug: str) -> None:
        super().__init__(f"Aucun secret ne porte le slug « {slug} »")
        self.slug = slug


async def db_list_secrets(session: AsyncSession) -> list[Secret]:
    result = await session.execute(select(Secret).order_by(Secret.slug))
    return list(result.scalars())


async def db_get_secret(session: AsyncSession, slug: str) -> Secret | None:
    result = await session.execute(select(Secret).where(Secret.slug == slug))
    return result.scalar_one_or_none()


async def db_create_secret(
    session: AsyncSession, slug: str, label: str, category: str, value_encrypted: bytes
) -> Secret:
    """Insère le secret ; l'unicité du slug est tranchée par la base."""
    secret = Secret(slug=slug, label=label, category=category, value_encrypted=value_encrypted)
    session.add(secret)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise SecretSlugTakenError from exc
    return secret


async def db_replace_secret_value(
    session: AsyncSession, slug: str, value_encrypted: bytes
) -> Secret:
    secret = await db_get_secret(session, slug)
    if secret is None:
        raise SecretNotFoundError(slug)
    secret.value_encrypted = value_encrypted
    await session.commit()
    return secret


async def db_update_secret_label(session: AsyncSession, slug: str, label: str) -> Secret:
    secret = await db_get_secret(session, slug)
    if secret is None:
        raise SecretNotFoundError(slug)
    secret.label = label
    await session.commit()
    return secret


async def db_delete_secret(session: AsyncSession, slug: str) -> bool:
    """Supprime le secret ; False si aucun secret ne porte ce slug."""
    secret = await db_get_secret(session, slug)
    if secret is None:
        return False
    await session.delete(secret)
    await session.commit()
    return True


async def resolve_secret_value(
    session: AsyncSession, crypto: CryptoService, slug: str
) -> SecretValue:
    """Rend la valeur d'un secret, sous le type opaque, pour usage serveur.

    Slug inconnu → erreur explicite, jamais une valeur vide. Le déchiffrement
    qui échoue est journalisé sans la moindre donnée chiffrée ni claire.
    """
    secret = await db_get_secret(session, slug)
    if secret is None:
        raise SecretNotFoundError(slug)
    try:
        return crypto.decrypt(secret.value_encrypted)
    except DecryptionFailedError:
        log.warning("secret_decrypt_failed", slug=slug)
        raise
