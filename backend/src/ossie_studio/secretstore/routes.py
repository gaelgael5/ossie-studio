from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ossie_studio.crypto import CryptoService, SecretValue
from ossie_studio.deps import ConnDep, get_crypto, require_admin, templates
from ossie_studio.models import User
from ossie_studio.secretstore.categories import SECRET_CATEGORIES
from ossie_studio.secretstore.forms import validate_new_secret, validate_value
from ossie_studio.secretstore.service import (
    SecretNotFoundError,
    SecretSlugTakenError,
    db_create_secret,
    db_delete_secret,
    db_list_secrets,
    db_replace_secret_value,
    db_update_secret_label,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/secrets")

AdminDep = Annotated[User, Depends(require_admin)]
CryptoDep = Annotated[CryptoService, Depends(get_crypto)]


async def _render_list(
    request: Request, conn: ConnDep, errors: list[str], status_code: int = 200
) -> Response:
    secrets = await db_list_secrets(conn)
    context = {"secrets": secrets, "categories": SECRET_CATEGORIES, "errors": errors}
    return templates.TemplateResponse(request, "secrets.html", context, status_code=status_code)


@router.get("", response_class=HTMLResponse)
async def secrets_page(request: Request, conn: ConnDep, _: AdminDep) -> Response:
    return await _render_list(request, conn, errors=[])


@router.post("", response_class=HTMLResponse)
async def create_secret(
    request: Request,
    conn: ConnDep,
    crypto: CryptoDep,
    _: AdminDep,
    slug: Annotated[str, Form()] = "",
    label: Annotated[str, Form()] = "",
    category: Annotated[str, Form()] = "",
    value: Annotated[str, Form()] = "",
) -> Response:
    slug = slug.strip()
    errors = validate_new_secret(slug, label, category, value)
    if errors:
        return await _render_list(request, conn, errors, status_code=400)
    try:
        await db_create_secret(
            conn, slug, label.strip(), category, crypto.encrypt(SecretValue(value))
        )
    except SecretSlugTakenError:
        message = f"Le slug « {slug} » est déjà pris."
        return await _render_list(request, conn, [message], status_code=409)
    log.info("secret_created", slug=slug, category=category)
    return RedirectResponse("/secrets", status_code=303)


@router.post("/{slug}/value", response_class=HTMLResponse)
async def replace_secret_value(
    request: Request,
    conn: ConnDep,
    crypto: CryptoDep,
    _: AdminDep,
    slug: str,
    value: Annotated[str, Form()] = "",
) -> Response:
    errors = validate_value(value)
    if errors:
        return await _render_list(request, conn, errors, status_code=400)
    try:
        await db_replace_secret_value(conn, slug, crypto.encrypt(SecretValue(value)))
    except SecretNotFoundError:
        raise HTTPException(status_code=404, detail="Secret introuvable.") from None
    log.info("secret_value_replaced", slug=slug)
    return RedirectResponse("/secrets", status_code=303)


@router.post("/{slug}/label", response_class=HTMLResponse)
async def update_secret_label(
    request: Request,
    conn: ConnDep,
    _: AdminDep,
    slug: str,
    label: Annotated[str, Form()] = "",
    new_slug: Annotated[str, Form(alias="slug")] = "",
) -> Response:
    # Le slug est l'identité du secret : toute tentative de le modifier est refusée.
    if new_slug and new_slug != slug:
        return await _render_list(
            request, conn, ["Le slug d'un secret n'est pas modifiable."], status_code=400
        )
    if not label.strip():
        return await _render_list(request, conn, ["Le libellé est obligatoire."], 400)
    try:
        await db_update_secret_label(conn, slug, label.strip())
    except SecretNotFoundError:
        raise HTTPException(status_code=404, detail="Secret introuvable.") from None
    return RedirectResponse("/secrets", status_code=303)


@router.post("/{slug}/delete")
async def delete_secret(conn: ConnDep, _: AdminDep, slug: str) -> Response:
    deleted = await db_delete_secret(conn, slug)
    if not deleted:
        # Rejouer la suppression rend la même réponse, sans erreur serveur.
        raise HTTPException(status_code=404, detail="Secret introuvable.")
    log.info("secret_deleted", slug=slug)
    return RedirectResponse("/secrets", status_code=303)
