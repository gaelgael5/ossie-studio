"""Ranger les fonctions libres dans le `.extend.yaml` de leur module (epic
« Cartographie applicative dans raw », ticket f1cc03c4).

Un parseur ne connaît pas les modules (un plugin ne lit jamais la sortie d'un
autre) : il note ses fonctions libres (minimal API C#, et demain fonctions
Python ou TypeScript) **par fichier d'origine** dans son index
`functions-<parseur>.yaml`. Cette étape du **socle** les range dans le
`<clé du module>.extend.yaml` du module qui contient le fichier (règle du projet
le plus proche, `parsers.application.module_of`), parmi les modules du même
langage :

- écrit seulement quand le contenu change ;
- un module qui n'a plus de fonction libre perd son `.extend.yaml` ;
- le `.extend.yaml` d'un module disparu est retiré avec lui.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ossie_studio.parsers.application import (
    MODULE_KEY,
    MODULE_PREFIX,
    dump_functions,
    extend_file,
    is_extend_name,
    is_module,
    key_of,
    module_of,
)
from ossie_studio.parsers.base import OutputAction
from ossie_studio.parsers.conventions import SchemaReadError, is_schema_name, load_schema

INDEX_PREFIX = "functions-"


def is_functions_index(path: str) -> bool:
    return path.startswith(INDEX_PREFIX) and path.endswith(".yaml") and "/" not in path


def _load(text: str) -> Any:
    try:
        return load_schema(text)
    except SchemaReadError:
        return None


def sync_module_functions(
    existing: Mapping[str, str], writes: list[OutputAction], deletes: list[str]
) -> tuple[list[OutputAction], list[str]]:
    """`existing` : `raw/` avant la collecte ; `writes`/`deletes` : actions de la
    collecte. Rend les actions complétées des `.extend.yaml` des modules."""
    removed = set(deletes)
    final = {path: text for path, text in existing.items() if path not in removed}
    final.update({action.path: action.content for action in writes})

    modules: dict[str, tuple[str, str]] = {}
    for path, text in final.items():
        if is_schema_name(path) and is_module(data := _load(text)):
            meta = data.get(MODULE_KEY) or {}
            modules[key_of(path)] = (str(data.get("x-language", "")), str(meta.get("project", "")))

    by_language: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for path, text in final.items():
        if not is_functions_index(path):
            continue
        data = _load(text)
        if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
            continue
        files = by_language.setdefault(str(data.get("language", "")), {})
        for origin, entry in data["files"].items():
            if isinstance(entry, dict) and isinstance(entry.get("functions"), list):
                files[str(origin)] = entry["functions"]

    wanted: dict[str, list[dict[str, Any]]] = {}
    for language, files in by_language.items():
        projects = {k: project for k, (lang, project) in modules.items() if lang == language}
        for origin, functions in sorted(files.items()):
            key = module_of(origin, projects)
            if key is not None:
                wanted.setdefault(key, []).extend(functions)

    new_writes = list(writes)
    new_deletes = list(deletes)
    for key, (language, _project) in sorted(modules.items()):
        if language not in by_language:
            continue  # aucun parseur de ce langage ne range de fonction libre
        path = extend_file(key)
        current = final.get(path)
        found = wanted.get(key)
        if found:
            text = dump_functions({"functions": found})
            if text != current:
                new_writes.append(OutputAction(path=path, content=text))
        elif current is not None:
            new_deletes.append(path)
    for path in sorted(final):
        key = key_of(path)
        if is_extend_name(path) and key.startswith(MODULE_PREFIX) and key not in modules:
            new_deletes.append(path)
    return new_writes, new_deletes
