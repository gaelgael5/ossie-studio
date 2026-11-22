"""Extracteur SQL : transforme un objet de catalogue normalisé (table, vue…) en
JSON Schema, selon les conventions. **Dialect-agnostique** : chaque moteur
(PostgreSQL, SQL Server, Snowflake) lit son catalogue et résout ses types en
fragments JSON Schema (il connaît ses types) ; cet extracteur assemble l'objet.

Il n'y a pas de fichier original : l'« original » d'un objet est sa définition
normalisée lue dans le catalogue, sérialisée de façon déterministe. Le socle de
collecte calcule le checksum sur cette forme — ajouter des **données** ne la
change pas, seule la **structure** compte.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ossie_studio.databases.base import CatalogObject
from ossie_studio.parsers.base import (
    CIBLE_RAW,
    ParsedObject,
    ParserError,
    PluginContext,
    PluginParam,
    PluginResult,
    matches_globs,
)
from ossie_studio.parsers.conventions import dump_schema

_SLUG = re.compile(r"[^a-zA-Z0-9]+")
_GLOBS = ["*"]

# Libellés d'affichage par dialecte.
_LABELS = {"postgres": "PostgreSQL", "sqlserver": "SQL Server", "snowflake": "Snowflake"}


def _slug(name: str) -> str:
    return _SLUG.sub("-", name).strip("-").lower() or "objet"


def source_key(obj: CatalogObject) -> str:
    """Clé d'un objet dans la table de travail : `schéma.objet`."""
    return f"{obj.schema}.{obj.name}"


def object_to_source(obj: CatalogObject) -> bytes:
    """Sérialise un objet de catalogue en JSON **déterministe** : c'est l'original
    sur lequel le socle calcule le checksum (structure seulement)."""
    mapping: dict[str, Any] = {
        "schema": obj.schema,
        "name": obj.name,
        "kind": obj.kind,
        "description": obj.description,
        "columns": [
            {
                "name": c.name,
                "schema": c.schema,
                "required": c.required,
                "position": c.position,
                "default": c.default,
                "description": c.description,
            }
            for c in obj.columns
        ],
        "primary_key": list(obj.primary_key),
        "unique_keys": [list(k) for k in obj.unique_keys],
        "foreign_keys": [
            {
                "columns": list(fk.columns),
                "ref_schema": fk.ref_schema,
                "ref_object": fk.ref_object,
                "ref_columns": list(fk.ref_columns),
                "enforced": fk.enforced,
            }
            for fk in obj.foreign_keys
        ],
        "depends_on": list(obj.depends_on),
        "extra": dict(obj.extra),
    }
    return (json.dumps(mapping, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _references_by_column(foreign_keys: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    refs: dict[str, list[dict[str, Any]]] = {}
    for fk in foreign_keys:
        ref_schema = fk.get("ref_schema", "")
        ref_object = fk.get("ref_object", "")
        ref_columns = fk.get("ref_columns") or []
        for i, local in enumerate(fk.get("columns") or []):
            ref_col = ref_columns[i] if i < len(ref_columns) else ""
            entry: dict[str, Any] = {"table": f"{ref_schema}.{ref_object}", "column": ref_col}
            if fk.get("enforced") is False:
                entry["enforced"] = False
            refs.setdefault(local, []).append(entry)
    return refs


def _build_schema(data: dict[str, Any]) -> dict[str, Any]:
    key = _slug(f"{data['schema']}.{data['name']}")
    schema: dict[str, Any] = {
        "$id": f"{key}.json",
        "title": data["name"],
        "x-kind": data["kind"],
    }
    if data.get("description"):
        schema["description"] = data["description"]
    for extra_key, extra_value in (data.get("extra") or {}).items():
        schema[extra_key] = extra_value

    schema["type"] = "object"
    refs = _references_by_column(data.get("foreign_keys") or [])
    properties: dict[str, Any] = {}
    required: list[str] = []
    for column in data.get("columns") or []:
        name = column["name"]
        fragment = dict(column.get("schema") or {})
        fragment["x-position"] = column["position"]
        if column.get("description"):
            fragment["description"] = column["description"]
        if column.get("default") is not None:
            fragment["x-default"] = column["default"]
        if name in refs:
            found = refs[name]
            fragment["x-references"] = found[0] if len(found) == 1 else found
        properties[name] = fragment
        if column.get("required"):
            required.append(name)
    if properties:
        schema["properties"] = properties
    if required:
        schema["required"] = sorted(required)
    if data.get("primary_key"):
        schema["x-primary-key"] = list(data["primary_key"])
    if data.get("unique_keys"):
        schema["x-unique-keys"] = [list(k) for k in data["unique_keys"]]
    if data.get("depends_on"):
        schema["x-depends-on"] = list(data["depends_on"])
    return schema


def build_object_schema(data: dict[str, Any]) -> dict[str, Any]:
    """Construit le JSON Schema d'un objet normalisé (table/vue). Partagé avec le
    parseur de projet SQL Server, qui enrichit `extra` et les fragments de
    colonnes (x-engine, x-source, contentSchema, x-derived-from…)."""
    return _build_schema(data)


class SqlExtractor:
    """Extracteur de catalogue SQL pour un dialecte. Ne lit jamais les données :
    il transforme des objets normalisés déjà lus en JSON Schema."""

    cible = CIBLE_RAW
    version = "1.0"

    def __init__(self, dialect: str) -> None:
        self.id = dialect
        self.label = _LABELS.get(dialect, dialect)

    def globs(self) -> list[str]:
        return list(_GLOBS)

    def parameters(self) -> list[PluginParam]:
        return []

    def run(self, context: PluginContext) -> PluginResult:
        from ossie_studio.sources.collect_core import schema_plugin_run

        return schema_plugin_run(context, self)

    def handles(self, path: str) -> bool:
        return matches_globs(_GLOBS, path)

    def parse(self, files: dict[str, bytes]) -> list[ParsedObject]:
        objects: list[ParsedObject] = []
        for path, raw in sorted(files.items()):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ParserError("unreadable", str(exc)) from exc
            schema = _build_schema(data)
            objects.append(
                ParsedObject(
                    key=_slug(f"{data['schema']}.{data['name']}"),
                    sources=[path],
                    schema=dump_schema(schema),
                )
            )
        return objects


def sql_extractor(dialect: str) -> SqlExtractor:
    return SqlExtractor(dialect)
