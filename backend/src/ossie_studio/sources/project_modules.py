"""Écriture commune des objets module tirés des fichiers de projet (epic
« Cartographie applicative dans raw ») : Gradle / Maven (`jvm_modules`), npm
(`npm_modules`)… Chaque lecteur rend des `BuildProject` ; cette étape du socle
écrit leurs objets module au format de l'enabler (`module_schema`) :

- un module dont le contenu ne change pas n'est pas réécrit ;
- le module d'un fichier de projet disparu est retiré (parmi les modules que le
  lecteur reconnaît comme siens : `owns`) ;
- un fichier de projet illisible est signalé dans le compte rendu, et son module
  précédent, s'il existe, est laissé tel quel.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

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

UNREADABLE = "build_file_unreadable"


@dataclass
class BuildProject:
    """Un projet : son fichier (chemin depuis la racine de la source), son nom,
    et ce qui fait son module ; `error` si le fichier est illisible."""

    path: str
    name: str = ""
    language: str = ""
    nature: str = "library"
    targets: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    error: str = ""


def _module_project(text: str) -> tuple[str, str] | None:
    """(langage, fichier de projet) d'un objet module de raw, None sinon."""
    try:
        data: Any = load_schema(text)
    except SchemaReadError:
        return None
    if not is_module(data):
        return None
    meta = data.get(MODULE_KEY)
    project = meta.get("project") if isinstance(meta, dict) else None
    if not isinstance(project, str):
        return None
    return str(data.get("x-language", "")), project


def project_module_actions(
    existing: Mapping[str, str],
    writes: list[OutputAction],
    deletes: list[str],
    projects: Mapping[str, BuildProject],
    owns: Callable[[str, str], bool],
) -> ModuleStep:
    """`existing` : `raw/` avant la collecte ; `writes`/`deletes` : actions déjà
    décidées ; `owns(langage, fichier de projet)` : ce module est-il de ce
    lecteur (seuls les siens sont retirés quand leur projet disparaît) ?"""
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
        found = _module_project(text)
        # Le module d'un projet disparu part ; celui d'un fichier illisible reste.
        if found is not None and owns(*found) and found[1] not in known:
            step.deletes.append(path)
            step.deleted += 1
    return step
