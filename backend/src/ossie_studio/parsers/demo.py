from __future__ import annotations

import json
import re

from ossie_studio.parsers.base import (
    CIBLE_RAW,
    ParsedObject,
    ParserError,
    PluginContext,
    PluginParam,
    PluginResult,
    matches_globs,
)
from ossie_studio.sources.collect_core import schema_plugin_run

_KEY_CLEAN = re.compile(r"[^a-zA-Z0-9]+")
_GLOBS = ["**/*.schema.json"]


def _key_from_path(path: str) -> str:
    """Nom d'objet stable dérivé du chemin du fichier (sans extension)."""
    stem = path
    for suffix in (".schema.json", ".json"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return _KEY_CLEAN.sub("-", stem).strip("-").lower() or "objet"


class DemoJsonSchemaParser:
    """Parseur de démonstration : lit les fichiers JSON Schema déjà présents dans
    le dépôt source (`*.schema.json`) et les restitue, normalisés de façon
    déterministe. Il éprouve la chaîne de collecte de bout en bout ; les parseurs
    réels (C#, Python…) ont leurs propres tickets."""

    id = "jsonschema-demo"
    version = "1.0"
    label = "Démo JSON Schema"
    cible = CIBLE_RAW

    def globs(self) -> list[str]:
        return list(_GLOBS)

    def parameters(self) -> list[PluginParam]:
        return []

    def run(self, context: PluginContext) -> PluginResult:
        return schema_plugin_run(context, self)

    def handles(self, path: str) -> bool:
        return matches_globs(_GLOBS, path)

    def parse(self, files: dict[str, bytes]) -> list[ParsedObject]:
        objects: list[ParsedObject] = []
        for path, raw in sorted(files.items()):
            try:
                data = json.loads(raw.decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise ParserError("unreadable") from exc
            except json.JSONDecodeError as exc:
                raise ParserError("syntax") from exc
            # Sortie déterministe : clés triées, indentation fixe, newline final.
            schema = json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
            objects.append(ParsedObject(key=_key_from_path(path), sources=[path], schema=schema))
        return objects
