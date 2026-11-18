from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

import httpx
import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import OidcRead, OidcWrite
from ossie_studio.crypto import CryptoService, SecretValue
from ossie_studio.deps import ConnDep, get_crypto, get_http_client_factory
from ossie_studio.errors import ApiError
from ossie_studio.oidc.forms import DEFAULT_SCOPES, parse_connector
from ossie_studio.oidc.provider import DiscoveryError, discover
from ossie_studio.oidc.service import (
    IssuerAlreadyDeclaredError,
    db_count_identities_for_issuer,
    db_create_connector,
    db_delete_connector,
    db_get_connector,
    db_list_connectors,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/oidc/connectors")

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]
HttpFactoryDep = Annotated[Callable[[], httpx.AsyncClient], Depends(get_http_client_factory)]


class ConnectorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = ""
    issuer: str = ""
    client_id: str = ""
    client_secret: str = ""
    scopes: str = DEFAULT_SCOPES


@router.get("")
async def list_connectors(conn: ConnDep, _: OidcRead) -> dict[str, object]:
    connectors = await db_list_connectors(conn)
    rows = [
        {
            "id": str(c.id),
            "label": c.label,
            "issuer": c.issuer,
            "accounts": await db_count_identities_for_issuer(conn, c.issuer),
        }
        for c in connectors
    ]
    return {"connectors": rows, "default_scopes": DEFAULT_SCOPES}


@router.post("", status_code=201)
async def create_connector(
    conn: ConnDep,
    crypto: CryptoDep,
    _: OidcWrite,
    http_client_factory: HttpFactoryDep,
    body: ConnectorCreate,
) -> dict[str, str]:
    form = parse_connector(body.label, body.issuer, body.client_id, body.client_secret, body.scopes)
    if form.errors:
        raise ApiError("invalid_request", status_code=400, fields=form.errors)

    # On interroge la découverte AVANT d'enregistrer.
    async with http_client_factory() as client:
        try:
            await discover(client, form.issuer)
        except DiscoveryError:
            raise ApiError("oidc_issuer_unreachable", status_code=400, issuer=form.issuer) from None

    try:
        connector = await db_create_connector(
            conn,
            form.label,
            form.issuer,
            form.client_id,
            crypto.encrypt(SecretValue(form.client_secret)),
            form.scopes,
        )
    except IssuerAlreadyDeclaredError:
        raise ApiError(
            "oidc_issuer_already_declared", status_code=409, issuer=form.issuer
        ) from None
    log.info("oidc_connector_created", issuer=form.issuer)
    return {"id": str(connector.id)}


@router.delete("/{connector_id}", status_code=204)
async def delete_connector(conn: ConnDep, _: OidcWrite, connector_id: uuid.UUID) -> None:
    connector = await db_get_connector(conn, connector_id)
    if connector is None:
        raise ApiError("not_found", status_code=404)
    # Les comptes rattachés et leur couple (issuer, sub) sont conservés.
    await db_delete_connector(conn, connector_id)
    log.info("oidc_connector_deleted", issuer=connector.issuer)
