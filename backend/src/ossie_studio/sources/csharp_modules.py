"""Modules C# : chaque `.csproj` devient un objet module dans raw (epic
« Cartographie applicative dans raw », ticket e0fa8519). Gael : « Si je fais le
parallèle avec C#, je veux trouver les csproj. »

Étape du **socle**, juste après les parseurs : elle part de la carte des projets
déjà lue pour départager les homonymes (`schema_resolve.read_project_map`, une
seule lecture des `.csproj`) et produit, au format de l'enabler
(`parsers.application.module_schema`), un objet module par projet :

- **nature** : projet de test (`IsTestProject`, ou paquet de test : xunit, NUnit,
  MSTest, `Microsoft.NET.Test.Sdk`), puis SDK web (`Microsoft.NET.Sdk.Web`,
  Blazor WebAssembly), SDK worker (`Microsoft.NET.Sdk.Worker`), `OutputType`
  `Exe` / `WinExe` (console), sinon bibliothèque ;
- **cibles** : `TargetFramework` / `TargetFrameworks` ;
- **dépendances** : chaque `ProjectReference` vers un `.csproj` de la source est
  reliée à son module ; une référence hors de la source reste non résolue ;
- **appartenance** : non écrite (règle du projet le plus proche, à la lecture).

Incrémental : un module dont le contenu ne change pas n'est pas réécrit ; le
module d'un `.csproj` disparu est retiré ; un `.csproj` illisible est signalé
dans le compte rendu de collecte et son module précédent, s'il existe, est
laissé tel quel (les autres modules sont produits).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ossie_studio.parsers.application import (
    MODULE_KEY,
    is_module,
    module_key,
    module_schema,
)
from ossie_studio.parsers.base import (
    STATUT_ERREUR,
    STATUT_INCHANGE,
    STATUT_TRAITE,
    OutputAction,
)
from ossie_studio.parsers.conventions import (
    SchemaReadError,
    dump_schema,
    is_schema_name,
    load_schema,
    schema_file,
    validate_schema,
)
from ossie_studio.sources.schema_resolve import CsProject, ProjectMap

LANGUAGE = "csharp"
_WEB_SDKS = ("microsoft.net.sdk.web", "microsoft.net.sdk.blazorwebassembly")
_WORKER_SDKS = ("microsoft.net.sdk.worker",)


def nature_of(project: CsProject) -> str:
    sdk = project.sdk.split("/", 1)[0].strip().lower()
    if project.test:
        return "test"
    if sdk in _WEB_SDKS:
        return "web"
    if sdk in _WORKER_SDKS:
        return "worker"
    if project.output_type.lower() in ("exe", "winexe"):
        return "console"
    return "library"


@dataclass
class ModuleStep:
    """Actions complétées, et ce qu'en dit le compte rendu de collecte."""

    writes: list[OutputAction]
    deletes: list[str]
    statuses: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    added: int = 0
    modified: int = 0
    deleted: int = 0


def _project_of_module(text: str) -> str | None:
    """Le `.csproj` d'un objet module C# de raw (None pour tout autre fichier)."""
    try:
        data = load_schema(text)
    except SchemaReadError:
        return None
    if not is_module(data) or data.get("x-language") != LANGUAGE:
        return None
    meta = data.get(MODULE_KEY)
    project = meta.get("project") if isinstance(meta, dict) else None
    if not isinstance(project, str) or not project.lower().endswith(".csproj"):
        return None
    return project


def csharp_module_actions(
    existing: Mapping[str, str],
    writes: list[OutputAction],
    deletes: list[str],
    projects: ProjectMap,
) -> ModuleStep:
    """`existing` : `raw/` avant la collecte ; `writes`/`deletes` : actions des
    parseurs. Rend les actions complétées des objets module C#."""
    removed = set(deletes)
    step = ModuleStep(list(writes), list(deletes))
    known = set(projects.projects)
    produced: set[str] = set()
    for path, project in sorted(projects.projects.items()):
        if project.error:
            step.statuses[path] = STATUT_ERREUR
            step.errors[path] = project.error
            continue
        schema = module_schema(
            project=path,
            name=project.name,
            language=LANGUAGE,
            nature=nature_of(project),
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
        # Le module d'un `.csproj` disparu part ; celui d'un `.csproj` illisible reste.
        if project_path is not None and project_path not in known:
            step.deletes.append(path)
            step.deleted += 1
    return step
