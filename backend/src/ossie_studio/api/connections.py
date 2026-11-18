from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

import httpx
import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import ConnectionsRead, ConnectionsWrite
from ossie_studio.connections.forms import validate_connection
from ossie_studio.connections.service import (
    ConnectionDuplicateError,
    db_create_connection,
    db_delete_connection,
    db_get_connection,
    db_list_connections,
    db_update_connection_status,
    resolve_connection_token,
)
from ossie_studio.crypto import CryptoService
from ossie_studio.deps import ConnDep, get_crypto, get_http_client_factory
from ossie_studio.errors import ApiError
from ossie_studio.forges.base import ForgeError
from ossie_studio.forges.registry import get_strategy
from ossie_studio.models import Connection
from ossie_studio.secretstore.service import db_get_secret, resolve_secret_value
from ossie_studio.secretstore.usages import db_count_secret_usages, db_delete_secret_if_unused
from ossie_studio.spaces.service import db_spaces_using_connection

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/connections")

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]
HttpFactoryDep = Annotated[Callable[[], httpx.AsyncClient], Depends(get_http_client_factory)]


class ConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = ""
    forge: str = ""
    url: str = ""
    secret_slug: str = ""


def _payload(c: Connection) -> dict[str, object]:
    # Jamais le jeton : seulement de quoi afficher la ligne. Le slug du secret
    # (une référence, pas la valeur) est utile pour piloter la suppression.
    return {
        "id": str(c.id),
        "label": c.label,
        "forge": c.forge,
        "url": c.url,
        "identity": c.identity,
        "last_status": c.last_status,
        "last_error_code": c.last_error_code,
        "last_checked_at": c.last_checked_at.isoformat() if c.last_checked_at else None,
    }


async def _row(conn: ConnDep, c: Connection) -> dict[str, object]:
    secret = await db_get_secret(conn, c.secret_slug)
    others = await db_count_secret_usages(conn, c.secret_slug, exclude=("connections", c.id))
    return {
        **_payload(c),
        "secret_slug": c.secret_slug,
        "secret_label": secret.label if secret is not None else c.secret_slug,
        "secret_used_by_others": others,
    }


@router.get("")
async def list_connections(conn: ConnDep, _: ConnectionsRead) -> dict[str, object]:
    connections = await db_list_connections(conn)
    return {"connections": [await _row(conn, c) for c in connections]}


@router.post("", status_code=201)
async def create_connection(
    conn: ConnDep,
    crypto: CryptoDep,
    _: ConnectionsWrite,
    http_client_factory: HttpFactoryDep,
    body: ConnectionCreate,
) -> dict[str, object]:
    errors = validate_connection(body.label, body.forge, body.url, body.secret_slug)
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)

    # Le jeton est un secret de la catégorie de la forge, désigné par son slug.
    secret = await db_get_secret(conn, body.secret_slug.strip())
    if secret is None or secret.category != body.forge:
        raise ApiError("connection_secret_invalid", status_code=400, slug=body.secret_slug.strip())
    token = (await resolve_secret_value(conn, crypto, secret.slug)).reveal()

    url = body.url.strip().rstrip("/")
    strategy = get_strategy(body.forge)
    # Vérification AVANT enregistrement : rien n'est écrit si la forge refuse.
    async with http_client_factory() as client:
        try:
            identity = await strategy.verify(client, url, token)
        except ForgeError as exc:
            raise ApiError(exc.code, status_code=400) from None

    try:
        connection = await db_create_connection(
            conn,
            label=body.label.strip(),
            forge=body.forge,
            url=url,
            secret_slug=secret.slug,
            identity=identity,
        )
    except ConnectionDuplicateError:
        raise ApiError("connection_duplicate", status_code=409) from None
    return _payload(connection)


@router.post("/{connection_id}/test")
async def test_connection(
    conn: ConnDep,
    crypto: CryptoDep,
    _: ConnectionsWrite,
    http_client_factory: HttpFactoryDep,
    connection_id: uuid.UUID,
) -> dict[str, object]:
    connection = await db_get_connection(conn, connection_id)
    if connection is None:
        raise ApiError("connection_not_found", status_code=404)
    token = (await resolve_connection_token(conn, crypto, connection)).reveal()
    strategy = get_strategy(connection.forge)
    async with http_client_factory() as client:
        try:
            identity = await strategy.verify(client, connection.url, token)
        except ForgeError as exc:
            await db_update_connection_status(
                conn, connection_id, status="error", identity=None, error_code=exc.code
            )
            updated = await db_get_connection(conn, connection_id)
            assert updated is not None
            return _payload(updated)
    await db_update_connection_status(
        conn, connection_id, status="valid", identity=identity.username, error_code=None
    )
    updated = await db_get_connection(conn, connection_id)
    assert updated is not None
    return _payload(updated)


@router.delete("/{connection_id}", status_code=204)
async def delete_connection(
    conn: ConnDep, _: ConnectionsWrite, connection_id: uuid.UUID, delete_secret: bool = False
) -> None:
    # Une connexion utilisée par un ou plusieurs espaces Git ne se supprime pas :
    # le refus nomme les espaces.
    spaces = await db_spaces_using_connection(conn, connection_id)
    if spaces:
        raise ApiError("connection_in_use", status_code=409, spaces=spaces)
    secret_slug = await db_delete_connection(conn, connection_id)
    if secret_slug is None:
        raise ApiError("connection_not_found", status_code=404)
    # Le secret ne part que si demandé ET s'il n'est plus utilisé par rien
    # d'autre (vérifié à l'instant, la connexion étant déjà supprimée).
    secret_outcome = "kept"  # noqa: S105  (état journalisé, pas un secret)
    if delete_secret:
        secret_outcome = await db_delete_secret_if_unused(conn, secret_slug)
    log.info("connection_removed", secret=secret_outcome)
