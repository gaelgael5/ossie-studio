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
"""

from __future__ import annotations

import json
from typing import Any

from ossie_studio.parsers.base import OutputAction
from ossie_studio.parsers.conventions import dump_schema

UNRESOLVED_KEY = "x-unresolved-type"
REF_KEY = "$ref"


def is_schema_path(path: str) -> bool:
    """Un schéma de `raw/` : un `.json` à la racine (les index sont des `.yaml`,
    les documents sont sous `docs/`)."""
    return path.endswith(".json") and "/" not in path


class SchemaIndex:
    """Index de recherche des schémas d'une source : nom d'objet → fichier(s).
    `resolve` ne rend une cible que si **une seule** porte ce nom (sinon ambigu)."""

    def __init__(self, schemas: dict[str, str]) -> None:
        self._by_name: dict[str, set[str]] = {}
        for path, content in schemas.items():
            try:
                data = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            title = data.get("title")
            if isinstance(title, str) and title:
                self._by_name.setdefault(title, set()).add(path)

    def resolve(self, name: str) -> str | None:
        """Le fichier du schéma nommé `name`, s'il est unique ; sinon None."""
        candidates = self._by_name.get(name)
        if candidates is not None and len(candidates) == 1:
            return next(iter(candidates))
        return None

    def names(self) -> set[str]:
        return set(self._by_name)


def _resolve_node(node: Any, index: SchemaIndex, counter: list[int]) -> Any:
    """Parcourt récursivement un schéma et remplace les marqueurs
    `x-unresolved-type` par `$ref` quand la cible est connue."""
    if isinstance(node, list):
        return [_resolve_node(item, index, counter) for item in node]
    if not isinstance(node, dict):
        return node
    name = node.get(UNRESOLVED_KEY)
    if isinstance(name, str) and REF_KEY not in node:
        target = index.resolve(name)
        if target is not None:
            resolved = {k: v for k, v in node.items() if k != UNRESOLVED_KEY}
            resolved[REF_KEY] = target
            counter[0] += 1
            return {k: _resolve_node(v, index, counter) for k, v in resolved.items()}
    return {k: _resolve_node(v, index, counter) for k, v in node.items()}


def resolve_source_schemas(
    existing: dict[str, str], writes: list[OutputAction], deletes: list[str]
) -> tuple[list[OutputAction], int]:
    """Rejoue la résolution sur l'ensemble final des schémas de `raw/`.

    `existing` : instantané de `raw/` avant cette collecte (chemin → contenu) ;
    `writes`/`deletes` : actions des plugins de cette collecte. Rend la **nouvelle
    liste d'actions** (les écritures de schémas résolues, plus les schémas déjà
    présents dont une référence vient d'être résolue) et le nombre de références
    résolues.
    """
    removed = set(deletes)
    final: dict[str, str] = {
        path: content
        for path, content in existing.items()
        if is_schema_path(path) and path not in removed
    }
    for action in writes:
        if is_schema_path(action.path):
            final[action.path] = action.content

    index = SchemaIndex(final)
    counter = [0]
    rewritten: dict[str, str] = {}
    for path, content in final.items():
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            continue
        before = counter[0]
        resolved = _resolve_node(data, index, counter)
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
