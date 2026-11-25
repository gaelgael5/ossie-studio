"""Modules TypeScript / JavaScript : chaque `package.json` devient un objet module
dans raw (epic « Cartographie applicative dans raw », ticket f619f4a7), comme les
`.csproj` pour C# et les projets Gradle / Maven.

Étape du **socle** (le `package.json` est lu comme du JSON, rien n'est exécuté) :

- un module par `package.json`, hors `node_modules/` ; la racine d'un monorepo
  (`workspaces` npm / yarn, `pnpm-workspace.yaml`, `lerna.json`) est un
  agrégateur, pas un module, sauf si elle a son propre `src/` ;
- **dépendances** : les noms de `dependencies`, `devDependencies`,
  `peerDependencies` et `optionalDependencies` qui sont ceux d'un autre paquet de
  la source ; les paquets npm externes sont ignorés ;
- **nature** : un framework serveur ou front dans `dependencies` → `web` ; un
  script `start` ou un `bin` → `application` ; sinon bibliothèque ;
- **langage** : `typescript` (tsconfig, dépendance `typescript`, fichiers `.ts`),
  sinon `javascript` ; **cible** : `engines.node` (`node18`).

Un `package.json` illisible est signalé dans le compte rendu
(`build_file_unreadable`), son module précédent laissé tel quel.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Mapping
from typing import Any

from ossie_studio.parsers.base import OutputAction
from ossie_studio.sources.csharp_modules import ModuleStep
from ossie_studio.sources.project_modules import (
    UNREADABLE,
    BuildProject,
    project_module_actions,
)

PACKAGE = "package.json"
LANGUAGES = ("typescript", "javascript")
_DEPENDENCY_FIELDS = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)
_SERVER = frozenset(
    {
        "express",
        "fastify",
        "@nestjs/core",
        "koa",
        "@hapi/hapi",
        "hono",
        "restify",
        "next",
        "nuxt",
        "@remix-run/node",
        "@sveltejs/kit",
    }
)
_FRONT = frozenset({"react", "react-dom", "vue", "@angular/core", "svelte", "solid-js", "preact"})
_WORKSPACE_FILES = ("pnpm-workspace.yaml", "lerna.json")
_NODE = re.compile(r"(\d+)")


def _ignored(path: str) -> bool:
    return "node_modules/" in f"/{path}"


def _prefix(folder: str) -> str:
    return f"{folder}/" if folder else ""


def _read(raw: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _names(data: dict[str, Any], key: str) -> set[str]:
    value = data.get(key)
    return set(value) if isinstance(value, dict) else set()


def _is_aggregator(data: dict[str, Any], folder: str, files: Mapping[str, bytes]) -> bool:
    workspaces = data.get("workspaces")
    declared = bool(workspaces) or any(
        f"{_prefix(folder)}{name}" in files for name in _WORKSPACE_FILES
    )
    if not declared:
        return False
    src = f"{_prefix(folder)}src/"
    return not any(p.startswith(src) for p in files)


def _language(data: dict[str, Any], folder: str, files: Mapping[str, bytes]) -> str:
    deps = set().union(*(_names(data, k) for k in _DEPENDENCY_FIELDS))
    prefix = _prefix(folder)
    typed = (
        "typescript" in deps
        or f"{prefix}tsconfig.json" in files
        or any(
            p.startswith(prefix) and p.endswith((".ts", ".tsx")) and not _ignored(p) for p in files
        )
    )
    return "typescript" if typed else "javascript"


def _nature(data: dict[str, Any]) -> str:
    runtime = _names(data, "dependencies")
    if runtime & (_SERVER | _FRONT):
        return "web"
    scripts = data.get("scripts")
    if (isinstance(scripts, dict) and "start" in scripts) or data.get("bin"):
        return "application"
    return "library"


def _targets(data: dict[str, Any]) -> list[str]:
    engines = data.get("engines")
    node = engines.get("node") if isinstance(engines, dict) else None
    found = _NODE.search(node) if isinstance(node, str) else None
    return [f"node{found.group(1)}"] if found else []


def read_npm_projects(files: Mapping[str, bytes]) -> dict[str, BuildProject]:
    """Les paquets npm de la source, par `package.json`."""
    read: dict[str, dict[str, Any] | None] = {
        path: _read(files[path])
        for path in sorted(files)
        if posixpath.basename(path) == PACKAGE and not _ignored(path)
    }
    by_name = {
        str(data["name"]): path
        for path, data in read.items()
        if data is not None and isinstance(data.get("name"), str)
    }
    projects: dict[str, BuildProject] = {}
    for path, data in read.items():
        if data is None:
            projects[path] = BuildProject(path=path, error=UNREADABLE)
            continue
        folder = posixpath.dirname(path)
        if _is_aggregator(data, folder, files):
            continue
        deps = set().union(*(_names(data, k) for k in _DEPENDENCY_FIELDS))
        references = sorted({by_name[d] for d in deps if d in by_name} - {path})
        name = data.get("name")
        projects[path] = BuildProject(
            path=path,
            name=name if isinstance(name, str) and name else posixpath.basename(folder) or "root",
            language=_language(data, folder, files),
            nature=_nature(data),
            targets=_targets(data),
            references=references,
        )
    # Une dépendance vers un agrégateur n'est pas un lien de module.
    for project in projects.values():
        project.references = [r for r in project.references if r in projects]
    return projects


def _owns(language: str, project: str) -> bool:
    return language in LANGUAGES and posixpath.basename(project) == PACKAGE


def npm_module_actions(
    existing: Mapping[str, str],
    writes: list[OutputAction],
    deletes: list[str],
    projects: Mapping[str, BuildProject],
) -> ModuleStep:
    """Actions complétées des objets module TypeScript / JavaScript."""
    return project_module_actions(existing, writes, deletes, projects, _owns)
