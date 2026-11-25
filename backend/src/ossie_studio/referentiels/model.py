"""Le modèle d'un référentiel : un JSON Schema 2020-12 de type `object`, écrit
en YAML (`modele.schema.yaml`), avec sa clé sous `x-key`.

Types de propriété acceptés, à toute profondeur : chaîne (éventuellement
`format: date` ou `date-time`, ou `enum` de chaînes), entier, nombre, booléen,
**objet** et **tableau** (d'objets ou de valeurs simples). Gael : « comment ça
ferait si l'objet avait un tableau d'objets ? ».

La clé (`x-key`) est une liste ordonnée d'une ou plusieurs propriétés de premier
niveau (clé composée possible), **simples** et **obligatoires**.

Sortie déterministe : les propriétés gardent leur ordre de déclaration ; même
modèle, mêmes octets.
"""

from __future__ import annotations

from typing import Any

import yaml

from ossie_studio.parsers.conventions import SchemaReadError, load_schema
from ossie_studio.referentiels.errors import Problem, ReferentielError

KEY_KEYWORD = "x-key"
SIMPLE_TYPES = frozenset({"string", "integer", "number", "boolean"})
STRING_FORMATS = frozenset({"date", "date-time"})
# Séparateur des chemins affichés : « FR+site \u203a paliers[2] \u203a delai_jours ».
SEPARATOR = " \u203a "

_BaseDumper = getattr(yaml, "CSafeDumper", yaml.SafeDumper)


class _ModelDumper(_BaseDumper):  # type: ignore[misc,valid-type]
    """Un texte sur plusieurs lignes (description) en bloc littéral `|`."""


def _str_representer(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_ModelDumper.add_representer(str, _str_representer)


def _join(path: str, name: str) -> str:
    return f"{path}{SEPARATOR}{name}" if path else name


def is_simple(schema: Any) -> bool:
    """Une propriété simple : chaîne (date, énumération comprises), entier,
    nombre ou booléen. Seules les propriétés simples peuvent former la clé."""
    return isinstance(schema, dict) and schema.get("type") in SIMPLE_TYPES


def _check_property(schema: Any, path: str) -> list[Problem]:
    if not isinstance(schema, dict):
        return [Problem(path, "property_invalid")]
    kind = schema.get("type")
    if kind == "string":
        problems: list[Problem] = []
        fmt = schema.get("format")
        if fmt is not None and fmt not in STRING_FORMATS:
            problems.append(Problem(path, "property_format_unsupported", str(fmt)))
        if "enum" in schema:
            enum = schema["enum"]
            if (
                not isinstance(enum, list)
                or not enum
                or not all(isinstance(v, str) for v in enum)
                or len(set(enum)) != len(enum)
            ):
                problems.append(Problem(path, "property_enum_invalid"))
        return problems
    if kind in ("integer", "number", "boolean"):
        return []
    if kind == "object":
        return _check_object(schema, path)
    if kind == "array":
        items = schema.get("items")
        if not isinstance(items, dict):
            return [Problem(path, "property_items_missing")]
        return _check_property(items, f"{path}[]")
    return [Problem(path, "property_type_unsupported", str(kind))]


def _check_object(schema: dict[str, Any], path: str) -> list[Problem]:
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return [Problem(path, "object_without_properties")]
    problems: list[Problem] = []
    for name, sub in properties.items():
        problems.extend(_check_property(sub, _join(path, str(name))))
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(r, str) for r in required):
        problems.append(Problem(path, "required_invalid"))
    else:
        for name in required:
            if name not in properties:
                problems.append(Problem(_join(path, name), "required_unknown"))
    return problems


def check_model(model: Any) -> list[Problem]:
    """Les problèmes du modèle (liste vide : modèle valide)."""
    if not isinstance(model, dict) or model.get("type") != "object":
        return [Problem("", "model_not_object")]
    problems = _check_object(model, "")
    raw_properties = model.get("properties")
    properties: dict[str, Any] = raw_properties if isinstance(raw_properties, dict) else {}
    raw_required = model.get("required")
    required: list[Any] = raw_required if isinstance(raw_required, list) else []
    key = model.get(KEY_KEYWORD)
    if (
        not isinstance(key, list)
        or not key
        or not all(isinstance(k, str) for k in key)
        or len(set(key)) != len(key)
    ):
        problems.append(Problem(KEY_KEYWORD, "key_invalid"))
        return problems
    for name in key:
        prop = properties.get(name)
        where = _join(KEY_KEYWORD, name)
        if prop is None:
            problems.append(Problem(where, "key_property_unknown"))
        elif not is_simple(prop):
            problems.append(Problem(where, "key_property_not_simple"))
        elif name not in required:
            problems.append(Problem(where, "key_property_optional"))
    return problems


def validate_model(model: Any) -> dict[str, Any]:
    """Le modèle s'il est valide ; sinon ReferentielError « model_invalid »."""
    problems = check_model(model)
    if problems:
        raise ReferentielError("model_invalid", problems=problems)
    assert isinstance(model, dict)
    return model


def model_key(model: dict[str, Any]) -> list[str]:
    key = model.get(KEY_KEYWORD)
    return [str(k) for k in key] if isinstance(key, list) else []


def load_model(text: str | bytes) -> Any:
    """Lit un modèle YAML (sans conversion implicite des dates). Lève
    ReferentielError « model_unreadable » si le texte est illisible."""
    try:
        return load_schema(text)
    except SchemaReadError as exc:
        raise ReferentielError("model_unreadable", detail=str(exc)) from None


def dump_model(model: dict[str, Any]) -> str:
    """Le modèle en YAML, propriétés dans l'ordre de déclaration : même modèle,
    mêmes octets."""
    return yaml.dump(
        model,
        Dumper=_ModelDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=1_000_000,
    )
