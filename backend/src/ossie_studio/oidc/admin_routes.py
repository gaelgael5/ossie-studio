from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

import httpx
import structlog
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ossie_studio.crypto import CryptoService, SecretValue
from ossie_studio.deps import (
    ConnDep,
    get_crypto,
    get_http_client_factory,
    require_admin,
    templates,
)
from ossie_studio.models import User
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

router = APIRouter(prefix="/oidc/connectors")

AdminDep = Annotated[User, Depends(require_admin)]
CryptoDep = Annotated[CryptoService, Depends(get_crypto)]
HttpFactoryDep = Annotated[Callable[[], httpx.AsyncClient], Depends(get_http_client_factory)]


async def _render(
    request: Request, conn: ConnDep, errors: list[str], status_code: int = 200
) -> Response:
    connectors = await db_list_connectors(conn)
    rows = [
        {
            "connector": connector,
            "accounts": await db_count_identities_for_issuer(conn, connector.issuer),
        }
        for connector in connectors
    ]
    context = {"rows": rows, "errors": errors, "default_scopes": DEFAULT_SCOPES}
    return templates.TemplateResponse(request, "oidc_connectors.html", context, status_code)


@router.get("", response_class=HTMLResponse)
async def connectors_page(request: Request, conn: ConnDep, _: AdminDep) -> Response:
    return await _render(request, conn, errors=[])


@router.post("", response_class=HTMLResponse)
async def create_connector(
    request: Request,
    conn: ConnDep,
    crypto: CryptoDep,
    _: AdminDep,
    http_client_factory: HttpFactoryDep,
    label: Annotated[str, Form()] = "",
    issuer: Annotated[str, Form()] = "",
    client_id: Annotated[str, Form()] = "",
    client_secret: Annotated[str, Form()] = "",
    scopes: Annotated[str, Form()] = DEFAULT_SCOPES,
) -> Response:
    form = parse_connector(label, issuer, client_id, client_secret, scopes)
    if form.errors:
        return await _render(request, conn, form.errors, status_code=400)

    # On interroge la découverte AVANT d'enregistrer : issuer injoignable ou
    # incohérent → rien n'est écrit.
    async with http_client_factory() as client:
        try:
            await discover(client, form.issuer)
        except DiscoveryError as exc:
            return await _render(request, conn, [str(exc)], status_code=400)

    try:
        await db_create_connector(
            conn,
            form.label,
            form.issuer,
            form.client_id,
            crypto.encrypt(SecretValue(form.client_secret)),
            form.scopes,
        )
    except IssuerAlreadyDeclaredError:
        message = "Un connecteur existe déjà sur cet issuer."
        return await _render(request, conn, [message], status_code=409)
    log.info("oidc_connector_created", issuer=form.issuer)
    return RedirectResponse("/oidc/connectors", status_code=303)


@router.post("/{connector_id}/delete")
async def delete_connector(conn: ConnDep, _: AdminDep, connector_id: uuid.UUID) -> Response:
    connector = await db_get_connector(conn, connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Connecteur introuvable.")
    # Les comptes rattachés ne sont pas supprimés : leur couple (issuer, sub)
    # est conservé, seul le moyen de connexion disparaît.
    await db_delete_connector(conn, connector_id)
    log.info("oidc_connector_deleted", issuer=connector.issuer)
    return RedirectResponse("/oidc/connectors", status_code=303)
