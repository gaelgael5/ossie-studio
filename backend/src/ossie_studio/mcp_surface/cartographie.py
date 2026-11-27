"""Primitives MCP de lecture de la cartographie (epic « Cartographie — types et
objets », ticket 41bf680e) : trois primitives, pas plus (Gael : « n'explosons
pas la liste des API ») ; et deux pour les modèles (epic « Cartographie — modèles
et vues », ticket be89037a).

- `list_carto_types` : les types de l'espace (et, sur demande, tout le catalogue
  ArchiMate 3.2) ;
- `list_carto_objects` : les objets et relations, filtrés par type (de l'espace ou
  ArchiMate), nature et nom ou clé, paginés ;
- `get_carto_object` : un objet ou une relation par sa clé, avec ses relations
  entrantes et sortantes pour explorer de proche en proche, et les modèles qui le
  retiennent ;
- `list_carto_models` : les modèles (diagrammes), filtrables par objet retenu ;
- `get_carto_model` : un modèle, ses objets et relations retenus, ses vues et leurs
  nœuds, sans le dessin (aucune coordonnée).

Une relation est un objet : les mêmes primitives servent aux deux, la `nature`
(`object` ou `relation`) les distingue. Même vérité que l'écran (lecture de
`cartographie/` par `cartographie.space`), vues brouillon (connexion OAuth) et
publiée (clé d'API ; toujours). Lecture seule.

Ticket 5d0b3e7d : chaque type, objet, relation et modèle rendu porte son **chemin**
dans l'espace ; un objet et un modèle s'adressent aussi par ce chemin ; et
`get_carto_object` / `get_carto_model` rendent la **version** du document —
l'empreinte Git du fichier dans la branche lue (même calcul que la publication),
que les primitives d'écriture exigent pour remplacer, modifier ou supprimer.
L'empreinte plutôt qu'un numéro : elle change dès que le contenu change, quelle
que soit la vue (publiée ou brouillon), sans rien stocker de plus.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from ossie_studio.cartographie.catalog import ArchimateType, Catalog, catalog
from ossie_studio.cartographie.diagram import (
    MODEL_SUFFIX,
    MODELS_DIR,
    DiagramRead,
    Node,
    diagram_path,
    graphic_path,
    list_diagrams,
    models_retaining,
    read_diagram,
)
from ossie_studio.cartographie.errors import CartographieError
from ossie_studio.cartographie.model import CartoObject, type_path
from ossie_studio.cartographie.space import Cartographie, read_cartographie
from ossie_studio.drafts.store import git_blob_sha
from ossie_studio.mcp_surface.branch import BranchArgs, BranchView, open_branch
from ossie_studio.mcp_surface.core import PrimitiveContext, PrimitiveError

Nature = Literal["object", "relation"]
MAX_OBJECTS = 100


class CartoTypesArgs(BranchArgs):
    catalogue: bool = Field(
        default=False,
        description=(
            "Also return every ArchiMate 3.2 type of the catalogue (id, French label, layer, "
            "nature), including those no type of the space uses yet."
        ),
    )


class CartoObjectsArgs(BranchArgs):
    type: str | None = Field(
        default=None,
        description=(
            "A type of the space (slug, e.g. 'application') or an ArchiMate type (e.g. "
            "'ApplicationComponent': all objects of the space types based on it)."
        ),
    )
    nature: Nature | None = Field(
        default=None, description="'object' or 'relation' (a relationship is an object too)."
    )
    name: str | None = Field(
        default=None,
        description="Part of the name or of the key; case and accents are ignored.",
    )
    limit: int = Field(default=50, ge=1, le=MAX_OBJECTS, description="Maximum objects returned.")
    offset: int = Field(default=0, ge=0, description="Objects to skip (for the next page).")


class CartoObjectArgs(BranchArgs):
    key: str | None = Field(
        default=None,
        description="Functional key of the object or relationship, e.g. 'APP-SHOP'.",
    )
    type: str | None = Field(
        default=None,
        description="With key: the type of the space the object must belong to (slug).",
    )
    path: str | None = Field(
        default=None,
        description=(
            "Path of the object in the space, e.g. 'cartographie/objets/application/"
            "app-shop.yaml' (as the lists give it). Give path or key."
        ),
    )


class CartoModelsArgs(BranchArgs):
    contains: str | None = Field(
        default=None,
        description=(
            "Only the models that keep this object or relationship (its key, e.g. 'APP-SHOP')."
        ),
    )


class CartoModelArgs(BranchArgs):
    slug: str | None = Field(
        default=None, description="Slug of the model, as list_carto_models gives it."
    )
    path: str | None = Field(
        default=None,
        description=(
            "Path of the model file, e.g. 'cartographie/modeles/sales.modele.yaml'. "
            "Give path or slug."
        ),
    )


def _fold(text: str) -> str:
    """Sans casse ni accents : « Écrit » et « ecrit » se rejoignent."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _clean(path: str) -> str:
    return path.strip().lstrip("/")


def _version(root: Path, path: str) -> str | None:
    """L'empreinte Git du fichier dans la copie lue ; absent → None."""
    file = root / path
    return git_blob_sha(file.read_bytes()) if file.is_file() else None


def _nature(base: ArchimateType | None) -> Nature:
    return "relation" if base is not None and base.is_relation else "object"


async def _read(ctx: PrimitiveContext, args: BranchArgs) -> tuple[BranchView, Cartographie]:
    bv = await open_branch(ctx, args)
    space: Cartographie = await bv.read(read_cartographie)
    return bv, space


def _end(space: Cartographie, key: str | None) -> dict[str, Any] | None:
    if key is None:
        return None
    found = space.objects.get(key)
    if found is None:
        return {"key": key, "name": None, "type": None, "exists": False}
    return {
        "key": key,
        "name": found[1].name,
        "type": found[1].type,
        "exists": True,
        "path": found[0],
    }


def _summary(
    space: Cartographie, cat: Catalog, bv: BranchView, path: str, obj: CartoObject
) -> dict[str, Any]:
    ctype = space.types.get(obj.type)
    base = cat.get(ctype.archimate) if ctype else None
    out: dict[str, Any] = {
        "key": obj.key,
        "name": obj.name,
        "type": obj.type,
        "archimate": ctype.archimate if ctype else None,
        "nature": _nature(base),
        "path": path,
    }
    if obj.is_relation:
        out["source"] = _end(space, obj.source)
        out["target"] = _end(space, obj.target)
    if bv.unpublished(path):
        out["unpublished"] = True
    return out


async def list_carto_types(ctx: PrimitiveContext, args: CartoTypesArgs) -> dict[str, Any]:
    bv, space = await _read(ctx, args)
    cat = catalog()
    counts: dict[str, int] = {}
    for _path, obj in space.objects.values():
        counts[obj.type] = counts.get(obj.type, 0) + 1
    types = []
    for ctype in sorted(space.types.values(), key=lambda t: (t.title.casefold(), t.slug)):
        base = cat.get(ctype.archimate)
        types.append(
            {
                "slug": ctype.slug,
                "name": ctype.title,
                "archimate": ctype.archimate,
                "layer": base.layer if base and not base.is_relation else None,
                "nature": _nature(base),
                "objects": counts.get(ctype.slug, 0),
                "path": type_path(ctype.slug),
            }
        )
    out: dict[str, Any] = {"view": bv.view, "types": types}
    if args.catalogue:
        out["catalogue"] = [
            {
                "archimate": t.name,
                "label": t.label,
                "layer": t.layer or None,
                "nature": _nature(t),
            }
            for t in cat.types.values()
        ]
    return out


async def list_carto_objects(ctx: PrimitiveContext, args: CartoObjectsArgs) -> dict[str, Any]:
    bv, space = await _read(ctx, args)
    cat = catalog()
    wanted = args.type
    if wanted and wanted not in space.types and cat.get(wanted) is None:
        raise PrimitiveError("carto_type_not_found", type=wanted)
    needle = _fold(args.name.strip()) if args.name else ""
    matching = []
    for path, obj in space.objects.values():
        ctype = space.types.get(obj.type)
        archimate = ctype.archimate if ctype else None
        if wanted and obj.type != wanted and archimate != wanted:
            continue
        if args.nature and _nature(cat.get(archimate or "")) != args.nature:
            continue
        if needle and needle not in _fold(f"{obj.name} {obj.key}"):
            continue
        matching.append((path, obj))
    matching.sort(key=lambda po: (po[1].name.casefold(), po[1].key))
    page = matching[args.offset : args.offset + args.limit]
    return {
        "view": bv.view,
        "total": len(matching),
        "has_more": args.offset + len(page) < len(matching),
        "objects": [_summary(space, cat, bv, path, obj) for path, obj in page],
    }


def _labels(base: ArchimateType | None, properties: dict[str, Any]) -> dict[str, str]:
    """Le libellé français des attributs ArchiMate renseignés (« Write » → « écriture »)."""
    out: dict[str, str] = {}
    for attribute in base.attributes if base else ():
        label = attribute.values.get(str(properties.get(attribute.name)))
        if label:
            out[attribute.name] = label
    return out


def _side(space: Cartographie, relation_key: str, direction: str) -> dict[str, Any]:
    path, relation = space.objects[relation_key]
    rtype = space.types.get(relation.type)
    other = relation.target if direction == "outgoing" else relation.source
    return {
        "key": relation.key,
        "name": relation.name,
        "type": relation.type,
        "archimate": rtype.archimate if rtype else None,
        "direction": direction,
        "other": _end(space, other),
        "path": path,
    }


def _find_object(space: Cartographie, args: CartoObjectArgs) -> tuple[str, CartoObject]:
    """Par chemin, par clé, ou par type + clé : le même objet. Une forme qui ne
    désigne rien, ou deux formes qui se contredisent → introuvable."""
    if not args.key and not args.path:
        raise PrimitiveError("invalid_arguments", fields=["key", "path"])
    found: tuple[str, CartoObject] | None = None
    if args.path:
        wanted = _clean(args.path)
        found = next((po for po in space.objects.values() if po[0] == wanted), None)
        if found is not None and args.key and found[1].key != args.key:
            found = None
    else:
        found = space.objects.get(args.key or "")
    if found is not None and args.type and found[1].type != args.type:
        found = None
    if found is None:
        raise PrimitiveError(
            "carto_object_not_found",
            **{k: v for k, v in (("key", args.key), ("type", args.type), ("path", args.path)) if v},
        )
    return found


async def get_carto_object(ctx: PrimitiveContext, args: CartoObjectArgs) -> dict[str, Any]:
    bv, space = await _read(ctx, args)
    path, obj = _find_object(space, args)
    cat = catalog()
    ctype = space.types.get(obj.type)
    base = cat.get(ctype.archimate) if ctype else None
    users = space.users(obj.key)
    models, version = await bv.read(
        lambda root: (models_retaining(root, obj.key), _version(root, path))
    )
    out: dict[str, Any] = {
        "view": bv.view,
        **_summary(space, cat, bv, path, obj),
        "documentation": obj.documentation,
        "properties": obj.properties,
        "property_labels": _labels(base, obj.properties),
        "relations": [
            *(_side(space, k, "outgoing") for k in users if space.objects[k][1].source == obj.key),
            *(_side(space, k, "incoming") for k in users if space.objects[k][1].target == obj.key),
        ],
        "models": [
            {"slug": slug, "name": name, "path": diagram_path(slug)} for slug, name in models
        ],
        "path": path,
        "version": version,
    }
    return out


# --- Modèles (ticket be89037a) : ce qu'ils montrent, jamais le dessin ----------------------


def _read_models(root: Any) -> tuple[Cartographie, list[DiagramRead]]:
    space = read_cartographie(root)
    return space, [read_diagram(root, slug, space) for slug in list_diagrams(root)]


def _model_unpublished(bv: BranchView, read: DiagramRead) -> bool:
    return bv.unpublished(read.path) or bv.unpublished(graphic_path(read.slug))


async def list_carto_models(ctx: PrimitiveContext, args: CartoModelsArgs) -> dict[str, Any]:
    bv = await open_branch(ctx, args)
    space, reads = await bv.read(_read_models)
    wanted = args.contains.strip() if args.contains else ""
    if wanted and wanted not in space.objects:
        raise PrimitiveError("carto_object_not_found", key=wanted)
    models = []
    for read in reads:
        diagram = read.diagram
        if diagram is None:  # illisible : nommé, sans contenu
            if not wanted:
                models.append(
                    {
                        "slug": read.slug,
                        "readable": False,
                        "problems": len(read.errors),
                        "path": read.path,
                    }
                )
            continue
        if wanted and wanted not in diagram.elements and wanted not in diagram.relationships:
            continue
        item: dict[str, Any] = {
            "slug": read.slug,
            "id": diagram.id,
            "name": diagram.name or diagram.id,
            "description": diagram.description,
            "objects": len(diagram.elements),
            "relations": len(diagram.relationships),
            "views": [
                {"id": v.id, "name": v.name, "viewpoint": v.viewpoint or None}
                for v in diagram.views
            ],
            "rules": diagram.rules,
            "problems": len(read.errors),
            "path": read.path,
        }
        if _model_unpublished(bv, read):
            item["unpublished"] = True
        models.append(item)
    return {"view": bv.view, "models": models}


def _node(space: Cartographie, node: Node) -> dict[str, Any]:
    out: dict[str, Any] = {"id": node.id}
    if node.element is not None:
        found = space.objects.get(node.element)
        out["element"] = node.element
        out["name"] = found[1].name if found else None
    elif node.note is not None:
        out["note"] = node.note
    else:
        out["frame"] = node.frame or ""
    if node.children:
        out["children"] = [_node(space, child) for child in node.children]
    return out


def _kept(space: Cartographie, cat: Catalog, key: str) -> dict[str, Any]:
    found = space.objects.get(key)
    if found is None:
        return {"key": key, "name": None, "type": None, "archimate": None, "exists": False}
    obj = found[1]
    ctype = space.types.get(obj.type)
    out: dict[str, Any] = {
        "key": key,
        "name": obj.name,
        "type": obj.type,
        "archimate": ctype.archimate if ctype else None,
        "exists": True,
    }
    out["path"] = found[0]
    if obj.is_relation:
        out["source"], out["target"] = obj.source, obj.target
    return out


def _model_slug(args: CartoModelArgs) -> str:
    """Le slug désigné par `slug` ou par `path` ; deux formes contradictoires ou
    un chemin hors des modèles → introuvable."""
    if not args.slug and not args.path:
        raise PrimitiveError("invalid_arguments", fields=["slug", "path"])
    slug = args.slug or ""
    if args.path:
        wanted = _clean(args.path)
        prefix = f"{MODELS_DIR}/"
        stem = wanted.removeprefix(prefix).removesuffix(MODEL_SUFFIX)
        valid = wanted.startswith(prefix) and wanted.endswith(MODEL_SUFFIX) and "/" not in stem
        if not valid or (args.slug and args.slug != stem):
            raise PrimitiveError(
                "carto_model_not_found",
                **{k: v for k, v in (("slug", args.slug), ("path", args.path)) if v},
            )
        slug = stem
    return slug


async def get_carto_model(ctx: PrimitiveContext, args: CartoModelArgs) -> dict[str, Any]:
    slug = _model_slug(args)
    bv = await open_branch(ctx, args)

    def read(root: Any) -> tuple[Cartographie, DiagramRead, str | None]:
        space = read_cartographie(root)
        found = read_diagram(root, slug, space)
        return space, found, _version(root, found.path)

    try:
        space, found, version = await bv.read(read)
    except CartographieError:
        raise PrimitiveError("carto_model_not_found", slug=slug) from None
    diagram = found.diagram
    if diagram is None:
        problems = [{"code": p.code, "detail": p.detail} for p in found.errors]
        raise PrimitiveError("carto_model_invalid", slug=slug, problems=problems)
    cat = catalog()
    out: dict[str, Any] = {
        "view": bv.view,
        "slug": found.slug,
        "id": diagram.id,
        "name": diagram.name or diagram.id,
        "description": diagram.description,
        "rules": diagram.rules,
        "objects": [_kept(space, cat, key) for key in diagram.elements],
        "relations": [_kept(space, cat, key) for key in diagram.relationships],
        "views": [
            {
                "id": view.id,
                "name": view.name,
                "viewpoint": view.viewpoint or None,
                "nodes": [_node(space, node) for node in view.nodes],
                "connections": [
                    {"id": c.id, "relationship": c.relationship, "from": c.source, "to": c.target}
                    for c in view.connections
                ],
            }
            for view in diagram.views
        ],
        "problems": [{"code": p.code, "detail": p.detail, "field": p.field} for p in found.errors],
        "path": found.path,
        "version": version,
        "rules_path": diagram.rules or None,
        "graphic_path": graphic_path(found.slug),
    }
    if _model_unpublished(bv, found):
        out["unpublished"] = True
    return out
