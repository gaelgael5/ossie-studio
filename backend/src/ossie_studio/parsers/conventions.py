"""Conventions des schémas générés, communes à tous les parseurs et extracteurs.

Ce module fixe **ce que produit** tout parseur (un fichier C#, une table SQL, un
contrat OpenAPI) pour que les schémas se lisent, se comparent et se relient de la
même façon :

- JSON Schema 2020-12, un fichier par objet, **identité stable** (`$id`) ;
- toute clef non standard commence par `x-` (métadonnées d'origine) ;
- **visibilité** (`x-visibility`) obligatoire sur un objet issu d'un parseur de
  **code** (repéré par `x-language`), parmi public / internal / protected /
  private ; absente pour le SQL et les contrats ;
- **sortie déterministe** : clefs triées, fin de ligne et encodage fixés.

`validate_schema` refuse un schéma non conforme ; la collecte signale alors le
fichier d'origine et la règle violée, et n'écrit rien. `dump_schema` sérialise de
façon déterministe (deux lectures du même original → mêmes octets).
"""

from __future__ import annotations

import json
from typing import Any

# Vocabulaire JSON Schema 2020-12 (mots-clefs standard). Toute autre clef doit
# commencer par `$` (noyau) ou `x-` (extension), sinon elle est refusée.
STANDARD_KEYWORDS: frozenset[str] = frozenset(
    {
        # noyau
        "$schema", "$id", "$ref", "$defs", "$anchor", "$dynamicRef",
        "$dynamicAnchor", "$vocabulary", "$comment",
        # applicateurs
        "allOf", "anyOf", "oneOf", "not", "if", "then", "else",
        "dependentSchemas", "prefixItems", "items", "contains",
        "properties", "patternProperties", "additionalProperties",
        "propertyNames", "unevaluatedItems", "unevaluatedProperties",
        # validation
        "type", "enum", "const", "multipleOf", "maximum", "exclusiveMaximum",
        "minimum", "exclusiveMinimum", "maxLength", "minLength", "pattern",
        "maxItems", "minItems", "uniqueItems", "maxContains", "minContains",
        "maxProperties", "minProperties", "required", "dependentRequired",
        # annotations et format
        "title", "description", "default", "deprecated", "readOnly", "writeOnly",
        "examples", "format", "contentEncoding", "contentMediaType", "contentSchema",
    }
)

# Clefs dont la valeur est un (ou des) sous-schéma(s) : on y descend pour valider.
_SCHEMA_VALUE = (
    "items", "contains", "additionalProperties", "propertyNames", "not",
    "if", "then", "else", "unevaluatedItems", "unevaluatedProperties", "contentSchema",
)
_SCHEMA_LIST = ("allOf", "anyOf", "oneOf", "prefixItems")
_SCHEMA_MAP = ("properties", "patternProperties", "$defs", "dependentSchemas")

VISIBILITIES: frozenset[str] = frozenset({"public", "internal", "protected", "private"})
CODE_LANGUAGE_KEY = "x-language"
VISIBILITY_KEY = "x-visibility"


class ConventionError(Exception):
    """Schéma non conforme aux conventions, porté par un code de règle stable
    (missing_id, unknown_key, not_object, visibility_missing, visibility_invalid)."""

    def __init__(self, rule: str, detail: str = "") -> None:
        super().__init__(rule)
        self.rule = rule
        self.detail = detail


def _check_keys(node: dict[str, Any]) -> None:
    """Toute clef d'un nœud de schéma est standard, de noyau (`$`) ou `x-`."""
    for key in node:
        if key in STANDARD_KEYWORDS or key.startswith("$") or key.startswith("x-"):
            continue
        raise ConventionError("unknown_key", key)


def _walk(node: Any) -> None:
    """Valide récursivement les clefs des nœuds en position de schéma, sans
    descendre dans les valeurs de données (enum, const, default, examples, x-*)."""
    if not isinstance(node, dict):
        return
    _check_keys(node)
    for key in _SCHEMA_VALUE:
        if key in node and isinstance(node[key], dict):
            _walk(node[key])
    for key in _SCHEMA_LIST:
        for item in node.get(key, []) or []:
            _walk(item)
    for key in _SCHEMA_MAP:
        for value in (node.get(key) or {}).values():
            _walk(value)


def validate_schema(schema: dict[str, Any]) -> None:
    """Vérifie qu'un schéma respecte les conventions. Lève ConventionError sinon."""
    if not isinstance(schema, dict):
        raise ConventionError("not_object")
    identity = schema.get("$id")
    if not isinstance(identity, str) or not identity.strip():
        raise ConventionError("missing_id")
    _walk(schema)
    # Visibilité obligatoire pour un objet issu d'un parseur de code.
    if CODE_LANGUAGE_KEY in schema:
        _check_visibility(schema, "visibility_missing", "visibility_invalid")
        for name, field in (schema.get("properties") or {}).items():
            if isinstance(field, dict):
                _check_visibility(field, "field_visibility_missing", "visibility_invalid", name)


def _check_visibility(node: dict[str, Any], missing: str, invalid: str, detail: str = "") -> None:
    value = node.get(VISIBILITY_KEY)
    if value is None:
        raise ConventionError(missing, detail)
    if value not in VISIBILITIES:
        raise ConventionError(invalid, str(value))


def dump_schema(schema: dict[str, Any]) -> str:
    """Sérialisation déterministe d'un schéma : clefs triées, indentation fixe,
    Unicode conservé, fin de ligne finale. Deux appels donnent les mêmes octets."""
    return json.dumps(schema, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
