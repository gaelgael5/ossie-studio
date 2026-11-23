from __future__ import annotations

import asyncio
import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import ConnectionsRead, ConnectionsWrite
from ossie_studio.audit.service import CREATED, DELETED, audited, audited_secret_removal
from ossie_studio.crypto import CryptoService
from ossie_studio.databases.base import VERIFY_TIMEOUT_SECONDS, DbEngineError
from ossie_studio.databases.registry import get_engine, is_known_engine, list_engine_specs
from ossie_studio.databases.service import (
    DbConnectionDuplicateError,
    DbConnectionSlugTakenError,
    db_create_db_connection,
    db_db_connection_slug_exists,
    db_delete_db_connection,
    db_get_db_connection,
    db_list_db_connections,
    db_update_db_connection_status,
)
from ossie_studio.deps import ConnDep, get_crypto
from ossie_studio.errors import ApiError
from ossie_studio.models import DbConnection
from ossie_studio.secretstore.service import db_get_secret, resolve_secret_value
from ossie_studio.secretstore.usages import db_count_secret_usages, db_delete_secret_if_unused
from ossie_studio.slugs import is_valid_slug


def _str_params(params: dict[str, object]) -> dict[str, str]:
    return {k: str(v) for k, v in params.items()}


log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/db-connections")

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]


class DbConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = ""
    # Optionnel : l'interface le déduit du libellé et le laisse corriger.
    slug: str = ""
    engine: str = ""
    params: dict[str, str] = {}
    secret_slug: str = ""


def _payload(c: DbConnection) -> dict[str, object]:
    # Jamais la valeur du secret : seulement de quoi afficher la ligne.
    return {
        "id": str(c.id),
        "label": c.label,
        "slug": c.slug,
        "engine": c.engine,
        "target": c.target,
        "identity": c.identity,
        "server_version": c.server_version,
        "last_status": c.last_status,
        "last_error_code": c.last_error_code,
        "last_checked_at": c.last_checked_at.isoformat() if c.last_checked_at else None,
    }


async def _row(conn: ConnDep, c: DbConnection) -> dict[str, object]:
    secret = await db_get_secret(conn, c.secret_slug)
    others = await db_count_secret_usages(conn, c.secret_slug, exclude=("db_connections", c.id))
    return {
        **_payload(c),
        "secret_slug": c.secret_slug,
        "secret_label": secret.label if secret is not None else c.secret_slug,
        "secret_used_by_others": others,
    }


@router.get("")
async def list_db_connections(conn: ConnDep, _: ConnectionsRead) -> dict[str, object]:
    connections = await db_list_db_connections(conn)
    return {"connections": [await _row(conn, c) for c in connections]}


@router.get("/engines")
async def list_engines(_: ConnectionsRead) -> dict[str, object]:
    # Menu « Ajouter une base » : une entrée par moteur enregistré, avec la
    # description de son formulaire (les textes sont rendus par l'interface).
    return {
        "engines": [
            {
                "engine": spec.engine,
                "secret_category": spec.secret_category,
                "secret_kind": spec.secret_kind,
                "target_field": spec.target_field,
                "fields": [
                    {
                        "name": f.name,
                        "kind": f.kind,
                        "default": f.default,
                        "required": f.required,
                        "options": list(f.options),
                    }
                    for f in spec.fields
                ],
            }
            for spec in list_engine_specs()
        ]
    }


@router.post("", status_code=201)
async def create_db_connection(
    conn: ConnDep,
    crypto: CryptoDep,
    user: ConnectionsWrite,
    body: DbConnectionCreate,
) -> dict[str, object]:
    if not body.label.strip():
        raise ApiError("invalid_request", status_code=400, fields=["label_required"])
    if not is_known_engine(body.engine):
        raise ApiError("invalid_request", status_code=400, fields=["engine_invalid"])
    slug = body.slug.strip()
    if slug and not is_valid_slug(slug):
        raise ApiError("invalid_request", status_code=400, fields=["connection_slug_invalid"])
    # Slug pris : refusé AVANT d'interroger la base (pas de connexion inutile).
    if slug and await db_db_connection_slug_exists(conn, slug):
        raise ApiError("db_connection_slug_taken", status_code=409, slug=slug)
    engine = get_engine(body.engine)
    spec = engine.form_spec()
    secret = await db_get_secret(conn, body.secret_slug.strip())
    if secret is None or secret.category != spec.secret_category:
        raise ApiError("db_connection_secret_invalid", status_code=400)
    secret_value = (await resolve_secret_value(conn, crypto, secret.slug)).reveal()

    # Vérification AVANT enregistrement, avec un délai maximal.
    try:
        identity = await asyncio.wait_for(
            engine.verify(dict(body.params), secret_value), timeout=VERIFY_TIMEOUT_SECONDS + 2
        )
    except DbEngineError as exc:
        raise ApiError(exc.code, status_code=400) from None
    except TimeoutError:
        raise ApiError("db_host_unreachable", status_code=400) from None

    try:
        # Paramètres de connexion hors audit : seuls le moteur et la cible.
        target = body.params.get(spec.target_field, "")
        async with audited(
            conn, user, CREATED, "db_connection", detail={"engine": body.engine, "target": target}
        ) as entry:
            created = await db_create_db_connection(
                conn,
                label=body.label.strip(),
                engine=body.engine,
                target=target,
                params=dict(body.params),
                secret_slug=secret.slug,
                identity=identity.username,
                server_version=identity.server_version,
                slug=slug,
            )
            entry.target(created.id, created.label)
    except DbConnectionSlugTakenError:
        raise ApiError("db_connection_slug_taken", status_code=409, slug=slug) from None
    except DbConnectionDuplicateError:
        raise ApiError("db_connection_duplicate", status_code=409) from None
    return _payload(created)


@router.post("/{connection_id}/test")
async def test_db_connection(
    conn: ConnDep, crypto: CryptoDep, _: ConnectionsWrite, connection_id: uuid.UUID
) -> dict[str, object]:
    connection = await db_get_db_connection(conn, connection_id)
    if connection is None:
        raise ApiError("db_connection_not_found", status_code=404)
    secret_value = (await resolve_secret_value(conn, crypto, connection.secret_slug)).reveal()
    engine = get_engine(connection.engine)
    try:
        identity = await asyncio.wait_for(
            engine.verify(_str_params(connection.params), secret_value),
            timeout=VERIFY_TIMEOUT_SECONDS + 2,
        )
    except (DbEngineError, TimeoutError) as exc:
        code = exc.code if isinstance(exc, DbEngineError) else "db_host_unreachable"
        await db_update_db_connection_status(
            conn, connection_id, status="error", identity=None, server_version=None, error_code=code
        )
        updated = await db_get_db_connection(conn, connection_id)
        assert updated is not None
        return _payload(updated)
    await db_update_db_connection_status(
        conn,
        connection_id,
        status="valid",
        identity=identity.username,
        server_version=identity.server_version,
        error_code=None,
    )
    updated = await db_get_db_connection(conn, connection_id)
    assert updated is not None
    return _payload(updated)


@router.delete("/{connection_id}", status_code=204)
async def delete_db_connection(
    conn: ConnDep, user: ConnectionsWrite, connection_id: uuid.UUID, delete_secret: bool = False
) -> None:
    current = await db_get_db_connection(conn, connection_id)
    secret_outcome = "kept"  # noqa: S105  (état journalisé, pas un secret)
    async with audited(
        conn, user, DELETED, "db_connection", connection_id, current.label if current else ""
    ) as entry:
        secret_slug = await db_delete_db_connection(conn, connection_id)
        entry.skip = secret_slug is None
    if secret_slug is None:
        raise ApiError("db_connection_not_found", status_code=404)
    if delete_secret:
        slug = secret_slug
        secret_outcome = await audited_secret_removal(
            conn, user, slug, lambda: db_delete_secret_if_unused(conn, slug)
        )
    log.info("db_connection_removed", secret=secret_outcome)
