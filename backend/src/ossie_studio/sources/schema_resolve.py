"""Service de recherche de schéma commun (socle) et résolution des références.

Les parseurs émettent `x-unresolved-type: "<nom>"` pour toute référence qu'ils ne
savent pas résoudre dans leur propre groupe de fichiers. Après l'écriture des
plugins, le **socle** reconstruit l'index des schémas de `raw/` d'une source et
transforme ces marqueurs en `$ref` quand une cible **unique** existe (par nom
d'objet). C'est le socle — jamais un plugin — qui lit l'ensemble des sorties ;
un plugin ne lit donc jamais directement la sortie d'un autre.

Propriétés : **déterministe** (fonction pure des schémas émis), **monotone** (une
nouvelle cible résout une ancienne référence encore en attente ; on ne défait
jamais un `$ref` déjà posé), et **idempotent** (une collecte sans changement ne
réécrit rien).

Portée v1 : **au sein d'une même source** (`raw/` de la source). L'inter-sources
et les signatures d'objets de bibliothèque viendront plus tard.

**Homonymes** (epic « résolution des références C# entre projets », 22/11/2026) :
quand plusieurs objets portent le nom visé, on départage comme le compilateur et
on ne lie que s'il reste un seul candidat — sinon on ne devine pas :
1. projets atteignables — le `.csproj` le plus proche du fichier d'origine de
   l'objet qui référence, plus ses `<ProjectReference>` de proche en proche ;
2. namespaces — celui de l'objet (`x-namespace`), puis ses parents du plus
   proche au plus lointain, puis ceux importés par son fichier (`x-usings`).
La carte des projets se lit dans les fichiers du dépôt au moment de la collecte
(elle n'est pas persistée) ; sans `.csproj`, seuls les namespaces départagent.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from ossie_studio.parsers.base import OutputAction
from ossie_studio.parsers.conventions import (
    SchemaReadError,
    dump_schema,
    is_schema_name,
    load_schema,
)

UNRESOLVED_KEY = "x-unresolved-type"
REF_KEY = "$ref"


def is_schema_path(path: str) -> bool:
    """Un schéma de `raw/` : un `.schema.yaml` à la racine (les index sont des
    `.yaml` sans ce suffixe, les documents sont sous `docs/`)."""
    return is_schema_name(path)


# --- Carte des projets C# ---------------------------------------------------------

# Expression plutôt qu'analyseur XML : on ne lit qu'un attribut, et un analyseur
# XML sur un fichier du dépôt ouvrirait la porte aux entités externes.
_PROJECT_REF = re.compile(r"<ProjectReference\s+Include\s*=\s*\"([^\"]+)\"", re.IGNORECASE)


@dataclass
class ProjectMap:
    """Projets `.csproj` d'une source : dossier du projet → dossiers des projets
    qu'il référence. Vide si la source n'en a pas."""

    refs: dict[str, set[str]] = field(default_factory=dict)

    def project_of(self, file: str) -> str | None:
        """Le projet d'un fichier : le dossier de projet le plus profond qui le contient."""
        best: str | None = None
        for folder in self.refs:
            inside = not folder or file.startswith(f"{folder}/")
            if inside and (best is None or len(folder) > len(best)):
                best = folder
        return best

    def reachable(self, project: str) -> set[str]:
        seen = {project}
        stack = [project]
        while stack:
            for nxt in self.refs.get(stack.pop(), set()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen


def read_project_map(source_files: dict[str, bytes]) -> ProjectMap:
    """Lit les `.csproj` des fichiers de la source ; un projet illisible compte
    pour un projet sans référence (jamais d'échec de collecte)."""
    refs: dict[str, set[str]] = {}
    for path, data in source_files.items():
        if not path.lower().endswith(".csproj"):
            continue
        folder = posixpath.dirname(path)
        text = data.decode("utf-8", errors="replace")
        targets: set[str] = set()
        for include in _PROJECT_REF.findall(text):
            target = posixpath.normpath(posixpath.join(folder, include.replace("\\", "/")))
            if not target.startswith(".."):
                targets.add(posixpath.dirname(target))
        refs[folder] = targets
    return ProjectMap(refs)


@dataclass(frozen=True)
class _Candidate:
    path: str
    title: str
    namespace: str
    project: str | None


@dataclass(frozen=True)
class RefContext:
    """Ce que l'on sait de l'objet qui porte la référence."""

    namespace: str = ""
    usings: tuple[str, ...] = ()
    project: str | None = None


class SchemaIndex:
    """Index de recherche des schémas d'une source : nom d'objet → fichier(s).
    `resolve` ne rend une cible que si **une seule** porte ce nom (sinon ambigu) ;
    `resolve_in` départage aussi des homonymes d'après le contexte de la référence."""

    def __init__(
        self,
        schemas: dict[str, str],
        *,
        origins: dict[str, str] | None = None,
        projects: ProjectMap | None = None,
    ) -> None:
        self._by_name: dict[str, set[str]] = {}
        self._candidates: dict[str, list[_Candidate]] = {}
        self._projects = projects or ProjectMap()
        self._origins = origins or {}
        self.contexts: dict[str, RefContext] = {}
        for path, content in schemas.items():
            try:
                data = load_schema(content)
            except (SchemaReadError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            namespace = str(data.get("x-namespace") or "")
            project = self._project(path)
            usings = data.get("x-usings")
            self.contexts[path] = RefContext(
                namespace,
                tuple(str(u) for u in usings) if isinstance(usings, list) else (),
                project,
            )
            title = data.get("title")
            if isinstance(title, str) and title:
                self._by_name.setdefault(title, set()).add(path)
                self._candidates.setdefault(title, []).append(
                    _Candidate(path, title, namespace, project)
                )

    def _project(self, schema_path: str) -> str | None:
        origin = self._origins.get(schema_path)
        if not origin or not self._projects.refs:
            return None
        return self._projects.project_of(origin)

    def resolve(self, name: str) -> str | None:
        """Le fichier du schéma nommé `name`, s'il est unique ; sinon None."""
        candidates = self._by_name.get(name)
        if candidates is not None and len(candidates) == 1:
            return next(iter(candidates))
        return None

    def resolve_in(self, name: str, context: RefContext) -> str | None:
        """Cible de `name` vue depuis `context` : unique, ou départagée par
        projets atteignables puis namespaces ; None s'il reste un doute."""
        if "<" in name:
            return None
        short = name.rsplit(".", 1)[-1]
        candidates = self._candidates.get(short, [])
        if "." in name:  # nom qualifié : la fin du nom complet doit correspondre
            candidates = [c for c in candidates if f"{c.namespace}.{c.title}".endswith(name)]
        if len(candidates) <= 1:
            return candidates[0].path if candidates else None
        if context.project is not None:
            reach = self._projects.reachable(context.project)
            candidates = [c for c in candidates if c.project in reach]
            if len(candidates) <= 1:
                return candidates[0].path if candidates else None
        scope = context.namespace
        while scope:
            here = [c for c in candidates if c.namespace == scope]
            if here:
                return here[0].path if len(here) == 1 else None
            scope = scope.rsplit(".", 1)[0] if "." in scope else ""
        imported = [c for c in candidates if c.namespace and c.namespace in context.usings]
        return imported[0].path if len(imported) == 1 else None

    def names(self) -> set[str]:
        return set(self._by_name)


def _resolve_node(
    node: Any, index: SchemaIndex, counter: list[int], context: RefContext | None = None
) -> Any:
    """Parcourt récursivement un schéma et remplace les marqueurs
    `x-unresolved-type` par `$ref` quand la cible est connue."""
    ctx = context or RefContext()
    if isinstance(node, list):
        return [_resolve_node(item, index, counter, ctx) for item in node]
    if not isinstance(node, dict):
        return node
    name = node.get(UNRESOLVED_KEY)
    if isinstance(name, str) and REF_KEY not in node:
        target = index.resolve_in(name, ctx)
        if target is not None:
            resolved = {k: v for k, v in node.items() if k != UNRESOLVED_KEY}
            resolved[REF_KEY] = target
            counter[0] += 1
            return {k: _resolve_node(v, index, counter, ctx) for k, v in resolved.items()}
    return {k: _resolve_node(v, index, counter, ctx) for k, v in node.items()}


def _is_tech_manifest(path: str) -> bool:
    return path.startswith("manifest") and path.endswith(".yaml") and "/" not in path


def _schema_origins(current: dict[str, str], writes: dict[str, str]) -> dict[str, str]:
    """Schéma → fichier d'origine, d'après les manifestes techniques de `raw/`
    (ceux écrits par cette collecte l'emportent sur les précédents)."""
    from ossie_studio.sources.collect_core import parse_tech_manifest

    out: dict[str, str] = {}
    manifests = {p: c for p, c in current.items() if _is_tech_manifest(p)}
    manifests.update({p: c for p, c in writes.items() if _is_tech_manifest(p)})
    for content in manifests.values():
        try:
            tech = parse_tech_manifest(content)
        except (yaml.YAMLError, KeyError, TypeError, AttributeError, ValueError):
            # Un manifeste illisible n'empêche pas la résolution (critères restants).
            continue
        for source, entry in tech.entries.items():
            for dest in entry.destinations:
                out.setdefault(dest, source)
    return out


def resolve_source_schemas(
    existing: dict[str, str],
    writes: list[OutputAction],
    deletes: list[str],
    source_files: dict[str, bytes] | None = None,
) -> tuple[list[OutputAction], int]:
    """Rejoue la résolution sur l'ensemble final des schémas de `raw/`.

    `existing` : instantané de `raw/` avant cette collecte (chemin → contenu) ;
    `writes`/`deletes` : actions des plugins de cette collecte ; `source_files` :
    fichiers du dépôt (pour la carte des projets `.csproj`). Rend la **nouvelle
    liste d'actions** (les écritures de schémas résolues, plus les schémas déjà
    présents dont une référence vient d'être résolue) et le nombre de références
    résolues.
    """
    removed = set(deletes)
    current = {path: content for path, content in existing.items() if path not in removed}
    final: dict[str, str] = {
        path: content for path, content in current.items() if is_schema_path(path)
    }
    for action in writes:
        if is_schema_path(action.path):
            final[action.path] = action.content

    index = SchemaIndex(
        final,
        origins=_schema_origins(current, {a.path: a.content for a in writes}),
        projects=read_project_map(source_files or {}),
    )
    counter = [0]
    rewritten: dict[str, str] = {}
    for path, content in final.items():
        try:
            data = load_schema(content)
        except SchemaReadError:
            continue
        before = counter[0]
        resolved = _resolve_node(data, index, counter, index.contexts.get(path))
        if counter[0] > before:
            new_content = dump_schema(resolved)
            if new_content != content:
                rewritten[path] = new_content

    if not rewritten:
        return writes, 0

    result: list[OutputAction] = []
    seen: set[str] = set()
    for action in writes:
        if action.path in rewritten:
            result.append(OutputAction(path=action.path, content=rewritten[action.path]))
            seen.add(action.path)
        else:
            result.append(action)
    for path, content in rewritten.items():
        if path not in seen:
            result.append(OutputAction(path=path, content=content))
    return result, counter[0]
