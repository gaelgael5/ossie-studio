"""Cartographie applicative dans raw : formats des modules, des fonctions et du
fichier `.view` (epic « Cartographie applicative dans raw », enabler 6c97ad98).

Décisions de Gael (25 novembre 2026) :

- **module** = un fichier de projet (`.csproj`, `.sqlproj`, module Gradle ou
  Maven, `package.json`, `pyproject.toml`). Il est rangé dans raw **comme tout
  objet** : `<clé>.schema.yaml`, repéré par `x-kind: module`, ses métadonnées
  sous `x-module`. Il apparaît ainsi dans l'Explorateur, la recherche et la
  lecture MCP sans rien de plus ;
- **fonctions** : les méthodes d'une classe vont dans **`<clé>.extend.yaml`** à
  côté de son schéma ; les fonctions libres (Python, TypeScript, minimal API C#)
  dans le `.extend.yaml` du module. Écrit par le parseur, régénéré à chaque
  collecte ; le `<clé>.schema.yaml` reste du JSON Schema standard, **sans
  aucune méthode** ;
- **`.view`** : `<clé du module>.view.yaml`, la personnalisation de l'affichage
  (ancres des connecteurs pour commencer). Gael : « le fichier view est un
  fichier qui n'est pas entièrement régénéré. Dedans on y fait des ajouts ou des
  suppressions sur les objets qui arrivent ou les objets qui sont supprimés. »

**Appartenance** d'un objet à un module : elle n'est pas écrite (le module serait
réécrit à chaque classe ajoutée). C'est une règle, appliquée à la lecture : le
fichier de projet **le plus proche en remontant** l'arborescence du fichier
d'origine de l'objet (`module_of`, la règle du compilateur .NET).

Chaque forme a son JSON Schema de validation (`formats/*.schema.json`) et
s'écrit avec l'écrivain déterministe des schémas (`dump_schema` : mêmes octets
pour le même contenu).
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Iterable, Mapping
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ossie_studio.parsers.conventions import (
    SCHEMA_SUFFIX,
    ConventionError,
    SchemaReadError,
    dump_schema,
    load_schema,
    schema_file,
)

MODULE_KIND = "module"
MODULE_KEY = "x-module"
EXTEND_SUFFIX = ".extend.yaml"
VIEW_SUFFIX = ".view.yaml"
MODULE_PREFIX = "module-"

NATURES = ("library", "web", "worker", "console", "test", "sql", "application")
ENTRY_KINDS = ("http", "task", "message")

# Ancre calculée par la vue (valeur par défaut d'un lien).
AUTO = "auto"
DEFAULT_LINK: dict[str, Any] = {"from": AUTO, "to": AUTO}

# Clés refusées dans un `<clé>.schema.yaml` : les fonctions vivent dans le
# `.extend.yaml`, jamais dans le schéma standard.
FUNCTION_KEYS = frozenset({"x-methods", "x-functions", "x-entry-point", "x-entry-points"})

_FORMATS = Path(__file__).parent / "formats"
_SLUG = re.compile(r"[^a-zA-Z0-9]+")


@cache
def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((_FORMATS / f"{name}.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _check(name: str, rule: str, document: Any) -> None:
    errors = sorted(_validator(name).iter_errors(document), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.absolute_path) or "."
        raise ConventionError(rule, f"{where}: {first.message}")


# --- Noms de fichiers ------------------------------------------------------------------------


def module_key(project: str) -> str:
    """Clé stable d'un module, tirée du chemin de son fichier de projet (unique
    dans la source, même si deux projets portent le même nom) :
    `src/Boutique.Api/Boutique.Api.csproj` → `module-src-boutique-api-boutique-api`."""
    folder, base = posixpath.split(project)
    stem = base.rsplit(".", 1)[0] if "." in base else base
    path = f"{folder}/{stem}" if folder else stem
    return MODULE_PREFIX + (_SLUG.sub("-", path).strip("-").lower() or "projet")


def extend_file(key: str) -> str:
    return f"{key}{EXTEND_SUFFIX}"


def view_file(key: str) -> str:
    return f"{key}{VIEW_SUFFIX}"


def _is_top_level(name: str, suffix: str) -> bool:
    return name.endswith(suffix) and "/" not in name


def is_extend_name(name: str) -> bool:
    return _is_top_level(name, EXTEND_SUFFIX)


def is_view_name(name: str) -> bool:
    return _is_top_level(name, VIEW_SUFFIX)


def key_of(name: str) -> str:
    """Clé d'un fichier de raw, quel que soit son suffixe (schéma, fonctions, vue)."""
    for suffix in (SCHEMA_SUFFIX, EXTEND_SUFFIX, VIEW_SUFFIX):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


# --- Objet module ----------------------------------------------------------------------------


def is_module(schema: Any) -> bool:
    return isinstance(schema, dict) and schema.get("x-kind") == MODULE_KIND


def module_schema(
    *,
    project: str,
    name: str,
    language: str,
    nature: str,
    targets: Iterable[str] = (),
    dependencies: Iterable[str] = (),
    known_projects: Iterable[str] = (),
    description: str = "",
) -> dict[str, Any]:
    """L'objet module d'un fichier de projet. `dependencies` : chemins des
    fichiers de projet référencés ; ceux qui sont dans `known_projects` (la
    source) sont reliés à leur module, les autres restent non résolus."""
    known = set(known_projects)
    deps = []
    for dep in sorted(set(dependencies)):
        entry: dict[str, Any] = {"project": dep}
        if dep in known:
            entry["module"] = schema_file(module_key(dep))
        deps.append(entry)
    meta: dict[str, Any] = {"project": project, "nature": nature, "dependencies": deps}
    target_list = sorted(set(targets))
    if target_list:
        meta["targets"] = target_list
    schema: dict[str, Any] = {
        "$id": schema_file(module_key(project)),
        "title": name,
        "x-kind": MODULE_KIND,
        "x-language": language,
        "x-source": [{"file": project}],
        MODULE_KEY: meta,
    }
    if description:
        schema["description"] = description
    return schema


def validate_module(schema: Any) -> None:
    """Lève ConventionError(« module_invalid ») si l'objet module est mal formé."""
    _check("module", "module_invalid", schema)


def module_links(schema: Mapping[str, Any]) -> list[str]:
    """Les modules d'arrivée (`$id`) des dépendances résolues d'un module."""
    meta = schema.get(MODULE_KEY)
    deps = meta.get("dependencies") if isinstance(meta, dict) else None
    targets = {d.get("module") for d in deps or [] if isinstance(d, dict)}
    return sorted(t for t in targets if isinstance(t, str))


def module_of(file: str, projects: Mapping[str, str]) -> str | None:
    """Le module d'un fichier d'origine : celui dont le dossier du fichier de
    projet est le plus proche en remontant. `projects` : clé de module → chemin
    du fichier de projet. None hors de tout module."""
    best: tuple[int, str] | None = None
    for key, project in projects.items():
        folder = posixpath.dirname(project)
        inside = not folder or file.startswith(f"{folder}/")
        if inside and (best is None or len(folder) > best[0]):
            best = (len(folder), key)
    return best[1] if best else None


# --- Fonctions (<clé>.extend.yaml) --------------------------------------------------------


def _signature(function: Mapping[str, Any]) -> tuple[str, tuple[str, ...], int]:
    params = tuple(str(p.get("type") or "") for p in function.get("parameters") or [])
    source = function.get("source")
    line = source.get("line", 0) if isinstance(source, dict) else 0
    return (str(function.get("name", "")), params, int(line or 0))


def normalize_functions(document: Mapping[str, Any]) -> dict[str, Any]:
    """Ordre déterministe (nom, types des paramètres, ligne) ; listes vides
    retirées. L'ordre de lecture des fichiers ne change donc pas les octets."""
    out: dict[str, Any] = {}
    for key, value in document.items():
        if key in ("methods", "functions"):
            if value:
                out[key] = sorted(value, key=_signature)
        elif value is not None:
            out[key] = value
    return out


def validate_functions(document: Any) -> None:
    """Lève ConventionError(« functions_invalid ») si le `.extend.yaml` est mal formé."""
    _check("functions", "functions_invalid", document)


def dump_functions(document: Mapping[str, Any]) -> str:
    """Le texte d'un `<clé>.extend.yaml` : validé, ordonné, déterministe."""
    normalized = normalize_functions(document)
    validate_functions(normalized)
    return dump_schema(normalized)


# --- Vue d'un module (<clé>.view.yaml) ----------------------------------------------------


def validate_view(document: Any) -> None:
    """Lève ConventionError(« view_invalid ») si le `.view.yaml` est mal formé."""
    _check("view", "view_invalid", document)


def _read_view(text: str | None) -> dict[str, Any] | None:
    """Le contenu d'un `.view.yaml` ; `{"links": {}}` s'il est absent ; None s'il
    est illisible ou invalide (on ne sait alors pas le mettre à jour en partie)."""
    if text is None:
        return {"links": {}}
    try:
        data = load_schema(text)
        validate_view(data)
    except (SchemaReadError, ConventionError):
        return None
    return data if isinstance(data, dict) else None


def view_links(text: str | None, links: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Les ancres de chaque lien d'un module pour l'affichage : celles du
    `.view.yaml`, la valeur par défaut pour un lien sans entrée ou un fichier
    absent ou illisible (il n'empêche jamais l'affichage)."""
    stored = (_read_view(text) or {"links": {}})["links"]
    return {target: dict(stored.get(target) or DEFAULT_LINK) for target in sorted(set(links))}


def sync_view(text: str | None, links: Iterable[str]) -> str | None:
    """Met à jour un `.view.yaml` **par ajouts et suppressions**, jamais en le
    régénérant : entrées des liens disparus retirées, entrées des liens nouveaux
    ajoutées avec la valeur par défaut, les autres gardées telles quelles.

    Rend le nouveau texte, ou None s'il n'y a rien à écrire (aucun changement, ou
    fichier illisible : on ne l'écrase pas, il reste à corriger à la main)."""
    current = _read_view(text)
    if current is None:
        return None
    wanted = set(links)
    stored: dict[str, Any] = current["links"]
    kept = {target: entry for target, entry in stored.items() if target in wanted}
    for target in wanted - set(kept):
        kept[target] = dict(DEFAULT_LINK)
    updated = {**current, "links": kept}
    new_text = dump_schema(updated)
    if text is not None and (updated == current or new_text == text):
        return None
    return new_text
