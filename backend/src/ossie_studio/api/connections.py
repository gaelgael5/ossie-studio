from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

import httpx
import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import AdminApiDep
from ossie_studio.connections.forms import validate_connection
from ossie_studio.connections.service import (
    ConnectionDuplicateError,
    db_create_connection_with_secret,
    db_delete_connection_and_secret,
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
    token: str = ""


def _payload(c: Connection) -> dict[str, object]:
    # Jamais le slug du secret ni le jeton : seulement de quoi afficher la ligne.
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


@router.get("")
async def list_connections(conn: ConnDep, _: AdminApiDep) -> dict[str, object]:
    connections = await db_list_connections(conn)
    return {"connections": [_payload(c) for c in connections]}


@router.post("", status_code=201)
async def create_connection(
    conn: ConnDep,
    crypto: CryptoDep,
    _: AdminApiDep,
    http_client_factory: HttpFactoryDep,
    body: ConnectionCreate,
) -> dict[str, object]:
    errors = validate_connection(body.label, body.forge, body.url, body.token)
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)

    url = body.url.strip().rstrip("/")
    strategy = get_strategy(body.forge)
    # Vérification AVANT enregistrement : rien n'est écrit si la forge refuse.
    async with http_client_factory() as client:
        try:
            identity = await strategy.verify(client, url, body.token)
        except ForgeError as exc:
            raise ApiError(exc.code, status_code=400) from None

    try:
        connection = await db_create_connection_with_secret(
            conn,
            crypto,
            label=body.label.strip(),
            forge=body.forge,
            url=url,
            token=body.token,
            identity=identity,
        )
    except ConnectionDuplicateError:
        raise ApiError("connection_duplicate", status_code=409) from None
    return _payload(connection)


@router.post("/{connection_id}/test")
async def test_connection(
    conn: ConnDep,
    crypto: CryptoDep,
    _: AdminApiDep,
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
async def delete_connection(conn: ConnDep, _: AdminApiDep, connection_id: uuid.UUID) -> None:
    # Une connexion utilisée par un ou plusieurs espaces Git ne se supprime pas :
    # le refus nomme les espaces.
    spaces = await db_spaces_using_connection(conn, connection_id)
    if spaces:
        raise ApiError("connection_in_use", status_code=409, spaces=spaces)
    deleted = await db_delete_connection_and_secret(conn, connection_id)
    if not deleted:
        raise ApiError("connection_not_found", status_code=404)
