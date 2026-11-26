"""Objets de la cartographie : liste en vignettes, fiche, créer, modifier,
supprimer à la main (epic « Cartographie — types et objets », ticket 6f71bcba).

Un objet = un fichier, `cartographie/objets/<type>/<clé>.yaml`, écrit dans le
brouillon de la personne (comme les types). Ses dates et auteurs viennent de
l'historique Git de la branche ; un objet du seul brouillon n'en a pas encore.
Une relation est un objet comme les autres : elle a sa fiche ; elle se crée avec
le ticket « Relations » (b58a0a13), elle se lit ici sur la fiche de ses extrémités.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.branches import WorkdirDep, space_credentials
from ossie_studio.api.cartographie import api_error, cartographie_target, draft_states
from ossie_studio.api.common import CartographieRead, CartographieWrite
from ossie_studio.api.governance_common import CryptoDep, DraftWorkdirDep
from ossie_studio.api.list_index import ListIndex
from ossie_studio.cartographie.catalog import catalog
from ossie_studio.cartographie.errors import CartographieError, problem_payload
from ossie_studio.cartographie.history import FileDates, file_dates
from ossie_studio.cartographie.model import (
    CARTO_DIR,
    OBJECTS_DIR,
    CartoObject,
    is_empty,
    properties_schema,
)
from ossie_studio.cartographie.service import CartographieTarget
from ossie_studio.cartographie.space import (
    Cartographie,
    delete_object,
    read_cartographie,
    save_object,
)
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.spaces.branch_service import db_get_branch
from ossie_studio.spaces.workdir import WorkdirError, WorkdirManager

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/cartographie/objets")

# Assez pour dater tous les objets d'un espace ordinaire ; au-delà, la date de
# création d'un très vieil objet peut être celle du plus ancien commit lu.
HISTORY_LIMIT = 2000
EXCERPT_LENGTH = 180
SHOWN_PROPERTIES = 3

_OBJECTS_INDEX: ListIndex[list[dict[str, Any]]] = ListIndex()


class ObjectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = ""
    type: str = ""
    name: str = ""
    documentation: str = ""
    properties: dict[str, Any] = {}
    source: str | None = None
    target: str | None = None


class ObjectChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    documentation: str = ""
    properties: dict[str, Any] = {}


def _excerpt(markdown: str) -> str:
    """Le début de la documentation, sans la syntaxe Markdown la plus courante."""
    text = re.sub(r"[`*_>#\[\]()-]+", " ", markdown)
    text = " ".join(text.split())
    return text if len(text) <= EXCERPT_LENGTH else text[: EXCERPT_LENGTH - 1].rstrip() + "…"


def _shown(value: Any) -> str:
    if isinstance(value, bool):
        return "oui" if value else "non"
    if isinstance(value, (list, dict)):
        return f"{len(value)} élément(s)"
    return str(value)


def _dates(found: FileDates | None) -> dict[str, str] | None:
    if found is None:
        return None
    return {
        "created_at": found.created_at,
        "created_by": found.created_by,
        "updated_at": found.updated_at,
        "updated_by": found.updated_by,
    }


async def _history(
    conn: ConnDep,
    crypto: CryptoDep,
    manager: WorkdirManager,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, FileDates]:
    """Dates et auteurs des fichiers d'objets, d'après les commits publiés."""
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        commits = await manager.path_log(
            space.id, branch.git_ref, space.repo_url, username, token, OBJECTS_DIR, HISTORY_LIMIT
        )
    except WorkdirError as exc:
        if exc.code == "branch_not_found":
            return {}
        raise ApiError(exc.code, status_code=502) from None
    return file_dates(commits)


def _summary(
    space: Cartographie, path: str, obj: CartoObject, dates: dict[str, FileDates]
) -> dict[str, Any]:
    ctype = space.types.get(obj.type)
    shown = [
        {"name": name, "value": _shown(value)}
        for name, value in obj.properties.items()
        if not is_empty(value)
    ][:SHOWN_PROPERTIES]
    return {
        "key": obj.key,
        "type": obj.type,
        "type_title": ctype.title if ctype else obj.type,
        "archimate": ctype.archimate if ctype else "",
        "name": obj.name,
        "excerpt": _excerpt(obj.documentation),
        "properties": shown,
        "relations": len(space.users(obj.key)),
        "is_relation": obj.is_relation,
        "source": obj.source,
        "target": obj.target,
        "path": path,
        "dates": _dates(dates.get(path)),
    }


def _with_state(item: dict[str, Any], states: dict[str, str]) -> dict[str, Any]:
    state = states.get(str(item["path"]))
    return {**item, "state": state if state in ("created", "modified") else None}


def _side(space: Cartographie, relation_key: str, other_key: str | None) -> dict[str, Any]:
    """Une ligne du panneau Relations : la relation et l'objet à l'autre bout."""
    _path, relation = space.objects[relation_key]
    rtype = space.types.get(relation.type)
    other = space.objects.get(other_key or "")
    other_type = space.types.get(other[1].type) if other else None
    return {
        "key": relation.key,
        "name": relation.name,
        "type": relation.type,
        "type_title": rtype.title if rtype else relation.type,
        "archimate": rtype.archimate if rtype else "",
        "properties": relation.properties,
        "other": {
            "key": other_key,
            "name": other[1].name if other else None,
            "type_title": other_type.title if other_type else None,
            "exists": other is not None,
        },
    }


def _detail(
    space: Cartographie, key: str, dates: dict[str, FileDates], states: dict[str, str]
) -> dict[str, Any]:
    found = space.objects.get(key)
    if found is None:
        raise ApiError("carto_object_not_found", status_code=404, key=key)
    path, obj = found
    ctype = space.types.get(obj.type)
    base = catalog().get(ctype.archimate) if ctype else None
    users = space.users(key)
    outgoing = [k for k in users if space.objects[k][1].source == key]
    incoming = [k for k in users if space.objects[k][1].target == key]
    state = states.get(path)
    return {
        "key": obj.key,
        "type": obj.type,
        "type_title": ctype.title if ctype else obj.type,
        "archimate": ctype.archimate if ctype else "",
        "name": obj.name,
        "documentation": obj.documentation,
        "properties": obj.properties,
        "source": obj.source,
        "target": obj.target,
        "path": path,
        "schema": properties_schema(ctype, base) if ctype else None,
        "relations": {
            "outgoing": [_side(space, k, space.objects[k][1].target) for k in outgoing],
            "incoming": [_side(space, k, space.objects[k][1].source) for k in incoming],
        },
        "dates": _dates(dates.get(path)),
        "state": state if state in ("created", "modified") else None,
        "problems": [problem_payload(p) for p in space.problems if p.path == path],
    }


@router.get("")
async def list_objects(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieRead,
    workdir: DraftWorkdirDep,
    manager: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    """Tous les objets de l'espace (relations comprises, marquées), vue de la
    personne, avec leurs dates de publication et leur état dans le brouillon."""
    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    try:
        signature, _paths = await target.signature(f"{CARTO_DIR}/")
        key = (space_id, branch_id, workdir.user_id, workdir.published_only)
        items = _OBJECTS_INDEX.get(key, signature)
        if items is None:
            space = await target.cartographie()
            dates = await _history(conn, crypto, manager, space_id, branch_id)
            items = sorted(
                (_summary(space, p, o, dates) for p, o in space.objects.values()),
                key=lambda i: (str(i["name"]).casefold(), str(i["key"])),
            )
            _OBJECTS_INDEX.put(key, signature, items)
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    states = await draft_states(conn, workdir, branch_id)
    return {"readonly": False, "objects": [_with_state(i, states) for i in items]}


@router.get("/{key}")
async def get_object(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieRead,
    workdir: DraftWorkdirDep,
    manager: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    key: str,
) -> dict[str, object]:
    """La fiche d'un objet : ses champs, le schéma de ses propriétés (attributs du
    type ArchiMate et propriétés ajoutées), ses relations sortantes et entrantes,
    ses dates."""
    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    try:
        space = await target.cartographie()
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    dates = await _history(conn, crypto, manager, space_id, branch_id)
    return _detail(space, key, dates, await draft_states(conn, workdir, branch_id))


async def _write_and_read(
    conn: ConnDep,
    crypto: CryptoDep,
    target: CartographieTarget,
    manager: WorkdirManager,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    intent: str,
    key: str,
    fn: Callable[[Path], CartoObject],
) -> dict[str, Any]:
    """Écrit dans le brouillon, puis rend la fiche relue (dates, état, relations)."""
    try:
        _result, revision = await target.write(intent, fn)
        space = await target.cartographie()
    except CartographieError as exc:
        raise api_error(exc) from None
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    dates = await _history(conn, crypto, manager, space_id, branch_id)
    states = await draft_states(conn, target.workdir, branch_id)
    return {**_detail(space, key, dates, states), "revision": revision}


@router.post("", status_code=201)
async def create_object(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieWrite,
    workdir: DraftWorkdirDep,
    manager: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: ObjectBody,
) -> dict[str, object]:
    """Crée un objet dans le brouillon : clé unique dans l'espace et figée, type de
    l'espace figé, nom obligatoire, propriétés valides pour le schéma du type."""
    obj = CartoObject(
        key=body.key.strip(),
        type=body.type.strip(),
        name=body.name.strip(),
        documentation=body.documentation,
        properties=body.properties,
        source=body.source,
        target=body.target,
    )
    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    intent = f"crée l'objet {obj.key}"
    return await _write_and_read(
        conn,
        crypto,
        target,
        manager,
        space_id,
        branch_id,
        intent,
        obj.key,
        lambda root: save_object(root, obj, create=True),
    )


@router.put("/{key}")
async def save_object_route(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieWrite,
    workdir: DraftWorkdirDep,
    manager: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    key: str,
    body: ObjectChange,
) -> dict[str, object]:
    """Modifie le nom, la documentation et les propriétés d'un objet ; la clé, le
    type et, pour une relation, sa source et sa cible ne changent pas."""

    def write(root: Path) -> CartoObject:
        current = read_cartographie(root).objects.get(key)
        if current is None:
            raise CartographieError("object_not_found", status=404, key=key)
        existing = current[1]
        obj = CartoObject(
            key=key,
            type=existing.type,
            name=body.name.strip(),
            documentation=body.documentation,
            properties=body.properties,
            source=existing.source,
            target=existing.target,
        )
        return save_object(root, obj, create=False)

    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    return await _write_and_read(
        conn, crypto, target, manager, space_id, branch_id, f"modifie l'objet {key}", key, write
    )


@router.delete("/{key}")
async def delete_object_route(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    key: str,
) -> dict[str, object]:
    """Supprime le fichier d'un objet dans le brouillon ; refusé
    (« carto_object_in_use », avec les relations) tant qu'une relation l'utilise."""
    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    try:
        _none, revision = await target.write(
            f"supprime l'objet {key}", lambda root: delete_object(root, key)
        )
    except CartographieError as exc:
        raise api_error(exc) from None
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    return {"deleted": key, "revision": revision}
