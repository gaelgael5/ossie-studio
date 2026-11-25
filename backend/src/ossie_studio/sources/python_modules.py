"""Modules Python : chaque `pyproject.toml` (à défaut `setup.cfg` / `setup.py`)
devient un objet module dans raw (epic « Cartographie applicative dans raw »,
ticket 3343afe4), comme les `.csproj`, les projets Gradle / Maven et les
`package.json`.

Étape du **socle** ; rien n'est exécuté (`setup.py` est lu comme du texte) :

- un projet par dossier : `pyproject.toml`, sinon `setup.cfg`, sinon `setup.py` ;
  la racine d'un espace de travail uv (`[tool.uv.workspace]`) sans projet à elle
  (ni `[project]`, ni `[tool.poetry]`) est un agrégateur, pas un module ;
- **dépendances** : par nom de paquet (`[project].dependencies`,
  `optional-dependencies`, `[dependency-groups]`, Poetry, `install_requires`),
  noms comparés après normalisation (PEP 503), ou par chemin (sources uv
  `{ path = "../x" }`, Poetry `{ path = "../x" }`) ; les paquets externes sont
  ignorés ;
- **nature** : FastAPI, Flask, Django… → `web` ; Celery → `worker` ; un script
  déclaré → `application` ; sinon bibliothèque ;
- **cible** : `requires-python` (`python3.11`).

Un fichier de projet illisible est signalé (`build_file_unreadable`), son module
précédent laissé tel quel.
"""

from __future__ import annotations

import configparser
import posixpath
import re
import tomllib
from collections.abc import Iterable, Mapping
from typing import Any

from ossie_studio.parsers.base import OutputAction
from ossie_studio.sources.csharp_modules import ModuleStep
from ossie_studio.sources.project_modules import (
    UNREADABLE,
    BuildProject,
    project_module_actions,
)

LANGUAGE = "python"
PROJECT_FILES = ("pyproject.toml", "setup.cfg", "setup.py")
_WEB = frozenset(
    {
        "fastapi",
        "flask",
        "django",
        "starlette",
        "litestar",
        "sanic",
        "aiohttp",
        "quart",
        "falcon",
        "tornado",
        "pyramid",
        "bottle",
    }
)
_WORKER = frozenset({"celery", "dramatiq", "rq", "arq", "huey"})
_REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_PYTHON = re.compile(r"(\d+\.\d+)")


def canonical(name: str) -> str:
    """Nom de paquet normalisé (PEP 503) : `My_Pkg.x` → `my-pkg-x`."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_names(items: Iterable[Any]) -> set[str]:
    names: set[str] = set()
    for item in items:
        if isinstance(item, str) and (found := _REQUIREMENT.match(item)):
            names.add(canonical(found.group(1)))
    return names


def _table(data: Any, *keys: str) -> dict[str, Any]:
    for key in keys:
        data = data.get(key) if isinstance(data, dict) else None
    return data if isinstance(data, dict) else {}


class _Read:
    """Ce qu'un fichier de projet dit : nom, dépendances (noms et chemins),
    scripts, version de Python ; `aggregator` pour une racine d'espace de travail."""

    def __init__(self) -> None:
        self.name = ""
        self.names: set[str] = set()
        self.paths: set[str] = set()
        self.scripts = False
        self.python = ""
        self.aggregator = False


def _read_pyproject(text: str) -> _Read:
    data = tomllib.loads(text)
    read = _Read()
    project = _table(data, "project")
    poetry = _table(data, "tool", "poetry")
    read.name = str(project.get("name") or poetry.get("name") or "")
    deps: list[Any] = list(project.get("dependencies") or [])
    for extra in _table(project, "optional-dependencies").values():
        deps += list(extra or [])
    for group in _table(data, "dependency-groups").values():
        deps += [d for d in group or [] if isinstance(d, str)]
    read.names = _requirement_names(deps)
    poetry_deps = dict(_table(poetry, "dependencies"))
    poetry_deps.update(_table(poetry, "dev-dependencies"))
    for group in _table(poetry, "group").values():
        poetry_deps.update(_table(group, "dependencies"))
    for name, spec in poetry_deps.items():
        if name.lower() == "python":
            read.python = read.python or str(spec)
            continue
        read.names.add(canonical(name))
        if isinstance(spec, dict) and isinstance(spec.get("path"), str):
            read.paths.add(spec["path"])
    for name, spec in _table(data, "tool", "uv", "sources").items():
        if isinstance(spec, dict) and isinstance(spec.get("path"), str):
            read.paths.add(spec["path"])
            read.names.add(canonical(name))
    read.scripts = bool(
        _table(project, "scripts") or _table(project, "gui-scripts") or _table(poetry, "scripts")
    )
    read.python = str(project.get("requires-python") or read.python or "")
    read.aggregator = bool(_table(data, "tool", "uv", "workspace")) and not read.name
    return read


def _read_setup_cfg(text: str) -> _Read:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(text)
    read = _Read()
    read.name = parser.get("metadata", "name", fallback="")
    requires = parser.get("options", "install_requires", fallback="")
    read.names = _requirement_names(requires.splitlines())
    read.scripts = bool(parser.get("options.entry_points", "console_scripts", fallback=""))
    read.python = parser.get("options", "python_requires", fallback="")
    return read


def _read_setup_py(text: str) -> _Read:
    read = _Read()
    name = re.search(r"""\bname\s*=\s*['"]([^'"]+)['"]""", text)
    read.name = name.group(1) if name else ""
    requires = re.search(r"install_requires\s*=\s*\[(.*?)\]", text, re.DOTALL)
    if requires:
        read.names = _requirement_names(re.findall(r"""['"]([^'"]+)['"]""", requires.group(1)))
    read.scripts = "console_scripts" in text or re.search(r"\bscripts\s*=", text) is not None
    python = re.search(r"""python_requires\s*=\s*['"]([^'"]+)['"]""", text)
    read.python = python.group(1) if python else ""
    return read


_READERS = {
    "pyproject.toml": _read_pyproject,
    "setup.cfg": _read_setup_cfg,
    "setup.py": _read_setup_py,
}


def _project_file(files: Mapping[str, bytes], folder: str) -> str | None:
    for name in PROJECT_FILES:
        path = f"{folder}/{name}" if folder else name
        if path in files:
            return path
    return None


def read_python_projects(files: Mapping[str, bytes]) -> dict[str, BuildProject]:
    """Les projets Python de la source, par fichier de projet."""
    folders = sorted(
        {posixpath.dirname(p) for p in files if posixpath.basename(p) in PROJECT_FILES}
    )
    read: dict[str, _Read | None] = {}
    for folder in folders:
        path = _project_file(files, folder)
        if path is None:
            continue
        try:
            text = files[path].decode("utf-8-sig")
            read[path] = _READERS[posixpath.basename(path)](text)
        except (UnicodeDecodeError, tomllib.TOMLDecodeError, configparser.Error):
            read[path] = None
    by_name = {canonical(r.name): p for p, r in read.items() if r is not None and r.name}
    projects: dict[str, BuildProject] = {}
    for path, found in read.items():
        if found is None:
            projects[path] = BuildProject(path=path, error=UNREADABLE)
            continue
        if found.aggregator:
            continue
        folder = posixpath.dirname(path)
        references = {by_name[n] for n in found.names if n in by_name}
        for rel in found.paths:
            target = posixpath.normpath(posixpath.join(folder, rel)) if folder else rel
            target_file = _project_file(files, "" if target == "." else target)
            if target_file is not None:
                references.add(target_file)
        references.discard(path)
        if found.names & _WEB:
            nature = "web"
        elif found.names & _WORKER:
            nature = "worker"
        elif found.scripts:
            nature = "application"
        else:
            nature = "library"
        version = _PYTHON.search(found.python)
        projects[path] = BuildProject(
            path=path,
            name=found.name or posixpath.basename(folder) or "root",
            language=LANGUAGE,
            nature=nature,
            targets=[f"python{version.group(1)}"] if version else [],
            references=sorted(references),
        )
    for project in projects.values():
        project.references = [r for r in project.references if r in projects]
    return projects


def _owns(language: str, project: str) -> bool:
    return language == LANGUAGE and posixpath.basename(project) in PROJECT_FILES


def python_module_actions(
    existing: Mapping[str, str],
    writes: list[OutputAction],
    deletes: list[str],
    projects: Mapping[str, BuildProject],
) -> ModuleStep:
    """Actions complétées des objets module Python."""
    return project_module_actions(existing, writes, deletes, projects, _owns)
