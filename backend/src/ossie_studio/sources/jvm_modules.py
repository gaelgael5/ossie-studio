"""Modules Kotlin / Java : chaque projet Gradle ou Maven devient un objet module
dans raw (epic « Cartographie applicative dans raw », ticket e34e5c29), comme les
`.csproj` pour C# (`sources.csharp_modules`).

Étape du **socle**, juste après les modules C#. Les fichiers de build sont lus
comme du texte (Gradle) ou du XML (Maven), **jamais exécutés** :

- **Gradle** : les sous-projets déclarés par `include(...)` dans
  `settings.gradle(.kts)` (`:services:facturation` → dossier
  `services/facturation`, ou le `projectDir` donné) ; chacun avec son
  `build.gradle(.kts)` est un module. La racine d'un build multi-projets n'est un
  module que si elle a son propre code (`src/`) ; un build sans `settings` est un
  module. Dépendances : `project(":x")` et les accesseurs `projects.x`.
- **Maven** : chaque `pom.xml` qui n'est pas un agrégateur (`packaging pom`) ;
  une dépendance vers le `groupId:artifactId` d'un autre pom de la source est
  reliée à son module (`groupId` hérité du parent s'il manque).
- **Nature** : Spring Boot (plugin `org.springframework.boot`,
  `spring-boot-maven-plugin`, ou parent `spring-boot-starter-parent`) →
  `web` avec `spring-boot-starter-web` / `webflux`, sinon `application` ; le
  reste est une bibliothèque.
- **Cible** : la version Java déclarée (`java17`…).
- **Langage** : `kotlin` si le plugin Kotlin est présent (ou des `.kt`), sinon
  `java`.

Incrémental, comme pour C# : un module inchangé n'est pas réécrit ; le module
d'un fichier de build disparu est retiré ; un fichier de build illisible est
signalé dans le compte rendu (`build_file_unreadable`) et son module précédent
est laissé tel quel.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from xml.etree.ElementTree import Element, ParseError

from defusedxml import ElementTree as DefusedET  # type: ignore[import-untyped]

from ossie_studio.parsers.application import MODULE_KEY, is_module, module_key, module_schema
from ossie_studio.parsers.base import STATUT_ERREUR, STATUT_INCHANGE, STATUT_TRAITE, OutputAction
from ossie_studio.parsers.conventions import (
    SchemaReadError,
    dump_schema,
    is_schema_name,
    load_schema,
    schema_file,
    validate_schema,
)
from ossie_studio.sources.csharp_modules import ModuleStep

LANGUAGES = ("java", "kotlin")
UNREADABLE = "build_file_unreadable"
GRADLE_BUILDS = ("build.gradle.kts", "build.gradle")
GRADLE_SETTINGS = ("settings.gradle.kts", "settings.gradle")
POM = "pom.xml"

_QUOTED = re.compile(r"""["']([^"']+)["']""")
_INCLUDE = re.compile(r"^\s*include\b(.*)$", re.MULTILINE)
_PROJECT_DIR = re.compile(
    r"""project\(\s*["'](:[^"']*)["']\s*\)\s*\.projectDir\s*=\s*(?:new\s+File\([^,]+,\s*)?"""
    r"""(?:file\()?\s*["']([^"']+)["']"""
)
_PROJECT_DEP = re.compile(r"""project\(\s*(?:path\s*[:=]\s*)?["'](:[^"']*)["']""")
_ACCESSOR = re.compile(r"\bprojects\.([A-Za-z0-9_.]+)")
_BOOT_PLUGIN = re.compile(
    r"""(?:id\s*\(?\s*["']org\.springframework\.boot["']|apply\s+plugin\s*:\s*["']"""
    r"""(?:org\.springframework\.boot|spring-boot)["']|plugins\.spring[._-]?boot)""",
    re.IGNORECASE,
)
_KOTLIN_PLUGIN = re.compile(
    r"""(?:\bkotlin\s*\(\s*["']|org\.jetbrains\.kotlin|id\s*\(?\s*["']kotlin-|plugins\.kotlin)""",
    re.IGNORECASE,
)
_WEB = re.compile(r"spring-boot-starter-web(?:flux)?\b")
_GRADLE_TARGETS = (
    re.compile(r"JavaLanguageVersion\.of\(\s*(\d+)\s*\)"),
    re.compile(r"jvmToolchain\(\s*(\d+)\s*\)"),
    re.compile(r"(?:source|target)Compatibility\s*=\s*JavaVersion\.VERSION_(?:1_)?(\d+)"),
    re.compile(r"""(?:source|target)Compatibility\s*=\s*["']?(?:1\.)?(\d+)["']?"""),
    re.compile(r"JvmTarget\.JVM_(?:1_)?(\d+)"),
    re.compile(r"""jvmTarget\s*(?:=|\.set\()\s*["'](?:1\.)?(\d+)["']"""),
)


@dataclass
class JvmProject:
    """Un projet Gradle ou Maven : son fichier de build (chemin depuis la racine
    de la source), son nom, et ce qui fait son module."""

    path: str
    name: str = ""
    language: str = "java"
    nature: str = "library"
    targets: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    error: str = ""


def _decode(raw: bytes) -> str | None:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _strip_comments(text: str) -> str:
    """Retire les commentaires `//` et `/* */` d'un script Gradle (sans toucher
    aux URL entre guillemets, assez pour les motifs lus ici)."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"(?m)(^|\s)//.*$", r"\1", text)


def _join(folder: str, rel: str) -> str:
    path = posixpath.normpath(posixpath.join(folder, rel)) if folder else posixpath.normpath(rel)
    return "" if path == "." else path


def _has_kotlin(files: Mapping[str, bytes], folder: str) -> bool:
    prefix = f"{folder}/" if folder else ""
    return any(p.startswith(prefix) and p.endswith(".kt") for p in files)


def _targets(text: str) -> list[str]:
    found = {f"java{m.group(1)}" for pattern in _GRADLE_TARGETS for m in pattern.finditer(text)}
    return sorted(found)


def _accessor_key(path: str) -> str:
    """`:services:billing-service` et `services.billingService` → même clé."""
    parts = [p for p in re.split(r"[:.]", path) if p]
    return ".".join(re.sub(r"[^a-z0-9]", "", p.lower()) for p in parts)


# --- Gradle --------------------------------------------------------------------------------


def _gradle_build(files: Mapping[str, bytes], folder: str) -> str | None:
    for name in GRADLE_BUILDS:
        path = f"{folder}/{name}" if folder else name
        if path in files:
            return path
    return None


def _gradle_projects(files: Mapping[str, bytes]) -> dict[str, JvmProject]:
    projects: dict[str, JvmProject] = {}
    settings = sorted(p for p in files if posixpath.basename(p) in GRADLE_SETTINGS)
    covered: set[str] = set()  # fichiers de build rattachés à un `settings`
    for settings_path in settings:
        root = posixpath.dirname(settings_path)
        text = _decode(files[settings_path])
        if text is None:
            continue
        text = _strip_comments(text)
        dirs = {m.group(1): _join(root, m.group(2)) for m in _PROJECT_DIR.finditer(text)}
        declared: dict[str, str] = {}  # chemin Gradle → dossier
        for match in _INCLUDE.finditer(text):
            for name in _QUOTED.findall(match.group(1)):
                gradle_path = name if name.startswith(":") else f":{name}"
                folder = dirs.get(gradle_path) or _join(
                    root, gradle_path.strip(":").replace(":", "/")
                )
                declared[gradle_path] = folder
        builds: dict[str, str] = {}  # chemin Gradle → fichier de build
        for gradle_path, folder in declared.items():
            build = _gradle_build(files, folder)
            if build is not None:
                builds[gradle_path] = build
        root_build = _gradle_build(files, root)
        if root_build is not None:
            covered.add(root_build)
            src = f"{root}/src/" if root else "src/"
            if not declared or any(p.startswith(src) for p in files):
                builds[":"] = root_build
        covered.update(builds.values())
        by_accessor = {_accessor_key(g): b for g, b in builds.items() if g != ":"}
        for gradle_path, build in builds.items():
            name = gradle_path.rsplit(":", 1)[-1] or posixpath.basename(root) or "root"
            projects[build] = _gradle_project(files, build, name, builds, by_accessor)
    for path in sorted(files):
        if posixpath.basename(path) in GRADLE_BUILDS and path not in covered:
            # Un build sans `settings` : un seul projet, lui-même.
            folder = posixpath.dirname(path)
            if _gradle_build(files, folder) == path:
                name = posixpath.basename(folder) or "root"
                projects[path] = _gradle_project(files, path, name, {}, {})
    return projects


def _gradle_project(
    files: Mapping[str, bytes],
    build: str,
    name: str,
    builds: Mapping[str, str],
    by_accessor: Mapping[str, str],
) -> JvmProject:
    project = JvmProject(path=build, name=name)
    raw = _decode(files[build])
    if raw is None:
        project.error = UNREADABLE
        return project
    text = _strip_comments(raw)
    references = {builds[g] for g in _PROJECT_DEP.findall(text) if g in builds}
    references |= {
        by_accessor[key]
        for key in (_accessor_key(a) for a in _ACCESSOR.findall(text))
        if key in by_accessor
    }
    references.discard(build)
    project.references = sorted(references)
    boot = any("apply false" not in line for line in text.splitlines() if _BOOT_PLUGIN.search(line))
    project.nature = ("web" if _WEB.search(text) else "application") if boot else "library"
    folder = posixpath.dirname(build)
    kotlin = _KOTLIN_PLUGIN.search(text) is not None or _has_kotlin(files, folder)
    project.language = "kotlin" if kotlin else "java"
    project.targets = _targets(text)
    return project


# --- Maven ---------------------------------------------------------------------------------


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(node: Element | None, name: str) -> Element | None:
    if node is None:
        return None
    return next((c for c in node if _local(c.tag) == name), None)


def _children(node: Element | None, name: str) -> list[Element]:
    return [c for c in node if _local(c.tag) == name] if node is not None else []


def _value(node: Element | None, name: str) -> str:
    found = _child(node, name)
    return (found.text or "").strip() if found is not None else ""


@dataclass
class _Pom:
    path: str
    group: str = ""
    artifact: str = ""
    packaging: str = "jar"
    parent: tuple[str, str] = ("", "")
    dependencies: list[tuple[str, str]] = field(default_factory=list)
    plugins: list[str] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)
    error: str = ""


def _read_pom(path: str, raw: bytes) -> _Pom:
    pom = _Pom(path=path)
    try:
        root = DefusedET.fromstring(raw)
    except (ParseError, ValueError):
        pom.error = UNREADABLE
        return pom
    if _local(root.tag) != "project":
        pom.error = UNREADABLE
        return pom
    parent = _child(root, "parent")
    pom.parent = (_value(parent, "groupId"), _value(parent, "artifactId"))
    pom.group = _value(root, "groupId") or pom.parent[0]
    pom.artifact = _value(root, "artifactId")
    pom.packaging = _value(root, "packaging") or "jar"
    for dep in _children(_child(root, "dependencies"), "dependency"):
        group = _value(dep, "groupId").replace("${project.groupId}", pom.group)
        pom.dependencies.append((group, _value(dep, "artifactId")))
    build = _child(root, "build")
    pom.plugins = [_value(p, "artifactId") for p in _children(_child(build, "plugins"), "plugin")]
    props = _child(root, "properties")
    pom.properties = (
        {_local(p.tag): (p.text or "").strip() for p in props} if props is not None else {}
    )
    return pom


def _pom_targets(pom: _Pom, poms: Mapping[tuple[str, str], _Pom]) -> list[str]:
    current: _Pom | None = pom
    for _ in range(5):  # hérité du parent, s'il est dans la source
        if current is None:
            break
        for key in ("maven.compiler.release", "java.version", "maven.compiler.target"):
            value = current.properties.get(key, "")
            if value:
                return [f"java{value.removeprefix('1.')}"]
        current = poms.get(current.parent)
    return []


def _maven_projects(files: Mapping[str, bytes]) -> dict[str, JvmProject]:
    read = [_read_pom(p, files[p]) for p in sorted(files) if posixpath.basename(p) == POM]
    by_coords = {(p.group, p.artifact): p for p in read if not p.error and p.artifact}
    projects: dict[str, JvmProject] = {}
    for pom in read:
        if pom.error:
            projects[pom.path] = JvmProject(path=pom.path, error=pom.error)
            continue
        if pom.packaging == "pom":
            continue  # agrégateur : sert à trouver les modules, pas un module lui-même
        folder = posixpath.dirname(pom.path)
        boot = (
            "spring-boot-maven-plugin" in pom.plugins
            or pom.parent[1] == "spring-boot-starter-parent"
        )
        web = any(_WEB.fullmatch(artifact) for _group, artifact in pom.dependencies)
        kotlin = (
            "kotlin-maven-plugin" in pom.plugins
            or any(a.startswith("kotlin-stdlib") for _g, a in pom.dependencies)
            or _has_kotlin(files, folder)
        )
        references = sorted(
            {
                by_coords[dep].path
                for dep in pom.dependencies
                if dep in by_coords and by_coords[dep].packaging != "pom"
            }
            - {pom.path}
        )
        projects[pom.path] = JvmProject(
            path=pom.path,
            name=pom.artifact or posixpath.basename(folder) or "root",
            language="kotlin" if kotlin else "java",
            nature=("web" if web else "application") if boot else "library",
            targets=_pom_targets(pom, by_coords),
            references=references,
        )
    return projects


def read_jvm_projects(files: Mapping[str, bytes]) -> dict[str, JvmProject]:
    """Les projets Gradle et Maven de la source, par fichier de build."""
    return {**_gradle_projects(files), **_maven_projects(files)}


# --- Actions --------------------------------------------------------------------------------


def _is_build_file(path: str) -> bool:
    return posixpath.basename(path) in (*GRADLE_BUILDS, POM)


def _project_of_module(text: str) -> str | None:
    """Le fichier de build d'un objet module Kotlin / Java de raw (None sinon)."""
    try:
        data: Any = load_schema(text)
    except SchemaReadError:
        return None
    if not is_module(data) or data.get("x-language") not in LANGUAGES:
        return None
    meta = data.get(MODULE_KEY)
    project = meta.get("project") if isinstance(meta, dict) else None
    return project if isinstance(project, str) and _is_build_file(project) else None


def jvm_module_actions(
    existing: Mapping[str, str],
    writes: list[OutputAction],
    deletes: list[str],
    projects: Mapping[str, JvmProject],
) -> ModuleStep:
    """`existing` : `raw/` avant la collecte ; `writes`/`deletes` : actions déjà
    décidées. Rend les actions complétées des objets module Kotlin / Java."""
    removed = set(deletes)
    step = ModuleStep(list(writes), list(deletes))
    known = set(projects)
    produced: set[str] = set()
    for path, project in sorted(projects.items()):
        if project.error:
            step.statuses[path] = STATUT_ERREUR
            step.errors[path] = project.error
            continue
        schema = module_schema(
            project=path,
            name=project.name,
            language=project.language,
            nature=project.nature,
            targets=project.targets,
            dependencies=project.references,
            known_projects=known,
        )
        validate_schema(schema)
        target = schema_file(module_key(path))
        text = dump_schema(schema)
        produced.add(target)
        before = None if target in removed else existing.get(target)
        if before == text:
            step.statuses[path] = STATUT_INCHANGE
            continue
        step.writes.append(OutputAction(path=target, content=text))
        step.statuses[path] = STATUT_TRAITE
        if before is None:
            step.added += 1
        else:
            step.modified += 1
    for path, text in sorted(existing.items()):
        if path in removed or path in produced or not is_schema_name(path):
            continue
        project_path = _project_of_module(text)
        # Le module d'un fichier de build disparu part ; celui d'un fichier illisible reste.
        if project_path is not None and project_path not in known:
            step.deletes.append(path)
            step.deleted += 1
    return step
