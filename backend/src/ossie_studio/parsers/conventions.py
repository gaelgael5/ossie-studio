"""Conventions des schémas générés, communes à tous les parseurs et extracteurs.

Ce module fixe **ce que produit** tout parseur (un fichier C#, une table SQL, un
contrat OpenAPI) pour que les schémas se lisent, se comparent et se relient de la
même façon :

- JSON Schema 2020-12, un fichier par objet, **identité stable** (`$id`) ;
- toute clef non standard commence par `x-` (métadonnées d'origine) ;
- **visibilité** (`x-visibility`) obligatoire sur un objet issu d'un parseur de
  **code** (repéré par `x-language`), parmi public / internal / protected /
  private ; absente pour le SQL et les contrats ;
- **sortie déterministe** : clefs triées, fin de ligne et encodage fixés ;
- **fichier YAML** `<clé>.schema.yaml` (ticket docflow 5cc1cb07, Gael 23/11/2026 :
  « tu vas passer tous les raw qui sont en json en yaml ») ; le suffixe double
  évite la collision avec les index YAML de `raw/` (`manifest.yaml`…).

`validate_schema` refuse un schéma non conforme ; la collecte signale alors le
fichier d'origine et la règle violée, et n'écrit rien. `dump_schema` sérialise de
façon déterministe (deux lectures du même original → mêmes octets).
"""

from __future__ import annotations

from typing import Any

import yaml

# Suffixe d'un fichier de schéma (raw comme asset) ; `$id` et `$ref` le portent.
SCHEMA_SUFFIX = ".schema.yaml"


def schema_file(key: str) -> str:
    """Nom du fichier (et `$id`) du schéma de clé `key`."""
    return f"{key}{SCHEMA_SUFFIX}"


def schema_key(name: str) -> str:
    """Clé d'un nom de fichier de schéma (`order.schema.yaml` → `order`)."""
    return name[: -len(SCHEMA_SUFFIX)] if name.endswith(SCHEMA_SUFFIX) else name


def is_schema_name(name: str) -> bool:
    """Un fichier de schéma directement dans son dossier (`raw/`, `assets/`)."""
    return name.endswith(SCHEMA_SUFFIX) and "/" not in name


_BaseLoader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_BaseDumper = getattr(yaml, "CSafeDumper", yaml.SafeDumper)


class _SchemaDumper(_BaseDumper):  # type: ignore[misc,valid-type]
    """Écriture des schémas : un texte sur plusieurs lignes (description
    Markdown, diagramme Mermaid) en bloc littéral `|`, lisible dans un diff."""


def _str_representer(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_SchemaDumper.add_representer(str, _str_representer)


class _SchemaLoader(_BaseLoader):  # type: ignore[misc,valid-type]
    """Lecture sûre SANS conversion implicite des dates : une valeur comme
    `2026-11-22` reste une chaîne, comme en JSON (sinon un objet date casse la
    mise en index et les comparaisons)."""


_SchemaLoader.yaml_implicit_resolvers = {
    first: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for first, resolvers in _BaseLoader.yaml_implicit_resolvers.items()
}


class SchemaReadError(ValueError):
    """Contenu de schéma illisible (YAML invalide)."""


def load_schema(text: str | bytes) -> Any:
    """Lit un schéma (YAML ; un JSON, sous-ensemble de YAML, se lit aussi).
    Lève SchemaReadError si le texte est illisible. Le résultat n'est pas
    forcément un objet : à l'appelant de vérifier."""
    try:
        return yaml.load(text, Loader=_SchemaLoader)  # noqa: S506 — chargeur sûr
    except yaml.YAMLError as exc:
        raise SchemaReadError(str(exc)) from None

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
    """Sérialisation déterministe d'un schéma en YAML : clefs triées, blocs (pas
    de style en ligne), Unicode conservé, aucune ligne repliée, fin de ligne
    finale. Deux appels donnent les mêmes octets (pas de faux diff à chaque
    collecte)."""
    return yaml.dump(
        schema,
        Dumper=_SchemaDumper,
        sort_keys=True,
        allow_unicode=True,
        default_flow_style=False,
        width=1_000_000,
    )
