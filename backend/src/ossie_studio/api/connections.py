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
    ConnectionSlugTakenError,
    db_connection_slug_exists,
    db_create_connection,
    db_delete_connection,
    db_get_connection,
    db_list_connections,
    db_update_connection,
    db_update_connection_status,
    resolve_connection_token,
)
from ossie_studio.crypto import CryptoService
from ossie_studio.deps import ConnDep, get_crypto, get_http_client_factory
from ossie_studio.errors import ApiError
from ossie_studio.forges.base import ForgeError
from ossie_studio.forges.registry import get_strategy, list_form_specs
from ossie_studio.models import Connection
from ossie_studio.secretstore.service import db_get_secret, resolve_secret_value
from ossie_studio.secretstore.usages import db_count_secret_usages, db_delete_secret_if_unused
from ossie_studio.slugs import is_valid_slug
from ossie_studio.spaces.service import db_spaces_using_connection

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/connections")

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]
HttpFactoryDep = Annotated[Callable[[], httpx.AsyncClient], Depends(get_http_client_factory)]


class ConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = ""
    # Optionnel : l'interface le déduit du libellé et le laisse corriger.
    # Absent, le serveur le déduit lui-même (appels antérieurs à ce champ).
    slug: str = ""
    forge: str = ""
    url: str = ""
    secret_slug: str = ""


class ConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = ""
    url: str = ""
    secret_slug: str = ""


def _payload(c: Connection) -> dict[str, object]:
    # Jamais le jeton : seulement de quoi afficher la ligne. Le slug du secret
    # (une référence, pas la valeur) est utile pour piloter la suppression.
    return {
        "id": str(c.id),
        "label": c.label,
        "slug": c.slug,
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
    spaces = await db_spaces_using_connection(conn, c.id)
    return {
        **_payload(c),
        "secret_slug": c.secret_slug,
        "secret_label": secret.label if secret is not None else c.secret_slug,
        "secret_used_by_others": others,
        # « Utilisée par » : espaces Git réels ; les sources n'existent pas encore
        # dans le code → 0, sans erreur.
        "used_by_spaces": len(spaces),
        "used_by_sources": 0,
    }


@router.get("")
async def list_connections(conn: ConnDep, _: ConnectionsRead) -> dict[str, object]:
    connections = await db_list_connections(conn)
    return {"connections": [await _row(conn, c) for c in connections]}


@router.get("/forges")
async def list_forges(_: ConnectionsRead) -> dict[str, object]:
    # Menu « Ajouter une forge » : une entrée par stratégie enregistrée. Les
    # textes (nom, précision, portées) sont rendus par l'interface, par forge.
    return {
        "forges": [
            {
                "forge": spec.forge,
                "default_url": spec.default_url,
                "url_required": spec.url_required,
                "secret_category": spec.forge,
            }
            for spec in list_form_specs()
        ]
    }


@router.get("/{connection_id}/repositories")
async def list_connection_repositories(
    conn: ConnDep,
    crypto: CryptoDep,
    _: ConnectionsRead,
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
            repos = await strategy.list_repositories(client, connection.url, token)
        except ForgeError as exc:
            raise ApiError(exc.code, status_code=400) from None
    return {
        # clone_url : l'adresse à rattacher à un espace (calculée par la forge),
        # url : la page web du dépôt.
        "repositories": [
            {
                "full_name": r.full_name,
                "url": r.url,
                "clone_url": strategy.git_clone_url(connection.url, r.full_name),
            }
            for r in repos
        ],
        "total": len(repos),
    }


@router.get("/{connection_id}/branches")
async def list_connection_branches(
    conn: ConnDep,
    crypto: CryptoDep,
    _: ConnectionsRead,
    http_client_factory: HttpFactoryDep,
    connection_id: uuid.UUID,
    repo: str = "",
) -> dict[str, object]:
    """Branches d'un dépôt lisible par la connexion : sert à proposer la
    « branche suivie » d'une source Git."""
    connection = await db_get_connection(conn, connection_id)
    if connection is None:
        raise ApiError("connection_not_found", status_code=404)
    if not repo.strip():
        raise ApiError("invalid_request", status_code=400, fields=["repo_required"])
    token = (await resolve_connection_token(conn, crypto, connection)).reveal()
    strategy = get_strategy(connection.forge)
    async with http_client_factory() as client:
        try:
            branches = await strategy.list_branches(client, connection.url, token, repo.strip())
        except ForgeError as exc:
            raise ApiError(exc.code, status_code=400) from None
    return {"branches": branches}


@router.post("", status_code=201)
async def create_connection(
    conn: ConnDep,
    crypto: CryptoDep,
    _: ConnectionsWrite,
    http_client_factory: HttpFactoryDep,
    body: ConnectionCreate,
) -> dict[str, object]:
    errors = validate_connection(body.label, body.forge, body.url, body.secret_slug)
    slug = body.slug.strip()
    if slug and not is_valid_slug(slug):
        errors.append("connection_slug_invalid")
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    # Slug pris : refusé AVANT d'interroger la forge (pas d'appel réseau inutile).
    if slug and await db_connection_slug_exists(conn, slug):
        raise ApiError("connection_slug_taken", status_code=409, slug=slug)

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
            slug=slug,
        )
    except ConnectionSlugTakenError:
        raise ApiError("connection_slug_taken", status_code=409, slug=slug) from None
    except ConnectionDuplicateError:
        raise ApiError("connection_duplicate", status_code=409) from None
    return _payload(connection)


@router.put("/{connection_id}")
async def update_connection(
    conn: ConnDep,
    crypto: CryptoDep,
    _: ConnectionsWrite,
    http_client_factory: HttpFactoryDep,
    connection_id: uuid.UUID,
    body: ConnectionUpdate,
) -> dict[str, object]:
    current = await db_get_connection(conn, connection_id)
    if current is None:
        raise ApiError("connection_not_found", status_code=404)
    # Mêmes règles qu'à la création ; la forge (figée) vient de la connexion.
    errors = validate_connection(body.label, current.forge, body.url, body.secret_slug)
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    secret = await db_get_secret(conn, body.secret_slug.strip())
    if secret is None or secret.category != current.forge:
        raise ApiError("connection_secret_invalid", status_code=400, slug=body.secret_slug.strip())
    token = (await resolve_secret_value(conn, crypto, secret.slug)).reveal()

    url = body.url.strip().rstrip("/")
    # Vérification AVANT d'écrire : une modification refusée par la forge laisse
    # la connexion telle qu'elle était (les espaces qui s'en servent continuent).
    async with http_client_factory() as client:
        try:
            identity = await get_strategy(current.forge).verify(client, url, token)
        except ForgeError as exc:
            raise ApiError(exc.code, status_code=400) from None
    try:
        updated = await db_update_connection(
            conn,
            connection_id,
            label=body.label.strip(),
            url=url,
            secret_slug=secret.slug,
            identity=identity,
        )
    except ConnectionDuplicateError:
        raise ApiError("connection_duplicate", status_code=409) from None
    if updated is None:
        raise ApiError("connection_not_found", status_code=404)
    return _payload(updated)


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
