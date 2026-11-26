"""Primitives MCP de la cartographie applicative (epic « Cartographie applicative
dans raw », ticket 72ce32b3). Gael : « il faut que la couche de primitives puisse
également explorer les modules et les fonctions. »

Un agent voit ce que montre la vue « Application » de l'Explorateur raw :

- `list_modules` : les modules d'une source (nature, cibles, langage, fichier de
  projet, compteurs, dépendances dans les deux sens) ;
- `get_module` : un module, ses objets, ses fonctions libres, ses points d'entrée ;
- `get_functions` : les méthodes d'un objet raw (signature, visibilité, `static`,
  point d'entrée) ;
- `list_entry_points` : les points d'entrée d'une source ou d'un module, filtrables
  par état au contrat — « quelles routes du code manquent au contrat ? ».

Comme `get_reference_graph` : la même vérité que l'écran (l'index de
l'Explorateur, sans relire la branche ; seuls les `.extend.yaml` utiles sont lus
dans la vue), vue `draft` ou `published`, capacité `sources:read`, lecture
seule ; une réponse trop longue est tronquée, et le dit.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ossie_studio.mcp_surface.branch import (
    BranchView,
    ObjectArgs,
    SourceArgs,
    open_branch,
    source_dir,
)
from ossie_studio.mcp_surface.core import PrimitiveContext, PrimitiveError
from ossie_studio.parsers.application import extend_file, key_of
from ossie_studio.parsers.conventions import SchemaReadError, load_schema
from ossie_studio.sources.curation import is_schema_file
from ossie_studio.sources.explorer import ExplorerObject, SourceEntries
from ossie_studio.sources.explorer_application import (
    application_view,
    members_by_module,
    module_detail,
    module_files,
)
from ossie_studio.sources.explorer_service import ensure_index, space_entries
from ossie_studio.spaces.workdir import WorkdirError

# Éléments rendus au plus par liste : au-delà, la réponse est tronquée (et le dit).
LIMIT = 200

ContractStatus = Literal["linked", "absent"]


class ModuleArgs(SourceArgs):
    module: str = Field(
        description="Module: its path (from list_modules, 'module-….schema.yaml') or its title."
    )


class EntryPointsArgs(SourceArgs):
    module: str | None = Field(
        default=None, description="Restrict to one module (path or title); omit for the source."
    )
    contract_status: ContractStatus | None = Field(
        default=None,
        description=(
            "'absent' = only entry points missing from the source's OpenAPI contract; "
            "'linked' = only those matched to an operation. Entry points not yet compared "
            "to a contract match neither."
        ),
    )


async def _entries(ctx: PrimitiveContext, args: SourceArgs) -> tuple[BranchView, SourceEntries]:
    source_dir(args.source)  # slug invalide → erreur nommée
    bv = await open_branch(ctx, args)
    try:
        key, signature = await ensure_index(
            ctx.conn, bv.workdir, bv.space, bv.branch.id, bv.branch.git_ref, bv.username, bv.token
        )
    except WorkdirError as exc:
        raise PrimitiveError(exc.code) from None
    # En base pour une personne, en mémoire pour une clé d'API (vue publiée).
    sources = await space_entries(ctx.conn, key, signature)
    entries = next((e for e in sources if e.slug == args.source), None)
    if entries is None:
        raise PrimitiveError("source_not_found", source=args.source)
    return bv, entries


async def _read_raw(bv: BranchView, source: str, names: list[str]) -> dict[str, str | None]:
    """Les fichiers de `raw/` demandés, lus en une fois dans la vue (None : absent)."""
    base = f"{source_dir(source)}/raw"

    def read(root: Any) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        for name in names:
            file = root / base / name
            out[name] = file.read_text(encoding="utf-8") if file.is_file() else None
        return out

    result: dict[str, str | None] = await bv.read(read)
    return result


def _truncate(items: list[Any]) -> tuple[list[Any], bool]:
    return items[:LIMIT], len(items) > LIMIT


def _module_ref(module: dict[str, Any]) -> dict[str, Any]:
    return {"path": module["file"], "title": module["title"]}


def _find_module(view: dict[str, Any], wanted: str) -> dict[str, Any]:
    modules: list[dict[str, Any]] = view["modules"]
    found = next((m for m in modules if m["file"] == wanted), None) or next(
        (m for m in modules if m["title"].casefold() == wanted.casefold()), None
    )
    if found is None:
        raise PrimitiveError("module_not_found", module=wanted)
    return found


async def list_modules(ctx: PrimitiveContext, args: SourceArgs) -> dict[str, Any]:
    bv, entries = await _entries(ctx, args)
    view = application_view(entries)
    by_id = {m["id"]: m for m in view["modules"]}
    modules = [
        {
            **_module_ref(m),
            "nature": m["nature"],
            "targets": m["targets"],
            "language": m["language"],
            "project": m["project"],
            "counts": {
                "classes": m["counts"]["classes"],
                "entry_points": m["counts"]["entry_points"],
                "public_methods": m["counts"]["methods"],
            },
            "depends_on": [
                _module_ref(by_id[d["id"]])
                if d["id"] in by_id
                else {"project": d["project"], "external": True}
                for d in m["dependencies"]
            ],
            "used_by": [_module_ref(by_id[d]) for d in m["dependents"] if d in by_id],
        }
        for m in view["modules"]
    ]
    shown, truncated = _truncate(modules)
    return {"view": bv.view, "source": args.source, "truncated": truncated, "modules": shown}


def _load(text: str | None) -> dict[str, Any]:
    if text is None:
        return {}
    try:
        data = load_schema(text)
    except SchemaReadError:
        return {}
    return data if isinstance(data, dict) else {}


def _function(function: dict[str, Any]) -> dict[str, Any]:
    params = [
        {"name": p.get("name"), "type": p.get("type")}
        for p in function.get("parameters") or []
        if isinstance(p, dict)
    ]
    out: dict[str, Any] = {
        "name": function.get("name"),
        "visibility": function.get("visibility"),
        "parameters": params,
        "returns": (function.get("returns") or {}).get("type"),
        "static": bool(function.get("static")),
        "async": bool(function.get("async")),
    }
    point = function.get("entryPoint")
    if isinstance(point, dict):
        out["entry_point"] = _point(point)
    return out


def _point(point: dict[str, Any]) -> dict[str, Any]:
    contract = point.get("contract") if isinstance(point.get("contract"), dict) else None
    return {
        "kind": point.get("kind"),
        "verb": point.get("verb"),
        "route": point.get("route"),
        "contract_status": contract.get("status") if contract else "not_compared",
        "contract_operations": (contract or {}).get("operations") or [],
    }


async def get_module(ctx: PrimitiveContext, args: ModuleArgs) -> dict[str, Any]:
    bv, entries = await _entries(ctx, args)
    module = _find_module(application_view(entries), args.module)
    texts = await _read_raw(bv, args.source, module_files(entries, module["file"]))
    detail = module_detail(entries, module["file"], texts.get)
    if detail is None:
        raise PrimitiveError("module_not_found", module=args.module)
    members: list[ExplorerObject] = members_by_module(entries)[module["id"]]
    objects, objects_truncated = _truncate(
        [{"path": o.file, "title": o.title, "kind": o.kind} for o in members]
    )
    own = _load(texts.get(extend_file(key_of(module["file"]))))
    functions = [_function(f) for f in own.get("functions") or [] if isinstance(f, dict)]
    points, points_truncated = _truncate([_entry(p, module) for p in detail["entry_points"]])
    return {
        "view": bv.view,
        "source": args.source,
        "module": {
            **_module_ref(module),
            "nature": module["nature"],
            "targets": module["targets"],
            "language": module["language"],
            "project": module["project"],
        },
        "objects": objects,
        "free_functions": functions[:LIMIT],
        "entry_points": points,
        "truncated": objects_truncated or points_truncated or len(functions) > LIMIT,
    }


def _entry(point: dict[str, Any], module: dict[str, Any]) -> dict[str, Any]:
    contract = point.get("contract")
    return {
        "module": module["title"],
        "module_path": module["file"],
        "kind": point.get("kind"),
        "verb": point.get("verb"),
        "route": point.get("route"),
        "method": point.get("method"),
        "owner": point.get("owner"),
        "owner_path": point.get("owner_file"),
        "contract_status": contract["status"] if contract else "not_compared",
        "contract_operations": (contract or {}).get("operations") or [],
    }


async def get_functions(ctx: PrimitiveContext, args: ObjectArgs) -> dict[str, Any]:
    if not is_schema_file(args.path):
        raise PrimitiveError("schema_not_found", path=args.path)
    bv, entries = await _entries(ctx, args)
    obj = next((o for o in entries.objects if o.file == args.path), None)
    if obj is None:
        raise PrimitiveError("schema_not_found", source=args.source, path=args.path)
    name = extend_file(key_of(args.path))
    doc = _load((await _read_raw(bv, args.source, [name]))[name])
    methods = [_function(m) for m in doc.get("methods") or [] if isinstance(m, dict)]
    shown, truncated = _truncate(methods)
    out: dict[str, Any] = {
        "view": bv.view,
        "source": args.source,
        "path": args.path,
        "title": obj.title,
        "kind": obj.kind,
        "truncated": truncated,
        "methods": shown,
    }
    if isinstance(doc.get("entryPoint"), dict):
        out["entry_point"] = _point(doc["entryPoint"])
    return out


async def list_entry_points(ctx: PrimitiveContext, args: EntryPointsArgs) -> dict[str, Any]:
    bv, entries = await _entries(ctx, args)
    view = application_view(entries)
    modules = [_find_module(view, args.module)] if args.module else view["modules"]
    names = sorted({n for m in modules for n in module_files(entries, m["file"])})
    texts = await _read_raw(bv, args.source, names)
    points: list[dict[str, Any]] = []
    for module in modules:
        detail = module_detail(entries, module["file"], texts.get)
        points += [_entry(p, module) for p in (detail or {}).get("entry_points", [])]
    if args.contract_status is not None:
        points = [p for p in points if p["contract_status"] == args.contract_status]
    shown, truncated = _truncate(points)
    return {
        "view": bv.view,
        "source": args.source,
        "contract_status": args.contract_status,
        "truncated": truncated,
        "entry_points": shown,
    }
