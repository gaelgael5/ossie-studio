"""Écran « Cartographie » : les types de l'espace en vignettes, ajouter un type
(epic « Cartographie — types et objets », ticket e7521e80).

Comme les Référentiels : les lectures voient la vue de la personne (publié + son
brouillon), les écritures vont dans son **brouillon**, sur toute branche, racine
comprise (bug e7deb2b9) ; rien n'atteint la branche avant la publication. Le
format et ses contrôles sont ceux de `cartographie/` (enablers f8167ae1 et
0e088eaa), jamais refaits ici.
"""

from __future__ import annotations

import uuid
from functools import cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ossie_studio.api.branches import space_credentials
from ossie_studio.api.common import CartographieRead, CartographieWrite
from ossie_studio.api.governance_common import CryptoDep, DraftWorkdirDep
from ossie_studio.api.list_index import ListIndex
from ossie_studio.cartographie.catalog import ArchimateType, catalog
from ossie_studio.cartographie.errors import CartographieError
from ossie_studio.cartographie.model import (
    ARCHIMATE_KEY,
    BASE_PROPERTIES,
    CARTO_DIR,
    CartoType,
    check_type,
    dump_type,
    type_path,
)
from ossie_studio.cartographie.rules import involved_relations
from ossie_studio.cartographie.service import CartographieTarget
from ossie_studio.cartographie.space import Cartographie, delete_type, save_type
from ossie_studio.deps import ConnDep
from ossie_studio.drafts.store import db_get_draft, db_list_object_states
from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.errors import ApiError
from ossie_studio.models import User
from ossie_studio.slugs import is_valid_slug
from ossie_studio.spaces.branch_service import db_get_branch
from ossie_studio.spaces.workdir import WorkdirError

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/cartographie")

# Liste des types : lue une fois par empreinte de `cartographie/` (ticket 0373780f).
_TYPES_INDEX: ListIndex[list[dict[str, Any]]] = ListIndex()


class TypeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = ""
    slug: str = ""
    archimate: str = ""


class SchemaBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_: dict[str, Any] = Field(alias="schema")


def api_error(exc: CartographieError) -> ApiError:
    extra: dict[str, object] = dict(exc.detail)
    if exc.problems:
        extra["problems"] = [p.payload() for p in exc.problems]
    return ApiError(f"carto_{exc.code}", status_code=exc.status, **extra)


async def cartographie_target(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: DraftWorkdir,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    user: User,
) -> CartographieTarget:
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    space, username, token = await space_credentials(conn, crypto, space_id)
    return CartographieTarget(workdir, space, branch.git_ref, username, token, user.email)


async def _draft_states(
    conn: ConnDep, workdir: DraftWorkdir, branch_id: uuid.UUID
) -> dict[str, str]:
    """État dans le brouillon (« created », « modified », « deleted ») des fichiers
    de la cartographie, par chemin — sur les seuls chemins, sans contenu."""
    draft = await db_get_draft(conn, workdir.user_id, branch_id)
    if draft is None:
        return {}
    prefix = f"{CARTO_DIR}/"
    return {
        path: state
        for path, state in await db_list_object_states(conn, draft.id)
        if path.startswith(prefix)
    }


def _attribute(a: Any) -> dict[str, object]:
    return {
        "name": a.name,
        "label": a.label,
        "type": a.type,
        "values": [{"value": v, "label": label} for v, label in a.values.items()],
        "unset": a.unset,
    }


def _archimate_type(t: ArchimateType) -> dict[str, object]:
    return {
        "name": t.name,
        "kind": t.kind,
        "layer": t.layer,
        "label": t.label,
        "definition": t.definition,
        "icon": t.icon(),
        "attributes": [_attribute(a) for a in t.attributes],
        "relations": list(involved_relations(t.name)),
    }


@cache
def _catalog_payload() -> dict[str, object]:
    cat = catalog()
    return {
        "archimate": cat.archimate,
        "layers": [{"key": k, "label": label} for k, label in cat.layers.items()],
        "base": [{"name": n, "type": t, "required": r} for n, t, r in BASE_PROPERTIES],
        "types": [_archimate_type(t) for t in cat.types.values()],
    }


def _summaries(space: Cartographie) -> list[dict[str, Any]]:
    """Les types de l'espace, par libellé, avec le nombre de propriétés ajoutées et
    d'objets ; et le nombre de problèmes trouvés à la lecture."""
    counts: dict[str, int] = {}
    for _path, obj in space.objects.values():
        counts[obj.type] = counts.get(obj.type, 0) + 1
    types = sorted(space.types.values(), key=lambda t: (t.title.casefold(), t.slug))
    return [
        {
            "slug": t.slug,
            "title": t.title,
            "archimate": t.archimate,
            "path": type_path(t.slug),
            "properties": len(t.properties),
            "objects": counts.get(t.slug, 0),
        }
        for t in types
    ]


def _with_state(item: dict[str, Any], states: dict[str, str]) -> dict[str, Any]:
    state = states.get(str(item["path"]))
    return {**item, "state": state if state in ("created", "modified") else None}


@router.get("/catalogue")
async def get_catalogue(_: CartographieRead) -> dict[str, object]:
    """Le catalogue ArchiMate 3.2 livré avec l'application : couches, types
    (libellé, définition, pictogramme, attributs propres, relations auxquelles ils
    prennent part) et propriétés de base de tout objet."""
    return _catalog_payload()


@router.get("/types")
async def list_types(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    """Les types de l'espace (vue de la personne), marqués « created » (créé dans
    son brouillon, pas encore publié) ou « modified »."""
    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    try:
        # Les objets comptent aussi (nombre par type) : tout `cartographie/`.
        signature, _paths = await target.signature(f"{CARTO_DIR}/")
        key = (space_id, branch_id, workdir.user_id, workdir.published_only)
        items = _TYPES_INDEX.get(key, signature)
        if items is None:
            space = await target.cartographie()
            items = _summaries(space)
            _TYPES_INDEX.put(key, signature, items)
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    states = await _draft_states(conn, workdir, branch_id)
    return {"readonly": False, "types": [_with_state(i, states) for i in items]}


def _candidate(body: TypeBody) -> CartoType:
    label, slug, archimate = body.label.strip(), body.slug.strip(), body.archimate.strip()
    fields = [
        f
        for f, bad in (
            ("label_required", not label),
            ("slug_invalid", not is_valid_slug(slug)),
            ("archimate_unknown", catalog().get(archimate) is None),
        )
        if bad
    ]
    if fields:
        raise ApiError("invalid_request", status_code=400, fields=fields)
    return CartoType(slug=slug, title=label, archimate=archimate)


@router.post("/types/apercu")
async def preview_type(_: CartographieRead, body: TypeBody) -> dict[str, object]:
    """Le fichier que créerait l'ajout de ce type (chemin et contenu exacts) ;
    rien n'est lu ni écrit. Un slug déjà pris est refusé à l'ajout."""
    ctype = _candidate(body)
    problems = check_type(ctype, catalog())
    return {
        "path": type_path(ctype.slug),
        "yaml": dump_type(ctype),
        "problems": [p.payload() for p in problems],
    }


@router.post("/types", status_code=201)
async def create_type(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: TypeBody,
) -> dict[str, object]:
    """Ajoute un type à l'espace, dans le brouillon de la personne : un type
    ArchiMate et un nom. Refus « carto_type_exists » si le slug est déjà pris."""
    ctype = _candidate(body)
    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)

    def write(root: Path) -> CartoType:
        return save_type(root, ctype, create=True)

    try:
        created, revision = await target.write(f"crée le type {ctype.slug}", write)
    except CartographieError as exc:
        raise api_error(exc) from None
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    item = {
        "slug": created.slug,
        "title": created.title,
        "archimate": created.archimate,
        "path": type_path(created.slug),
        "properties": len(created.properties),
        "objects": 0,
        "state": "created",
    }
    return {"type": item, "revision": revision}


# --- Personnaliser un type (ticket 5c39d4df) -------------------------------------------------

# Clés de premier niveau d'un type que l'on accepte : le reste serait perdu à
# l'écriture, il est refusé plutôt que d'être effacé en silence.
_TYPE_KEYS = {"$schema", "title", ARCHIMATE_KEY, "type", "properties", "required"}


def _from_schema(slug: str, schema: dict[str, Any]) -> CartoType:
    unsupported = sorted(set(schema) - _TYPE_KEYS)
    if unsupported:
        raise ApiError("invalid_request", status_code=400, fields=["schema_keys"], keys=unsupported)
    if schema.get("type", "object") != "object":
        raise ApiError("invalid_request", status_code=400, fields=["schema_type"])
    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise ApiError("invalid_request", status_code=400, fields=["schema_properties"])
    return CartoType(
        slug=slug,
        title=str(schema.get("title") or "").strip(),
        archimate=str(schema.get(ARCHIMATE_KEY) or ""),
        properties=properties,
        required=[str(r) for r in required],
    )


def _detail(space: Cartographie, slug: str, states: dict[str, str]) -> dict[str, object]:
    ctype = space.types.get(slug)
    if ctype is None:
        raise ApiError("carto_type_not_found", status_code=404, slug=slug)
    path = type_path(slug)
    state = states.get(path)
    return {
        "slug": slug,
        "title": ctype.title,
        "archimate": ctype.archimate,
        "path": path,
        "document": ctype.document(),
        "yaml": dump_type(ctype),
        "objects": len(space.objects_of(slug)),
        "usage": space.property_usage(slug),
        "state": state if state in ("created", "modified") else None,
        "problems": [p.payload() for p in space.problems if p.path == path],
    }


@router.get("/types/{slug}")
async def get_type(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
) -> dict[str, object]:
    """Un type de l'espace (vue de la personne) : son document, son YAML, le nombre
    de ses objets et, pour chaque propriété ajoutée, le nombre d'objets qui
    l'utilisent."""
    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    try:
        space = await target.cartographie()
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    return _detail(space, slug, await _draft_states(conn, workdir, branch_id))


@router.put("/types/{slug}")
async def save_type_route(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    body: SchemaBody,
) -> dict[str, object]:
    """Enregistre un type dans le brouillon : libellé et propriétés ajoutées. Le
    slug et le type ArchiMate sont figés ; retirer une propriété utilisée par des
    objets, ou rendre des objets invalides, est refusé."""
    ctype = _from_schema(slug, body.schema_)
    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    try:
        _saved, revision = await target.write(
            f"modifie le type {slug}", lambda root: save_type(root, ctype, create=False)
        )
        space = await target.cartographie()
    except CartographieError as exc:
        raise api_error(exc) from None
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    states = await _draft_states(conn, workdir, branch_id)
    return {**_detail(space, slug, states), "revision": revision}


@router.delete("/types/{slug}")
async def delete_type_route(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
) -> dict[str, object]:
    """Supprime un type dans le brouillon ; refusé (« carto_type_in_use ») tant
    qu'il a des objets."""
    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    try:
        _none, revision = await target.write(
            f"supprime le type {slug}", lambda root: delete_type(root, slug)
        )
    except CartographieError as exc:
        raise api_error(exc) from None
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    return {"deleted": slug, "revision": revision}
