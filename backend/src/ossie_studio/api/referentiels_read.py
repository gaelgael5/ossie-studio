"""API de lecture des référentiels publiés d'un espace (epic « Référentiels »).

Lecture seule, version publiée uniquement : un brouillon n'est jamais servi.
Chaque réponse porte le commit publié dont elle provient (`commit`).

Accès :
- par **clé d'API de service** (`Authorization: Bearer osk_…`) : son profil
  agent doit contenir la primitive MCP de la même lecture (un seul droit pour
  l'API et le MCP) ;
- par une **personne connectée**, avec `spaces:read` sur l'espace.

La logique de lecture est celle du service MCP : `referentiels/published.py`.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ossie_studio.api.branches import WorkdirDep
from ossie_studio.api.common import require_capability
from ossie_studio.api.governance_common import CryptoDep
from ossie_studio.api_keys.service import resolve_api_key
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.mcp_surface.authz import authorize
from ossie_studio.referentiels import published
from ossie_studio.referentiels.errors import ReferentielError
from ossie_studio.spaces.workdir import WorkdirError

router = APIRouter(prefix="/api/spaces/{space_id}/referentiels", tags=["referentiels"])

_bearer = HTTPBearer(auto_error=False, description="Clé d'API de service (osk_…).")
BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]

# Chaque lecture et la primitive MCP qui donne le même droit à une clé.
LIST_PRIMITIVE = "list_referentiels"
MODEL_PRIMITIVE = "read_referentiel_model"
SEARCH_PRIMITIVE = "search_referentiel_values"
VALUE_PRIMITIVE = "get_referentiel_value"
MAX_LIMIT = 500


def _reader(primitive: str) -> Callable[..., Awaitable[None]]:
    async def dependency(
        request: Request, conn: ConnDep, space_id: uuid.UUID, bearer: BearerDep
    ) -> None:
        if bearer is not None:
            caller = await resolve_api_key(conn, bearer.credentials, "")
            if caller is None:
                raise ApiError("unauthenticated", status_code=401)
            if not await authorize(conn, caller, primitive, space_id):
                raise ApiError("forbidden", status_code=403)
            return
        await require_capability("spaces:read")(request, conn)

    return dependency


def _pairs(raw: list[str], field: str) -> dict[str, str]:
    """`["pays:FR", "mode:site"]` → `{"pays": "FR", "mode": "site"}`."""
    out: dict[str, str] = {}
    for item in raw:
        name, sep, value = item.partition(":")
        if not sep or not name.strip():
            raise ApiError("invalid_request", status_code=400, fields=[f"{field}_invalid"])
        out[name.strip()] = value
    return out


async def _read[T](
    conn: ConnDep,
    crypto: CryptoDep,
    manager: WorkdirDep,
    space_id: uuid.UUID,
    fn: Callable[[Any], T],
    empty: T,
) -> published.Published[T]:
    try:
        return await published.read_published(conn, crypto, manager, space_id, fn, empty)
    except ReferentielError as exc:
        raise ApiError(exc.code, status_code=exc.status, **exc.detail) from None
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None


@router.get("", dependencies=[Depends(_reader(LIST_PRIMITIVE))])
async def list_published_referentiels(
    conn: ConnDep, crypto: CryptoDep, manager: WorkdirDep, space_id: uuid.UUID
) -> dict[str, object]:
    """Les référentiels publiés de l'espace : nom, titre, description,
    propriétés de la clé, nombre de valeurs ; et le commit publié lu."""
    empty: list[dict[str, Any]] = []
    read = await _read(conn, crypto, manager, space_id, published.summaries, empty)
    return {"commit": read.commit, "referentiels": read.data}


@router.get("/{name}/modele", dependencies=[Depends(_reader(MODEL_PRIMITIVE))])
async def read_published_model(
    conn: ConnDep, crypto: CryptoDep, manager: WorkdirDep, space_id: uuid.UUID, name: str
) -> dict[str, object]:
    """Le modèle (JSON Schema 2020-12, clé sous `x-key`) d'un référentiel publié."""
    read = await _read(
        conn, crypto, manager, space_id, lambda root: published.model_of(root, name), None
    )
    if read.data is None:
        raise ApiError("referentiel_not_found", status_code=404, name=name)
    return {"commit": read.commit, "name": name, "model": read.data}


@router.get("/{name}/valeurs", dependencies=[Depends(_reader(SEARCH_PRIMITIVE))])
async def search_published_values(
    conn: ConnDep,
    crypto: CryptoDep,
    manager: WorkdirDep,
    space_id: uuid.UUID,
    name: str,
    filter: Annotated[
        list[str],
        Query(description="Égalité sur une propriété simple : `propriete:valeur` (répétable)."),
    ] = [],  # noqa: B006 — liste de requête FastAPI, jamais modifiée
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    """Les valeurs d'un référentiel publié, triées par clé, filtrées et paginées."""
    filters = _pairs(filter, "filter")
    read = await _read(
        conn,
        crypto,
        manager,
        space_id,
        lambda root: published.search_values(root, name, filters, limit, offset),
        None,
    )
    if read.data is None:
        raise ApiError("referentiel_not_found", status_code=404, name=name)
    return {"commit": read.commit, "name": name, **read.data}


@router.get("/{name}/valeurs/cle", dependencies=[Depends(_reader(VALUE_PRIMITIVE))])
async def read_published_value(
    conn: ConnDep,
    crypto: CryptoDep,
    manager: WorkdirDep,
    space_id: uuid.UUID,
    name: str,
    key: Annotated[
        list[str],
        Query(
            description=(
                "Chaque propriété de la clé : `propriete:valeur` (répétable ; toutes les "
                "propriétés d'une clé composée)."
            )
        ),
    ] = [],  # noqa: B006 — liste de requête FastAPI, jamais modifiée
) -> dict[str, object]:
    """Une valeur d'un référentiel publié, par sa clé (composée ou non)."""
    parts = _pairs(key, "key")
    read = await _read(
        conn,
        crypto,
        manager,
        space_id,
        lambda root: published.value_by_key(root, name, parts),
        None,
    )
    if read.data is None:
        raise ApiError("referentiel_not_found", status_code=404, name=name)
    return {"commit": read.commit, "name": name, "value": read.data}
