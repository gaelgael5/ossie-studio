"""Onglet « Imports » de la fiche d'un type de cartographie (ticket 49952be0) :
les routes communes d'import (`api/import_routes.py`) branchées sur la cible
« type de cartographie » (`cartographie/import_target.py`).

Différences avec les référentiels, décidées avec Gael : **fusion seulement**
(aucune suppression par import, sujet à part), écran seulement (aucun import par
clé d'API ni par MCP), droit « Cartographie : modifier » pour importer. Imports
rangés sous `cartographie/imports/<slug du type>/`.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from ossie_studio.api.cartographie import api_error, cartographie_target
from ossie_studio.api.common import require_capability
from ossie_studio.api.import_routes import ImportAdapter, Skeleton, import_router
from ossie_studio.cartographie.catalog import catalog
from ossie_studio.cartographie.errors import CartographieError
from ossie_studio.cartographie.import_target import JOURNAL_TYPE, imports_dir, target_of
from ossie_studio.cartographie.model import BASE_PROPERTIES
from ossie_studio.cartographie.service import CartographieTarget
from ossie_studio.cartographie.space import read_cartographie, save_type
from ossie_studio.errors import ApiError
from ossie_studio.imports.errors import ImportsError
from ossie_studio.imports.target import ImportTarget
from ossie_studio.slugs import is_valid_slug
from ossie_studio.spaces.workdir import WorkdirError


def _translated(exc: Exception) -> ApiError:
    if isinstance(exc, CartographieError):
        return api_error(exc)
    if isinstance(exc, ImportsError):
        extra: dict[str, object] = dict(exc.detail)
        if exc.problems:
            extra["problems"] = [p.payload() for p in exc.problems]
        return ApiError(exc.code, status_code=exc.status, **extra)
    assert isinstance(exc, WorkdirError)
    return ApiError(exc.code, status_code=502)


class _Handle:
    def __init__(self, target: CartographieTarget) -> None:
        self._target = target

    async def read(self, fn: Any) -> Any:
        try:
            return await self._target.read(fn)
        except (CartographieError, ImportsError, WorkdirError) as exc:
            raise _translated(exc) from None

    async def write(self, intent: str, fn: Any) -> tuple[Any, str | None]:
        try:
            result: tuple[Any, str | None] = await self._target.write(intent, fn)
        except (CartographieError, ImportsError, WorkdirError) as exc:
            raise _translated(exc) from None
        return result


async def _open(
    conn: Any, crypto: Any, workdir: Any, space_id: uuid.UUID, branch_id: uuid.UUID, user: Any
) -> _Handle:
    return _Handle(await cartographie_target(conn, crypto, workdir, space_id, branch_id, user))


def _require(root: Path, slug: str) -> None:
    if not is_valid_slug(slug) or slug not in read_cartographie(root).types:
        raise CartographieError("type_not_found", status=404, slug=slug)


def _folder(slug: str) -> str:
    if not is_valid_slug(slug):
        raise ApiError("carto_type_not_found", status_code=404, slug=slug)
    return imports_dir(slug)


def _current(root: Path, slug: str) -> tuple[ImportTarget, list[Any]]:
    _require(root, slug)
    target = target_of(read_cartographie(root), slug)
    return target, list(target.current())


def _write(root: Path, slug: str, target: Any, _now: list[Any], items: list[Any]) -> None:
    target.write(root, items)


def _properties(root: Path, slug: str) -> list[str]:
    """La base d'un objet (cle, nom, documentation), la source et la cible d'une
    relation, et les propriétés du type : attributs ArchiMate et propriétés ajoutées
    (ticket 221d832b). Source et cible manquaient : une relation ne pouvait pas
    rattacher ses colonnes d'extrémités (bug b5a86f55), alors que `_skeleton` les
    écrit déjà dans le template."""
    _require(root, slug)
    ctype = read_cartographie(root).types[slug]
    base = catalog().get(ctype.archimate)
    return [
        *(name for name, _kind, _required in BASE_PROPERTIES),
        *(("source", "cible") if base is not None and base.is_relation else ()),
        *(a.name for a in (base.attributes if base else ())),
        *ctype.properties,
    ]


def _add_property(root: Path, slug: str, prop: str, kind: str) -> None:
    """« Ajouter au modèle » : une propriété ajoutée au type, par le service (nom
    valide, non réservé, objets toujours valides)."""
    _require(root, slug)
    ctype = read_cartographie(root).types[slug]
    if prop in ctype.properties:
        raise CartographieError("property_exists", status=409, property=prop)
    changed = replace(ctype, properties={**ctype.properties, prop: {"type": kind}})
    save_type(root, changed, create=False)


def _kind(schema: Any) -> str | None:
    kind = schema.get("type") if isinstance(schema, dict) else None
    return kind if isinstance(kind, str) else None


def _skeleton(root: Path, slug: str) -> Skeleton:
    """Un objet du type tel que son fichier l'écrit (ticket 0598c41b) : la base, la
    source et la cible d'une relation, puis `proprietes` (attributs ArchiMate et
    propriétés ajoutées)."""
    _require(root, slug)
    ctype = read_cartographie(root).types[slug]
    base = catalog().get(ctype.archimate)
    fields: list[tuple[str, str | None]] = [("cle", "string"), ("nom", "string")]
    if base is not None and base.is_relation:
        fields += [("source", "string"), ("cible", "string")]
    fields.append(("documentation", "string"))
    inner = [(a.name, _kind(a.schema())) for a in (base.attributes if base else ())]
    inner += [(name, _kind(schema)) for name, schema in ctype.properties.items()]
    return fields, ("proprietes", inner)


ADAPTER = ImportAdapter(
    journal_type=JOURNAL_TYPE,
    read_dependency=require_capability("cartographie:read"),
    write_dependency=require_capability("cartographie:write"),
    open=_open,
    folder=_folder,
    require=_require,
    current=_current,
    write_items=_write,
    of=lambda slug: f"du type {slug}",
    into=lambda slug: f"dans le type {slug}",
    properties=_properties,
    add_property=_add_property,
    skeleton=_skeleton,
)

router = import_router(
    "/api/spaces/{space_id}/branches/{branch_id}/cartographie/types/{type_slug}/imports", ADAPTER
)
