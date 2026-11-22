"""Lecture des contrats OpenAPI et parseur `openapi`.

Un contrat OpenAPI est **subi** (imposé par un système tiers), **importé et jamais
édité** : plusieurs versions peuvent coexister. Ce module sait :

- analyser un fichier déposé (version OpenAPI, titre, version déclarée, comptes,
  références externes) pour l'afficher *avant* de l'enregistrer, et le refuser
  s'il est invalide (Swagger 2.0, ni YAML ni JSON, référence interne absente) ;
- tirer d'un contrat **un schéma par objet** de `components/schemas`, converti en
  JSON Schema 2020-12, chaque schéma portant son origine (`x-source`) et les
  opérations qui le reçoivent ou le renvoient (`x-used-by`).

La **version du contrat** entre dans l'identité des schémas : `Commande` en v1 et
en v2 donnent deux schémas distincts, parce que les deux versions coexistent.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

import yaml

from ossie_studio.parsers.base import (
    CIBLE_RAW,
    ParsedObject,
    ParserError,
    PluginContext,
    PluginParam,
    PluginResult,
    matches_globs,
)
from ossie_studio.parsers.conventions import STANDARD_KEYWORDS, dump_schema

OPENAPI_PARSER_ID = "openapi"
# 1.1 : `x-source` porte `file` / `pointer` (au lieu de `fichier` / `pointeur`).
# Le changement de version force la régénération des schémas déjà collectés.
OPENAPI_PARSER_VERSION = "1.1"
_GLOBS = ["*.yaml", "*.yml", "*.json"]

_SCHEMA_REF_PREFIX = "#/components/schemas/"
_HTTP_METHODS = ("get", "put", "post", "delete", "patch", "options", "head", "trace")
_NAME_CLEAN = re.compile(r"[^a-zA-Z0-9]+")
_VERSION_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")


class OpenApiError(Exception):
    """Contrat refusé au dépôt, porté par un code stable :
    `not_openapi`, `yaml_invalid`, `swagger_unsupported`, `ref_missing`.
    `detail` porte la précision affichable (p. ex. le nom de l'objet absent)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass
class Analysis:
    """Ce que l'analyse d'un contrat montre avant l'enregistrement."""

    openapi: str
    title: str
    declared_version: str
    schema_count: int
    operation_count: int
    external_refs: list[str] = field(default_factory=list)

    @property
    def proposed_version(self) -> str:
        return proposed_version_name(self.declared_version)


def is_valid_version_name(name: str) -> bool:
    """Nom de version rangé : minuscules, chiffres, points et tirets, sans bord."""
    return bool(name) and len(name) <= 40 and bool(_VERSION_RE.match(name))


def proposed_version_name(declared: str) -> str:
    """Nom de version proposé à partir de `info.version` : la majeure préfixée de
    `v` (`4.0.0` → `v4`). Vide ou illisible → `v1`."""
    head = (declared or "").strip().lstrip("vV")
    first = re.split(r"[.\-]", head, maxsplit=1)[0]
    major = re.sub(r"[^0-9]", "", first)
    return f"v{major}" if major else "v1"


def _slug(name: str) -> str:
    return _NAME_CLEAN.sub("-", name).strip("-").lower() or "objet"


def load_document(raw: bytes) -> dict[str, Any]:
    """Charge un contrat (YAML ou JSON — YAML est un sur-ensemble de JSON) et
    s'assure que c'est un document OpenAPI 3.x. Lève OpenApiError sinon."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OpenApiError("yaml_invalid") from exc
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise OpenApiError("yaml_invalid") from exc
    if not isinstance(doc, dict):
        raise OpenApiError("not_openapi")
    if "swagger" in doc:
        raise OpenApiError("swagger_unsupported", str(doc.get("swagger")))
    version = doc.get("openapi")
    if not isinstance(version, str) or not version.startswith("3."):
        raise OpenApiError("not_openapi")
    return doc


def _major(doc: dict[str, Any]) -> str:
    """`3.0` ou `3.1`, pour choisir la conversion."""
    version = str(doc.get("openapi", ""))
    parts = version.split(".")
    return f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else version


def _schemas(doc: dict[str, Any]) -> dict[str, Any]:
    components = doc.get("components")
    schemas = components.get("schemas") if isinstance(components, dict) else None
    return schemas if isinstance(schemas, dict) else {}


def _iter_refs(node: Any) -> Iterable[str]:
    """Tous les `$ref` (chaînes) d'un sous-arbre."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            yield ref
        for key, value in node.items():
            if key != "$ref":
                yield from _iter_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_refs(item)


def _operations(doc: dict[str, Any]) -> list[tuple[str, set[str]]]:
    """Pour chaque opération : son libellé (`POST /commandes`) et les objets de
    `components/schemas` qu'elle reçoit (corps) ou renvoie (réponses)."""
    out: list[tuple[str, set[str]]] = []
    paths = doc.get("paths")
    if not isinstance(paths, dict):
        return out
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method in _HTTP_METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            names: set[str] = set()
            for part in (op.get("requestBody"), op.get("responses")):
                for ref in _iter_refs(part):
                    if ref.startswith(_SCHEMA_REF_PREFIX):
                        names.add(ref[len(_SCHEMA_REF_PREFIX) :])
            out.append((f"{method.upper()} {path}", names))
    return out


def _external_refs(schemas: dict[str, Any]) -> list[str]:
    """Références qui sortent du contrat (autre fichier, URL) — non suivies."""
    externals: set[str] = set()
    for schema in schemas.values():
        for ref in _iter_refs(schema):
            if not ref.startswith("#"):
                externals.add(ref)
    return sorted(externals)


def _check_internal_refs(schemas: dict[str, Any]) -> None:
    """Refuse un contrat dont un `$ref` interne vise un objet de
    `components/schemas` absent (critère : référence vers un objet absent)."""
    for schema in schemas.values():
        for ref in _iter_refs(schema):
            if ref.startswith(_SCHEMA_REF_PREFIX):
                target = ref[len(_SCHEMA_REF_PREFIX) :]
                if target not in schemas:
                    raise OpenApiError("ref_missing", ref)


def analyze(raw: bytes) -> Analysis:
    """Analyse un contrat déposé. Lève OpenApiError si le contrat est à refuser."""
    doc = load_document(raw)
    schemas = _schemas(doc)
    _check_internal_refs(schemas)
    raw_info = doc.get("info")
    info: dict[str, Any] = raw_info if isinstance(raw_info, dict) else {}
    return Analysis(
        openapi=str(doc.get("openapi", "")),
        title=str(info.get("title", "")),
        declared_version=str(info.get("version", "")),
        schema_count=len(schemas),
        operation_count=len(_operations(doc)),
        external_refs=_external_refs(schemas),
    )


def _dest_name(version: str, schema_name: str) -> str:
    """Nom du fichier schéma dans `raw/`, préfixé par la version du contrat."""
    return f"{_slug(version)}-{_slug(schema_name)}.json"


def _convert(node: Any, *, major: str, version: str, names: set[str]) -> Any:
    """Convertit récursivement un nœud OpenAPI en JSON Schema 2020-12.

    - OpenAPI 3.1 est déjà du JSON Schema 2020-12 : on ne touche qu'aux `$ref`.
    - OpenAPI 3.0 : `nullable`, `example`, `exclusiveMinimum/Maximum` booléens.
    - `$ref` interne `#/components/schemas/X` → `$ref` vers le fichier généré de X.
    - `$ref` externe → non suivi, remplacé par `x-unresolved-type`.
    """
    if isinstance(node, list):
        return [_convert(item, major=major, version=version, names=names) for item in node]
    if not isinstance(node, dict):
        return node

    ref = node.get("$ref")
    if isinstance(ref, str):
        rest = {k: v for k, v in node.items() if k != "$ref"}
        ref_nullable = major == "3.0" and rest.pop("nullable", None) is True
        converted_rest = _convert(rest, major=major, version=version, names=names)
        if ref.startswith(_SCHEMA_REF_PREFIX) and ref[len(_SCHEMA_REF_PREFIX) :] in names:
            ref_node: dict[str, Any] = {"$ref": _dest_name(version, ref[len(_SCHEMA_REF_PREFIX) :])}
        else:
            # Objet absent, référence interne hors components/schemas, ou référence
            # externe (autre fichier / URL) : non suivie.
            ref_node = {"x-unresolved-type": ref}
        merged = {**ref_node, **converted_rest}
        return {"anyOf": [merged, {"type": "null"}]} if ref_nullable else merged

    skip: set[str] = set()
    override: dict[str, Any] = {}
    nullable = major == "3.0" and node.get("nullable") is True
    if major == "3.0":
        skip.add("nullable")
        for ex_key, bound in (("exclusiveMinimum", "minimum"), ("exclusiveMaximum", "maximum")):
            ex = node.get(ex_key)
            if isinstance(ex, bool):
                if ex and bound in node:
                    # Forme booléenne 3.0 → forme numérique 2020-12.
                    override[ex_key] = node[bound]
                    skip.add(bound)
                else:
                    skip.add(ex_key)
    # `example` (singulier, OpenAPI) → `examples` (standard 2020-12), quel que soit
    # le major, pour rester conforme aux conventions.
    if "example" in node:
        override["examples"] = [node["example"]]
        skip.add("example")

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in skip:
            continue
        source = override[key] if key in override else value
        out[_norm_key(key)] = _convert_value(key, source, major=major, version=version, names=names)
    for key, value in override.items():
        out.setdefault(_norm_key(key), value)

    if nullable:
        out = _apply_null(out)
    return out


# Positions de (sous-)schémas, pour descendre sans toucher aux noms de propriétés
# ni aux valeurs de données (enum, const, type, required…).
_SCHEMA_VALUE = ("items", "additionalProperties", "not", "if", "then", "else", "contains",
                 "propertyNames", "unevaluatedItems", "unevaluatedProperties", "contentSchema")
_SCHEMA_LIST = ("allOf", "anyOf", "oneOf", "prefixItems")
_SCHEMA_MAP = ("properties", "patternProperties", "$defs", "dependentSchemas")


def _convert_value(key: str, value: Any, *, major: str, version: str, names: set[str]) -> Any:
    """Transforme la valeur d'une clef selon sa position : sous-schéma, liste de
    sous-schémas, carte nom→sous-schéma, ou donnée (laissée telle quelle)."""
    if key in _SCHEMA_MAP and isinstance(value, dict):
        return {
            name: _convert(sub, major=major, version=version, names=names)
            for name, sub in value.items()
        }
    if key in _SCHEMA_LIST and isinstance(value, list):
        return [_convert(item, major=major, version=version, names=names) for item in value]
    if key in _SCHEMA_VALUE:
        return _convert(value, major=major, version=version, names=names)
    # Donnée (type, enum, required, examples…) ou clef inconnue : valeur gardée.
    return value


def _norm_key(key: str) -> str:
    """Toute clef non standard (ni mot-clef 2020-12, ni `$`, ni `x-`) — p. ex.
    `discriminator`, `xml` — est préfixée `x-` pour respecter les conventions."""
    if key in STANDARD_KEYWORDS or key.startswith(("$", "x-")):
        return key
    return f"x-{key}"


def _apply_null(schema: dict[str, Any]) -> dict[str, Any]:
    """Rend un schéma 3.0 `nullable` admettant `null` en 2020-12."""
    node_type = schema.get("type")
    if isinstance(node_type, str):
        return {**schema, "type": [node_type, "null"]}
    if isinstance(node_type, list):
        if "null" not in node_type:
            return {**schema, "type": [*node_type, "null"]}
        return schema
    # Pas de `type` explicite (p. ex. un `$ref` déjà transformé, un `allOf`) :
    # on autorise `null` par une alternative.
    return {"anyOf": [schema, {"type": "null"}]}


def _contract_schemas(filename: str, raw: bytes) -> list[ParsedObject]:
    """Les schémas tirés d'un contrat. `filename` nomme la version (son radical :
    `v1.yaml` → `v1`)."""
    try:
        doc = load_document(raw)
    except OpenApiError as exc:
        raise ParserError("syntax", exc.detail or exc.code) from exc
    version = PurePosixPath(filename).stem
    major = _major(doc)
    schemas = _schemas(doc)
    names = set(schemas)

    used_by: dict[str, list[str]] = {}
    for label, referenced in _operations(doc):
        for name in referenced:
            used_by.setdefault(name, []).append(label)

    objects: list[ParsedObject] = []
    for name in sorted(schemas):
        body = _convert(schemas[name], major=major, version=version, names=names)
        if not isinstance(body, dict):
            body = {"allOf": [body]}
        body["$id"] = _dest_name(version, name)
        body["x-source"] = {"file": filename, "pointer": f"{_SCHEMA_REF_PREFIX}{name}"}
        if name in used_by:
            body["x-used-by"] = sorted(used_by[name])
        objects.append(
            ParsedObject(
                key=f"{_slug(version)}-{_slug(name)}",
                sources=[filename],
                schema=dump_schema(body),
            )
        )
    return objects


class OpenApiParser:
    """Parseur `openapi` : d'un contrat déposé, tire un schéma par objet de
    `components/schemas`, converti en JSON Schema 2020-12 et marqué de son origine.
    Appliqué aux seules sources de type `openapi` (fichiers de `contrats/`)."""

    id = OPENAPI_PARSER_ID
    version = OPENAPI_PARSER_VERSION
    label = "Contrats OpenAPI"
    cible = CIBLE_RAW

    def globs(self) -> list[str]:
        return list(_GLOBS)

    def parameters(self) -> list[PluginParam]:
        return []

    def run(self, context: PluginContext) -> PluginResult:
        # Import tardif : évite un cycle base ← collect_core ← openapi.
        from ossie_studio.sources.collect_core import schema_plugin_run

        return schema_plugin_run(context, self)

    def handles(self, path: str) -> bool:
        return matches_globs(_GLOBS, path.lower())

    def parse(self, files: dict[str, bytes]) -> list[ParsedObject]:
        objects: list[ParsedObject] = []
        for filename, raw in sorted(files.items()):
            objects.extend(_contract_schemas(filename, raw))
        return objects
