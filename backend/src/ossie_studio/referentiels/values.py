"""Les valeurs d'un référentiel (`valeurs.json`) : un tableau d'objets conformes
au modèle, à toute profondeur, dont la clé (composée ou non) est unique.

Chaque problème est localisé par la clé de la valeur puis le chemin dans
l'objet, séparés par `model.SEPARATOR` (la clé « FR+site », puis « paliers[2] »,
puis « delai_jours »). Une valeur sans clé lisible est désignée par son rang
(`#3`).

Les objets du modèle sont **fermés** : une propriété non déclarée est refusée
(une donnée de référence n'a pas de champ imprévu).

Sortie déterministe : valeurs triées par clé, clés JSON triées dans chaque
objet, ordre des éléments d'un tableau conservé ; même contenu, mêmes octets.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from ossie_studio.referentiels.errors import Problem, ReferentielError
from ossie_studio.referentiels.model import SEPARATOR, model_key

# Formats vérifiés sans dépendance de plus (celui de jsonschema ignore
# `date-time` quand rfc3339-validator est absent).
_FORMATS = FormatChecker(formats=())


@_FORMATS.checks("date", raises=ValueError)
def _is_date(value: object) -> bool:
    if not isinstance(value, str):
        return True
    return len(value) == 10 and bool(date.fromisoformat(value))


@_FORMATS.checks("date-time", raises=ValueError)
def _is_date_time(value: object) -> bool:
    if not isinstance(value, str):
        return True
    if "T" not in value.upper():
        raise ValueError(value)
    return bool(datetime.fromisoformat(value.replace("Z", "+00:00").replace("z", "+00:00")))


def _closed(schema: Any) -> Any:
    """Copie du modèle où chaque objet refuse les propriétés non déclarées."""
    if not isinstance(schema, dict):
        return schema
    out = {k: v for k, v in schema.items()}
    if out.get("type") == "object":
        out.setdefault("additionalProperties", False)
        props = out.get("properties")
        if isinstance(props, dict):
            out["properties"] = {name: _closed(sub) for name, sub in props.items()}
    if out.get("type") == "array" and isinstance(out.get("items"), dict):
        out["items"] = _closed(out["items"])
    return out


def _validator(model: dict[str, Any]) -> Draft202012Validator:
    return Draft202012Validator(_closed(copy.deepcopy(model)), format_checker=_FORMATS)


def _shown(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def key_of(value: Any, key: list[str]) -> tuple[Any, ...] | None:
    """La clé d'une valeur (None si une partie manque ou n'est pas simple)."""
    if not isinstance(value, dict):
        return None
    parts = []
    for name in key:
        part = value.get(name)
        if part is None or isinstance(part, dict | list):
            return None
        parts.append(part)
    return tuple(parts)


def key_label(value: Any, key: list[str], index: int) -> str:
    """« FR+site » pour une clé composée ; « #3 » si la clé est illisible."""
    parts = key_of(value, key)
    if parts is None:
        return f"#{index + 1}"
    return "+".join(_shown(p) for p in parts)


def field_path(parts: Iterable[str | int]) -> str:
    """Le chemin d'un champ dans une valeur, séparé par `SEPARATOR` (« paliers[2] » puis
    « delai_jours »)."""
    out = ""
    for part in parts:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f"{SEPARATOR}{part}" if out else str(part)
    return out


def _join(label: str, field: str) -> str:
    return f"{label}{SEPARATOR}{field}" if field else label


def _child(field: str, name: str) -> str:
    return f"{field}{SEPARATOR}{name}" if field else name


def _problems(label: str, index: int, error: ValidationError) -> list[Problem]:
    field = field_path(error.absolute_path)
    validator = error.validator
    instance = error.instance

    def at(where: str, code: str, detail: str = "") -> Problem:
        return Problem(_join(label, where), code, detail, index, where)

    if validator == "required" and isinstance(instance, dict):
        required = error.validator_value if isinstance(error.validator_value, list) else []
        return [
            at(_child(field, name), "value_required") for name in required if name not in instance
        ]
    if validator == "additionalProperties" and isinstance(instance, dict):
        declared = error.schema.get("properties", {}) if isinstance(error.schema, dict) else {}
        return [
            at(_child(field, name), "value_unknown_property")
            for name in sorted(instance)
            if name not in declared
        ]
    if validator == "type":
        return [at(field, "value_type", str(error.validator_value))]
    if validator == "enum":
        return [at(field, "value_enum", _shown(instance))]
    if validator == "format":
        return [at(field, "value_format", str(error.validator_value))]
    return [at(field, f"value_{validator}")]


def check_values(model: dict[str, Any], values: Any) -> list[Problem]:
    """Les problèmes des valeurs (liste vide : valeurs valides). Le modèle doit
    être valide (`model.validate_model`)."""
    if not isinstance(values, list):
        return [Problem("", "values_not_list")]
    key = model_key(model)
    validator = _validator(model)
    problems: list[Problem] = []
    seen: dict[tuple[Any, ...], str] = {}
    for index, value in enumerate(values):
        label = key_label(value, key, index)
        for error in validator.iter_errors(value):
            problems.extend(_problems(label, index, error))
        parts = key_of(value, key)
        if parts is None:
            continue
        if parts in seen:
            problems.append(Problem(label, "key_duplicate", label, index))
        else:
            seen[parts] = label
    return sorted(set(problems))


def validate_values(model: dict[str, Any], values: Any) -> list[dict[str, Any]]:
    """Les valeurs si elles sont valides ; sinon ReferentielError « values_invalid »."""
    problems = check_values(model, values)
    if problems:
        raise ReferentielError("values_invalid", problems=problems)
    assert isinstance(values, list)
    return values


def _sort_part(part: Any) -> tuple[int, Any]:
    """Ordre stable entre types simples : booléens, nombres, chaînes."""
    if isinstance(part, bool):
        return (0, part)
    if isinstance(part, int | float):
        return (1, part)
    return (2, str(part))


def sorted_values(model: dict[str, Any], values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    key = model_key(model)

    def order(value: dict[str, Any]) -> tuple[tuple[int, Any], ...]:
        parts = key_of(value, key) or ()
        return tuple(_sort_part(p) for p in parts)

    return sorted(values, key=order)


def dump_values(model: dict[str, Any], values: list[dict[str, Any]]) -> str:
    """Les valeurs en JSON : triées par clé, clés triées dans chaque objet,
    tableaux dans leur ordre ; même contenu, mêmes octets."""
    text = json.dumps(sorted_values(model, values), sort_keys=True, ensure_ascii=False, indent=2)
    return text + "\n"


def load_values(text: str | bytes) -> Any:
    """Lit `valeurs.json`. Lève ReferentielError « values_unreadable » si le
    texte n'est pas du JSON."""
    try:
        return json.loads(text)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ReferentielError("values_unreadable", detail=str(exc)) from None
