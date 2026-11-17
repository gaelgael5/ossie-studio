from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import AdminApiDep
from ossie_studio.crypto import CryptoService, SecretValue
from ossie_studio.deps import ConnDep, get_crypto
from ossie_studio.errors import ApiError
from ossie_studio.models import Secret
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

router = APIRouter(prefix="/api/secrets")

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]


class SecretCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = ""
    label: str = ""
    category: str = ""
    value: str = ""


class SecretValueBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = ""


class SecretLabelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = ""


def _payload(secret: Secret) -> dict[str, object]:
    # La valeur n'est jamais renvoyée, ni chiffrée ni claire.
    return {
        "slug": secret.slug,
        "label": secret.label,
        "category": secret.category,
        "updated_at": secret.updated_at.isoformat(),
    }


@router.get("")
async def list_secrets(conn: ConnDep, _: AdminApiDep) -> dict[str, object]:
    secrets = await db_list_secrets(conn)
    return {"secrets": [_payload(s) for s in secrets], "categories": SECRET_CATEGORIES}


@router.post("", status_code=201)
async def create_secret(
    conn: ConnDep, crypto: CryptoDep, _: AdminApiDep, body: SecretCreate
) -> dict[str, object]:
    slug = body.slug.strip()
    errors = validate_new_secret(slug, body.label, body.category, body.value)
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    try:
        secret = await db_create_secret(
            conn, slug, body.label.strip(), body.category, crypto.encrypt(SecretValue(body.value))
        )
    except SecretSlugTakenError:
        raise ApiError("secret_slug_taken", status_code=409, slug=slug) from None
    log.info("secret_created", slug=slug, category=body.category)
    return _payload(secret)


@router.put("/{slug}/value")
async def replace_value(
    conn: ConnDep, crypto: CryptoDep, _: AdminApiDep, slug: str, body: SecretValueBody
) -> dict[str, object]:
    errors = validate_value(body.value)
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    try:
        secret = await db_replace_secret_value(conn, slug, crypto.encrypt(SecretValue(body.value)))
    except SecretNotFoundError:
        raise ApiError("secret_not_found", status_code=404, slug=slug) from None
    log.info("secret_value_replaced", slug=slug)
    return _payload(secret)


@router.put("/{slug}/label")
async def update_label(
    conn: ConnDep, _: AdminApiDep, slug: str, body: SecretLabelBody
) -> dict[str, object]:
    if not body.label.strip():
        raise ApiError("invalid_request", status_code=400, fields=["label_required"])
    try:
        secret = await db_update_secret_label(conn, slug, body.label.strip())
    except SecretNotFoundError:
        raise ApiError("secret_not_found", status_code=404, slug=slug) from None
    return _payload(secret)


@router.delete("/{slug}", status_code=204)
async def delete_secret(conn: ConnDep, _: AdminApiDep, slug: str) -> None:
    deleted = await db_delete_secret(conn, slug)
    if not deleted:
        raise ApiError("secret_not_found", status_code=404, slug=slug)
    log.info("secret_deleted", slug=slug)
