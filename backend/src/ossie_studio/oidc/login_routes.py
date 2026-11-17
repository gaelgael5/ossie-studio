from __future__ import annotations

import secrets
import uuid
from collections.abc import Callable
from typing import Annotated

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from ossie_studio.crypto import CryptoService
from ossie_studio.deps import SessionDep, get_crypto, get_http_client_factory
from ossie_studio.oidc.linking import LinkOutcome
from ossie_studio.oidc.provider import (
    DiscoveryError,
    IdTokenInvalidError,
    TokenExchangeError,
    build_authorization_url,
    discover,
    exchange_code,
    generate_pkce_pair,
    validate_id_token,
)
from ossie_studio.oidc.service import db_get_connector, resolve_federated_login

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth/oidc")

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]
HttpFactoryDep = Annotated[Callable[[], httpx.AsyncClient], Depends(get_http_client_factory)]

_SESSION_FLOW_KEY = "oidc_flow"


def _callback_uri(request: Request, connector_id: uuid.UUID) -> str:
    return str(request.url_for("oidc_callback", connector_id=connector_id))


@router.get("/{connector_id}/login", name="oidc_login")
async def oidc_login(
    request: Request,
    session: SessionDep,
    http_client_factory: HttpFactoryDep,
    connector_id: uuid.UUID,
) -> Response:
    connector = await db_get_connector(session, connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Connecteur introuvable.")
    async with http_client_factory() as client:
        try:
            metadata = await discover(client, connector.issuer)
        except DiscoveryError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier, challenge = generate_pkce_pair()
    # État transitoire du flux, gardé dans le cookie de session signé.
    request.session[_SESSION_FLOW_KEY] = {
        "connector_id": str(connector_id),
        "state": state,
        "nonce": nonce,
        "verifier": verifier,
    }
    url = build_authorization_url(
        metadata,
        client_id=connector.client_id,
        redirect_uri=_callback_uri(request, connector_id),
        scopes=connector.scopes,
        state=state,
        nonce=nonce,
        code_challenge=challenge,
    )
    return RedirectResponse(url, status_code=303)


@router.get("/{connector_id}/callback", name="oidc_callback")
async def oidc_callback(
    request: Request,
    session: SessionDep,
    crypto: CryptoDep,
    http_client_factory: HttpFactoryDep,
    connector_id: uuid.UUID,
    code: str = "",
    state: str = "",
) -> Response:
    flow = request.session.pop(_SESSION_FLOW_KEY, None)
    if not _flow_matches(flow, connector_id, state) or not code:
        raise HTTPException(status_code=400, detail="Flux OIDC invalide ou expiré.")
    connector = await db_get_connector(session, connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Connecteur introuvable.")

    client_secret = crypto.decrypt(connector.client_secret_encrypted).reveal()
    async with http_client_factory() as client:
        metadata = await discover(client, connector.issuer)
        try:
            token_response = await exchange_code(
                client,
                metadata,
                code=code,
                redirect_uri=_callback_uri(request, connector_id),
                client_id=connector.client_id,
                client_secret=client_secret,
                code_verifier=flow["verifier"],
            )
            claims = await validate_id_token(
                client,
                metadata,
                token_response,
                client_id=connector.client_id,
                nonce=flow["nonce"],
            )
        except (TokenExchangeError, IdTokenInvalidError) as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    decision = await resolve_federated_login(session, connector.issuer, claims)
    if decision.outcome is LinkOutcome.AMBIGUOUS_EMAIL:
        raise HTTPException(
            status_code=403,
            detail="Plusieurs comptes portent cet email : un administrateur doit trancher.",
        )
    if decision.outcome is LinkOutcome.REFUSED_UNVERIFIED:
        raise HTTPException(
            status_code=403, detail="Connexion refusée : email absent ou non vérifié."
        )
    assert decision.user_id is not None
    request.session["user_id"] = str(decision.user_id)
    log.info("oidc_login_succeeded", outcome=decision.outcome.value, issuer=connector.issuer)
    return RedirectResponse("/", status_code=303)


def _flow_matches(flow: object, connector_id: uuid.UUID, state: str) -> bool:
    return (
        isinstance(flow, dict)
        and flow.get("connector_id") == str(connector_id)
        and bool(state)
        and secrets.compare_digest(str(flow.get("state", "")), state)
    )
