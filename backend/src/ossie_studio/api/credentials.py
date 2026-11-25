"""Mes identifiants personnels par forge (`/api/me/credentials`).

Toujours ceux de la personne connectée : aucun paramètre ne désigne un autre
compte, et l'identifiant d'un autre répond 404 (ni lu, ni modifié, ni testé)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

import httpx
import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import CurrentUser
from ossie_studio.connections.service import db_get_connection
from ossie_studio.credentials.service import (
    STATUS_REFUSED,
    STATUS_VALID,
    CredentialNotFoundError,
    PersonalCredential,
    db_get_own_credential,
    db_record_test,
    db_set_credential_value,
    ensure_personal_credential,
    reveal_own_token,
)
from ossie_studio.crypto import CryptoService
from ossie_studio.deps import ConnDep, get_crypto, get_http_client_factory
from ossie_studio.errors import ApiError
from ossie_studio.forges.base import ForgeError, repo_path
from ossie_studio.forges.registry import get_strategy
from ossie_studio.models import Connection, GitSpace, User
from ossie_studio.profiles.service import load_capabilities
from ossie_studio.spaces.service import db_get_space

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/me/credentials")

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]
HttpFactoryDep = Annotated[Callable[[], httpx.AsyncClient], Depends(get_http_client_factory)]


class ValueBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = ""


class TestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    space_id: uuid.UUID


def _payload(cred: PersonalCredential, connection: Connection) -> dict[str, object]:
    # Jamais la valeur : seulement l'état et de quoi l'afficher.
    return {
        "id": str(cred.id),
        "connection_id": str(cred.connection_id),
        "forge": connection.forge,
        "url": connection.url,
        "status": cred.status,
        "forge_login": cred.forge_login,
        "error_code": cred.error_code,
        "checked_at": cred.checked_at.isoformat() if cred.checked_at else None,
    }


async def _readable_space(conn: ConnDep, user: User, space_id: uuid.UUID) -> GitSpace:
    space = await db_get_space(conn, space_id)
    if space is None:
        raise ApiError("space_not_found", status_code=404)
    if "spaces:read" not in await load_capabilities(conn, user, space_id):
        raise ApiError("forbidden", status_code=403)
    return space


async def _connection(conn: ConnDep, connection_id: uuid.UUID) -> Connection:
    connection = await db_get_connection(conn, connection_id)
    if connection is None:
        raise ApiError("credential_not_found", status_code=404)
    return connection


@router.get("")
async def my_credential_for_space(
    conn: ConnDep, user: CurrentUser, space_id: uuid.UUID
) -> dict[str, object]:
    """Mon identifiant pour la connexion de forge de cet espace, créé « à saisir »
    au premier besoin."""
    space = await _readable_space(conn, user, space_id)
    cred = await ensure_personal_credential(conn, user.id, space.connection_id)
    return _payload(cred, await _connection(conn, space.connection_id))


@router.put("/{credential_id}")
async def set_my_credential(
    conn: ConnDep, crypto: CryptoDep, user: CurrentUser, credential_id: uuid.UUID, body: ValueBody
) -> dict[str, object]:
    if not body.value.strip():
        raise ApiError("invalid_request", status_code=400, fields=["value_required"])
    try:
        cred = await db_set_credential_value(
            conn, crypto, user.id, credential_id, body.value.strip()
        )
    except CredentialNotFoundError:
        raise ApiError("credential_not_found", status_code=404) from None
    log.info("personal_credential_set", credential_id=str(cred.id))
    return _payload(cred, await _connection(conn, cred.connection_id))


@router.post("/{credential_id}/test")
async def test_my_credential(
    conn: ConnDep,
    crypto: CryptoDep,
    http_client_factory: HttpFactoryDep,
    user: CurrentUser,
    credential_id: uuid.UUID,
    body: TestBody,
) -> dict[str, object]:
    """Éprouve le jeton : identité du compte et droit d'écriture sur le dépôt de
    l'espace. Forge injoignable → erreur nommée, état inchangé."""
    try:
        cred = await db_get_own_credential(conn, user.id, credential_id)
    except CredentialNotFoundError:
        raise ApiError("credential_not_found", status_code=404) from None
    space = await _readable_space(conn, user, body.space_id)
    if space.connection_id != cred.connection_id:
        raise ApiError("invalid_request", status_code=400, fields=["space_connection_mismatch"])
    token = await reveal_own_token(conn, crypto, user.id, credential_id)
    if token is None:
        raise ApiError("credential_value_missing", status_code=400)
    connection = await _connection(conn, cred.connection_id)
    strategy = get_strategy(connection.forge)
    async with http_client_factory() as client:
        try:
            check = await strategy.verify_write(
                client, connection.url, repo_path(connection.url, space.repo_url), token.reveal()
            )
        except ForgeError as exc:
            if exc.code in ("connection_token_invalid", "connection_token_scope_insufficient"):
                cred = await db_record_test(
                    conn, cred.id, status=STATUS_REFUSED, forge_login=None, error_code=exc.code
                )
                return _payload(cred, connection)
            raise ApiError(exc.code, status_code=400) from None
    if check.can_write is False:
        cred = await db_record_test(
            conn,
            cred.id,
            status=STATUS_REFUSED,
            forge_login=check.login,
            error_code="credential_no_write",
        )
    else:
        # can_write None (forge où ce n'est pas vérifiable) : valide, signalé.
        cred = await db_record_test(
            conn,
            cred.id,
            status=STATUS_VALID,
            forge_login=check.login,
            error_code=None if check.can_write else "credential_write_unverifiable",
        )
    log.info("personal_credential_tested", credential_id=str(cred.id), status=cred.status)
    return _payload(cred, connection)
