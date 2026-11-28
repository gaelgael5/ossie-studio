"""Lire les référentiels **publiés** d'un espace : service commun à l'API de
lecture (`api/referentiels_read.py`) et au service MCP
(`mcp_surface/referentiels.py`). Une seule logique de lecture.

Seule la version publiée est servie, jamais le brouillon d'une personne. Le
contenu et le commit publié dont il provient sont lus ensemble, sous le verrou
de la copie de lecture : un consommateur sait exactement quelle version il lit.

Les valeurs publiées sont servies telles qu'elles sont dans Git (triées par
clé), sans revalidation.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg

from ossie_studio.api.branches import space_credentials
from ossie_studio.crypto import CryptoService
from ossie_studio.referentiels.errors import ReferentielError
from ossie_studio.referentiels.model import model_key, model_tags
from ossie_studio.referentiels.repo import list_referentiels, read_referentiel
from ossie_studio.referentiels.values import key_of, sorted_values
from ossie_studio.spaces.branch_service import db_get_base
from ossie_studio.spaces.workdir import WorkdirManager

# Lecture d'un référentiel publié illisible (modèle ou valeurs) : un seul refus.
_UNREADABLE = frozenset({"model_unreadable", "values_unreadable", "import_unreadable"})


@dataclass(frozen=True)
class Published[T]:
    """Ce qui a été lu, et le commit publié dont cela provient (vide : rien de
    publié dans l'espace)."""

    data: T
    commit: str


async def read_published[T](
    conn: asyncpg.Connection,
    crypto: CryptoService,
    manager: WorkdirManager,
    space_id: uuid.UUID,
    fn: Callable[[Path], T],
    empty: T,
) -> Published[T]:
    """Applique `fn` à la version publiée de l'espace. Rien de publié : `empty`."""
    base = await db_get_base(conn, space_id)
    if base is None:
        return Published(empty, "")
    space, username, token = await space_credentials(conn, crypto, space_id)
    async with manager.read_copy(space.id, base.git_ref, space.repo_url, username, token) as (
        root,
        _env,
        head,
    ):
        return Published(fn(root), head)


def _read(root: Path, name: str) -> Any:
    return readable(name, lambda n: read_referentiel(root, n))


def readable(name: str, load: Callable[[str], Any]) -> Any:
    """Le référentiel `name` lu par `load` (les fichiers, ou l'index des documents :
    ticket c854ad1e) ; un fichier illisible devient « referentiel_unreadable »."""
    try:
        return load(name)
    except ReferentielError as exc:
        if exc.code in _UNREADABLE:
            raise ReferentielError("referentiel_unreadable", 409, name=name) from None
        raise


# --- Lectures (fonctions pures sur la version publiée) ------------------------------------------


def summaries(root: Path) -> list[dict[str, Any]]:
    """Les référentiels : nom, titre, description, propriétés de la clé, tags, nombre
    de valeurs. Un référentiel illisible est listé avec son erreur, sans casser
    la liste."""
    return summaries_of(list_referentiels(root), lambda name: _read(root, name))


def summaries_of(names: list[str], read: Callable[[str], Any]) -> list[dict[str, Any]]:
    """`summaries`, pour ces référentiels lus par `read` (qui lève comme `_read`)."""
    out: list[dict[str, Any]] = []
    for name in names:
        try:
            ref = read(name)
        except ReferentielError as exc:
            out.append({"name": name, "error": exc.code})
            continue
        out.append(
            {
                "name": name,
                "title": str(ref.model.get("title") or ""),
                "description": str(ref.model.get("description") or ""),
                "key": model_key(ref.model),
                "tags": model_tags(ref.model),
                "count": len(ref.values),
            }
        )
    return out


def model_of(root: Path, name: str) -> dict[str, Any]:
    model: dict[str, Any] = _read(root, name).model
    return model


def _simple_type(model: dict[str, Any], name: str) -> str | None:
    props = model.get("properties")
    schema = props.get(name) if isinstance(props, dict) else None
    if not isinstance(schema, dict):
        return None
    kind = schema.get("type")
    return kind if kind in ("string", "integer", "number", "boolean") else None


def _matches(kind: str, actual: Any, given: Any) -> bool:
    """Égalité typée : une valeur donnée en texte (paramètre d'URL) est lue
    selon le type de la propriété."""
    if isinstance(given, str) and kind != "string":
        try:
            if kind == "boolean":
                lowered = given.strip().lower()
                if lowered not in ("true", "false"):
                    return False
                given = lowered == "true"
            elif kind == "integer":
                given = int(given)
            else:
                given = float(given)
        except ValueError:
            return False
    if kind == "boolean":
        return isinstance(actual, bool) and actual is given
    if kind in ("integer", "number"):
        return (
            not isinstance(actual, bool)
            and isinstance(actual, int | float)
            and not isinstance(given, bool)
            and isinstance(given, int | float)
            and actual == given
        )
    return bool(actual == given)


def _check_filters(model: dict[str, Any], filters: Mapping[str, Any]) -> dict[str, str]:
    kinds: dict[str, str] = {}
    for name in filters:
        kind = _simple_type(model, name)
        if kind is None:
            raise ReferentielError("referentiel_filter_unknown", 400, property=name)
        kinds[name] = kind
    return kinds


def search_values(
    root: Path,
    name: str,
    filters: Mapping[str, Any],
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Les valeurs, triées par clé, filtrées par égalité sur des propriétés
    simples de premier niveau, paginées : `values`, `total`, `has_more`."""
    return search_in(_read(root, name), filters, limit, offset)


def search_in(ref: Any, filters: Mapping[str, Any], limit: int, offset: int) -> dict[str, Any]:
    """`search_values` sur un référentiel déjà lu."""
    kinds = _check_filters(ref.model, filters)
    matching = [
        v
        for v in sorted_values(ref.model, ref.values)
        if all(_matches(kinds[p], v.get(p), given) for p, given in filters.items())
    ]
    page = matching[offset : offset + limit]
    return {
        "values": page,
        "total": len(matching),
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < len(matching),
    }


def value_by_key(root: Path, name: str, key_values: Mapping[str, Any]) -> dict[str, Any]:
    """La valeur dont la clé (composée ou non) vaut `key_values`. Clé
    incomplète ou inconnue : refus qui liste les propriétés attendues."""
    ref = _read(root, name)
    key = model_key(ref.model)
    missing = [k for k in key if k not in key_values]
    unknown = sorted(set(key_values) - set(key))
    if missing or unknown:
        raise ReferentielError(
            "referentiel_key_incomplete", 400, expected=key, missing=missing, unknown=unknown
        )
    kinds = {k: _simple_type(ref.model, k) or "string" for k in key}
    for value in ref.values:
        parts = key_of(value, key)
        if parts is not None and all(
            _matches(kinds[k], part, key_values[k]) for k, part in zip(key, parts, strict=True)
        ):
            found: dict[str, Any] = value
            return found
    raise ReferentielError("referentiel_value_not_found", 404, name=name)
