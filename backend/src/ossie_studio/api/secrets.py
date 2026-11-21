from __future__ import annotations

import base64
import binascii
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import SecretsRead, SecretsWrite
from ossie_studio.crypto import CryptoService, SecretValue
from ossie_studio.deps import ConnDep, get_crypto
from ossie_studio.errors import ApiError
from ossie_studio.models import Secret
from ossie_studio.secretstore.categories import (
    SECRET_CATEGORIES,
    SECRET_CATEGORY_INPUTS,
    is_known_category,
)
from ossie_studio.secretstore.forms import validate_new_secret, validate_value
from ossie_studio.secretstore.service import (
    SecretNotFoundError,
    SecretSlugTakenError,
    db_create_secret,
    db_delete_secret,
    db_list_secrets,
    db_list_secrets_by_category,
    db_replace_secret_value,
    db_update_secret_label,
)
from ossie_studio.secretstore.usages import db_count_secret_usages, db_list_secret_usages

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/secrets")

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]


def _opened_key_value(body: SecretCreate) -> str:
    """Valeur d'un secret « keyfile » : la clé privée ouverte. Le fichier arrive
    en base64 ; la phrase de passe sert à l'ouvrir et n'est pas conservée."""
    from ossie_studio.databases.base import DbEngineError
    from ossie_studio.databases.keys import open_private_key

    if not body.key_file:
        raise ApiError("invalid_request", status_code=400, fields=["key_file_required"])
    try:
        pem = base64.b64decode(body.key_file, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError("db_key_rejected", status_code=400) from None
    try:
        return open_private_key(pem, body.passphrase)
    except DbEngineError:
        raise ApiError("db_key_rejected", status_code=400) from None


class SecretCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = ""
    label: str = ""
    category: str = ""
    value: str = ""
    # Catégories « keyfile » (Snowflake) : la valeur vient d'un fichier de clé
    # privée (base64) et d'une phrase de passe facultative, ouverte ici — c'est
    # la clé ouverte qui est stockée, la phrase de passe n'est pas conservée.
    key_file: str = ""
    passphrase: str = ""


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
async def list_secrets(conn: ConnDep, _: SecretsRead, category: str = "") -> dict[str, object]:
    # Filtre optionnel par catégorie (pour alimenter les listes déroulantes des
    # popups de connexion) ; catégorie inconnue → refus explicite.
    if category.strip():
        if not is_known_category(category):
            raise ApiError("secret_category_unknown", status_code=400, category=category)
        secrets = await db_list_secrets_by_category(conn, category)
    else:
        secrets = await db_list_secrets(conn)
    rows = [
        {**_payload(s), "used_by": await db_count_secret_usages(conn, s.slug)} for s in secrets
    ]
    return {
        "secrets": rows,
        "categories": SECRET_CATEGORIES,
        "category_inputs": SECRET_CATEGORY_INPUTS,
    }


@router.post("", status_code=201)
async def create_secret(
    conn: ConnDep, crypto: CryptoDep, _: SecretsWrite, body: SecretCreate
) -> dict[str, object]:
    slug = body.slug.strip()
    # Pour une catégorie « keyfile », la valeur stockée est la clé ouverte.
    if SECRET_CATEGORY_INPUTS.get(body.category) == "keyfile":
        value = _opened_key_value(body)
    else:
        value = body.value
    errors = validate_new_secret(slug, body.label, body.category, value)
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    try:
        secret = await db_create_secret(
            conn, slug, body.label.strip(), body.category, crypto.encrypt(SecretValue(value))
        )
    except SecretSlugTakenError:
        raise ApiError("secret_slug_taken", status_code=409, slug=slug) from None
    log.info("secret_created", slug=slug, category=body.category)
    return _payload(secret)


@router.put("/{slug}/value")
async def replace_value(
    conn: ConnDep, crypto: CryptoDep, _: SecretsWrite, slug: str, body: SecretValueBody
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
    conn: ConnDep, _: SecretsWrite, slug: str, body: SecretLabelBody
) -> dict[str, object]:
    if not body.label.strip():
        raise ApiError("invalid_request", status_code=400, fields=["label_required"])
    try:
        secret = await db_update_secret_label(conn, slug, body.label.strip())
    except SecretNotFoundError:
        raise ApiError("secret_not_found", status_code=404, slug=slug) from None
    return _payload(secret)


@router.delete("/{slug}", status_code=204)
async def delete_secret(conn: ConnDep, _: SecretsWrite, slug: str) -> None:
    # Supprimer un secret utilisé casserait silencieusement les objets qui le
    # désignent : on refuse, en disant combien l'utilisent.
    usages = await db_list_secret_usages(conn, slug)
    if usages:
        raise ApiError("secret_in_use", status_code=409, slug=slug, count=len(usages))
    deleted = await db_delete_secret(conn, slug)
    if not deleted:
        raise ApiError("secret_not_found", status_code=404, slug=slug)
    log.info("secret_deleted", slug=slug)
