"""Onglet « Imports » d'un référentiel (tickets cda9c43a et a909dc66) : les
routes communes d'import (`api/import_routes.py`, enabler 4f32c8c6) branchées
sur la cible « référentiel ». Mêmes adresses, mêmes fichiers, mêmes refus, même
journal qu'avant la factorisation.

Les API des référentiels sont **en lecture seule** (Gael, 24 novembre : « Les
api sur les référentiels sont en lecture seules ») : une clé d'API n'importe
rien ; ces routes sont celles de l'écran, pour une personne connectée.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from ossie_studio.api.common import require_capability
from ossie_studio.api.import_routes import ImportAdapter, import_router
from ossie_studio.api.referentiels import read_in, referentiel_target, write_in
from ossie_studio.imports.target import ImportTarget
from ossie_studio.referentiels.errors import ReferentielError
from ossie_studio.referentiels.import_target import JOURNAL_TYPE, ReferentielImportTarget
from ossie_studio.referentiels.repo import (
    imports_dir,
    read_referentiel,
    require_referentiel,
    write_referentiel,
)


class _Handle:
    def __init__(self, target: Any) -> None:
        self._target = target

    async def read(self, fn: Any) -> Any:
        return await read_in(self._target, fn)

    async def write(self, intent: str, fn: Any) -> tuple[Any, str | None]:
        result: tuple[Any, str | None] = await write_in(self._target, intent, fn)
        return result


async def _open(
    conn: Any, crypto: Any, workdir: Any, space_id: uuid.UUID, branch_id: uuid.UUID, user: Any
) -> _Handle:
    return _Handle(await referentiel_target(conn, crypto, workdir, space_id, branch_id, user))


def _current(root: Path, name: str) -> tuple[ImportTarget, list[Any]]:
    ref = read_referentiel(root, name)
    return ReferentielImportTarget(ref.model), list(ref.values)


def _write(root: Path, name: str, target: ImportTarget, _now: list[Any], items: list[Any]) -> None:
    model = read_referentiel(root, name).model
    write_referentiel(root, name, model, items, create=False)


def _properties(root: Path, name: str) -> list[str]:
    """Les propriétés de premier niveau du modèle (ticket 221d832b)."""
    props = read_referentiel(root, name).model.get("properties")
    return list(props) if isinstance(props, dict) else []


def _add_property(root: Path, name: str, prop: str, kind: str) -> None:
    """« Ajouter au modèle » : une propriété facultative, validée avec le modèle."""
    ref = read_referentiel(root, name)
    model = dict(ref.model)
    props = dict(model.get("properties") or {})
    if prop in props:
        raise ReferentielError("model_property_exists", 409, property=prop)
    props[prop] = {"type": kind}
    model["properties"] = props
    write_referentiel(root, name, model, ref.values, create=False)


ADAPTER = ImportAdapter(
    journal_type=JOURNAL_TYPE,
    modes=("merge", "replace"),
    read_dependency=require_capability("referentiels:read"),
    write_dependency=require_capability("referentiels:write"),
    open=_open,
    folder=imports_dir,
    require=require_referentiel,
    current=_current,
    write_items=_write,
    of=lambda name: f"de {name}",
    into=lambda name: f"dans {name}",
    properties=_properties,
    add_property=_add_property,
)

router = import_router(
    "/api/spaces/{space_id}/branches/{branch_id}/referentiels/{name}/imports", ADAPTER
)
