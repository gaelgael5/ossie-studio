"""Vue « Application » de l'Explorateur raw (epic « Cartographie applicative dans
raw », ticket 0fa620be) : les modules d'une source, leurs dépendances dans les deux
sens, leurs compteurs, et l'aperçu d'un module (points d'entrée, méthodes
publiques par classe).

La vue se calcule sur l'**index** de l'Explorateur (`SourceEntries`), sans relire
la branche ; seul l'aperçu d'un module lit les `.extend.yaml` de ses objets.
L'appartenance d'un objet à un module est la règle du projet le plus proche
(`parsers.application.module_of`), appliquée au fichier d'origine de l'objet.
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable, Mapping
from typing import Any

from ossie_studio.parsers.application import extend_file, key_of, module_of
from ossie_studio.parsers.conventions import SchemaReadError, load_schema
from ossie_studio.sources.explorer import ExplorerObject, SourceEntries

SQL_NATURE = "sql"


def _modules(entries: SourceEntries) -> dict[str, ExplorerObject]:
    """Les objets module de la source, par `$id`."""
    return {o.schema_id or o.file: o for o in entries.objects if o.module is not None}


def members_by_module(entries: SourceEntries) -> dict[str, list[ExplorerObject]]:
    """Les objets (hors modules) qui appartiennent à chaque module (`$id`), par la
    règle du projet le plus proche appliquée à leur fichier d'origine."""
    modules = _modules(entries)
    out: dict[str, list[ExplorerObject]] = {key: [] for key in modules}
    projects = {key: m.module.project for key, m in modules.items() if m.module}
    for obj in entries.objects:
        if obj.module is not None or not obj.origin:
            continue
        key = module_of(obj.origin, projects)
        if key is not None:
            out[key].append(obj)
    return out


def _counts(module: ExplorerObject, members: list[ExplorerObject]) -> dict[str, int]:
    sql = module.module is not None and module.module.nature == SQL_NATURE
    own = module.functions
    return {
        # Projet SQL : ses objets (tables, vues) ; code : ses classes.
        "classes": sum(1 for m in members if sql or m.visibility),
        "entry_points": own.entry_points + sum(m.functions.entry_points for m in members),
        "http": own.http + sum(m.functions.http for m in members),
        "methods": sum(m.functions.methods for m in members),
        "functions": own.functions,
    }


def application_view(entries: SourceEntries) -> dict[str, Any]:
    """Les modules d'une source pour la vue « Application » (blocs, flèches,
    compteurs) et ses contrats OpenAPI (bloc à part)."""
    modules = _modules(entries)
    members = members_by_module(entries)
    dependents: dict[str, list[str]] = {key: [] for key in modules}
    for key, mod in modules.items():
        for _project, target in mod.module.dependencies if mod.module else ():
            if target in dependents and key not in dependents[target]:
                dependents[target].append(key)
    payload = []
    for key, mod in sorted(modules.items(), key=lambda kv: kv[1].title.casefold()):
        facts = mod.module
        assert facts is not None
        payload.append(
            {
                "id": key,
                "file": mod.file,
                "title": mod.title,
                "language": facts.language,
                "nature": facts.nature,
                "targets": list(facts.targets),
                "project": facts.project,
                # Le dossier du module dans l'arborescence d'origine (« n classes »).
                "folder": posixpath.dirname(facts.project),
                "dependencies": [
                    {"id": target or None, "project": project}
                    for project, target in facts.dependencies
                ],
                "dependents": sorted(dependents[key]),
                "counts": _counts(mod, members[key]),
            }
        )
    return {
        "modules": payload,
        "contracts": [
            {"path": c.path, "title": c.title, "operations": c.operations}
            for c in entries.contracts
        ],
    }


def _load(text: str | None) -> dict[str, Any]:
    if text is None:
        return {}
    try:
        data = load_schema(text)
    except SchemaReadError:
        return {}
    return data if isinstance(data, dict) else {}


def _signature(function: Mapping[str, Any]) -> str:
    params = ", ".join(
        f"{p.get('name', '')}: {p.get('type', '?')}" if p.get("type") else str(p.get("name", ""))
        for p in function.get("parameters") or []
        if isinstance(p, dict)
    )
    returns = function.get("returns")
    result = f": {returns['type']}" if isinstance(returns, dict) and returns.get("type") else ""
    return f"{function.get('name', '')}({params}){result}"


def _contract(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    """L'état du point d'entrée au contrat (None tant qu'il n'a pas été comparé)."""
    contract = entry.get("contract")
    if not isinstance(contract, dict):
        return None
    return {"status": contract.get("status"), "operations": contract.get("operations") or []}


def module_files(entries: SourceEntries, module_file: str) -> list[str]:
    """Les `.extend.yaml` à lire pour l'aperçu d'un module : le sien et ceux de
    ses objets."""
    modules = _modules(entries)
    module = next((m for m in modules.values() if m.file == module_file), None)
    if module is None:
        return []
    key = module.schema_id or module.file
    own = [extend_file(key_of(module.file))]
    return own + [extend_file(key_of(o.file)) for o in members_by_module(entries)[key]]


def module_detail(
    entries: SourceEntries, module_file: str, read: Callable[[str], str | None]
) -> dict[str, Any] | None:
    """L'aperçu d'un module : fichier de projet, nature, cibles, dépendances dans
    les deux sens, points d'entrée (verbe, route, méthode, état au contrat) et
    méthodes publiques groupées par classe. `read` : contenu d'un fichier de
    `raw/` (None s'il n'existe pas). None si le module est inconnu."""
    view = application_view(entries)
    module = next((m for m in view["modules"] if m["file"] == module_file), None)
    if module is None:
        return None
    titles = {m["id"]: m["title"] for m in view["modules"]}
    members = members_by_module(entries)[module["id"]]
    entry_points: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    for function in _load(read(extend_file(key_of(module_file)))).get("functions") or []:
        point = function.get("entryPoint") if isinstance(function, dict) else None
        if isinstance(point, dict):
            entry = {**_point(point), "method": function.get("name"), "owner": None}
            entry["source"] = function.get("source")
            entry_points.append(entry)
    for obj in sorted(members, key=lambda o: o.title.casefold()):
        doc = _load(read(extend_file(key_of(obj.file))))
        if not doc:
            continue
        own_point = doc.get("entryPoint")
        if isinstance(own_point, dict):
            entry_points.append(_owned(own_point, None, obj))
        methods = []
        for method in doc.get("methods") or []:
            if not isinstance(method, dict) or method.get("visibility") != "public":
                continue
            point = method.get("entryPoint")
            is_entry = isinstance(point, dict)
            methods.append(
                {"name": method.get("name"), "signature": _signature(method), "entry": is_entry}
            )
            if isinstance(point, dict):
                entry_points.append(_owned(point, method.get("name"), obj))
        if methods:
            classes.append({"title": obj.title, "file": obj.file, "methods": methods})
    return {
        **module,
        "dependencies": [
            {**d, "title": titles.get(d["id"]) if d["id"] else None} for d in module["dependencies"]
        ],
        "dependents": [{"id": d, "title": titles.get(d, d)} for d in module["dependents"]],
        "entry_points": entry_points,
        "classes": classes,
    }


def _owned(point: Mapping[str, Any], method: Any, obj: ExplorerObject) -> dict[str, Any]:
    """Un point d'entrée porté par une classe (ou une de ses méthodes)."""
    return {**_point(point), "method": method, "owner": obj.title, "owner_file": obj.file}


def _point(point: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "kind": point.get("kind"),
        "verb": point.get("verb"),
        "route": point.get("route"),
        "contract": _contract(point),
    }
