"""Règles de tri d'une source : écarter en masse le bruit (schémas `STAGING`,
classes `*Dto`, champs techniques `ETL_LOAD_TS`…).

Les règles **ne suppriment rien** : `raw/` reste complet. Elles filtrent
l'affichage de la curation et de la revue. Elles vivent dans le manifeste de la
source (section `tri`) ; ce module ne fait que les valider et les évaluer — pur,
sans accès disque.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Any

# Ce sur quoi porte une règle. Les quatre premières écartent un **objet** entier ;
# `field-name` écarte un **champ** d'un objet.
TRI_TARGETS = ("object-name", "object-type", "field-name", "file-path", "sql-schema")
TRI_KINDS = ("glob", "regex")
_RULE_KEYS = {"target", "pattern", "kind", "label"}


@dataclass(frozen=True)
class TriRule:
    target: str
    pattern: str
    kind: str  # glob | regex
    label: str = ""  # libellé libre (affichage), optionnel


def validate_tri_rules(raw: Any) -> list[str]:
    """Liste des erreurs d'une section `triage` (vide = valide). Chaque erreur
    **nomme la règle** par son index, pour un refus lisible."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        return ["triage_invalid"]
    errors: list[str] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(f"triage[{i}]:invalid")
            continue
        if set(item) - _RULE_KEYS:
            errors.append(f"triage[{i}]:unknown_key")
        if item.get("target") not in TRI_TARGETS:
            errors.append(f"triage[{i}]:target_invalid")
        pattern = item.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            errors.append(f"triage[{i}]:pattern_required")
        kind = item.get("kind")
        if kind not in TRI_KINDS:
            errors.append(f"triage[{i}]:kind_invalid")
        elif kind == "regex" and isinstance(pattern, str):
            try:
                re.compile(pattern)
            except re.error:
                errors.append(f"triage[{i}]:pattern_invalid")
        if "label" in item and not isinstance(item["label"], str):
            errors.append(f"triage[{i}]:label_invalid")
    return errors


def compile_rules(raw: Any) -> list[TriRule]:
    """Construit les règles depuis la section brute du manifeste. Ignore en silence
    une entrée mal formée (le manifeste est validé en amont ; c'est une défense en
    profondeur côté lecture)."""
    out: list[TriRule] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        target = item.get("target")
        pattern = item.get("pattern")
        kind = item.get("kind")
        if target not in TRI_TARGETS or kind not in TRI_KINDS:
            continue
        if not isinstance(pattern, str) or not pattern.strip():
            continue
        label = item.get("label") if isinstance(item.get("label"), str) else ""
        out.append(TriRule(target=target, pattern=pattern, kind=kind, label=label or ""))
    return out


def rule_label(rule: TriRule) -> str:
    """Libellé affichable d'une règle (celui saisi, ou un résumé déterministe)."""
    return rule.label or f"{rule.target} {rule.kind}: {rule.pattern}"


def _matches(rule: TriRule, value: str) -> bool:
    if not value:
        return False
    if rule.kind == "regex":
        try:
            return re.search(rule.pattern, value) is not None
        except re.error:
            return False
    return fnmatch.fnmatchcase(value, rule.pattern)


def _strip_json(name: str) -> str:
    return name[:-5] if name.endswith(".json") else name


def _source_files(schema: dict[str, Any]) -> list[str]:
    """Chaînes feuilles sous `x-source` (chemins de fichiers d'origine)."""
    out: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, dict):
            for inner in value.values():
                walk(inner)
        elif isinstance(value, list):
            for inner in value:
                walk(inner)

    walk(schema.get("x-source"))
    return out


def _sql_schema_of(schema: dict[str, Any]) -> str:
    """Nom de schéma SQL d'un objet, déduit du `$id` (`<schema>.<table>.json`)."""
    sid = _strip_json(str(schema.get("$id") or ""))
    return sid.rsplit(".", 1)[0] if "." in sid else ""


def _object_values(schema: dict[str, Any], target: str) -> list[str]:
    if target == "object-name":
        return [str(schema.get("title") or _strip_json(str(schema.get("$id") or "")) or "")]
    if target == "object-type":
        return [str(schema.get("x-kind") or "")]
    if target == "file-path":
        return _source_files(schema)
    if target == "sql-schema":
        return [_sql_schema_of(schema)]
    return []


def object_excluded(schema: dict[str, Any], rules: list[TriRule]) -> TriRule | None:
    """Première règle d'objet qui écarte ce schéma, ou None. `field-name` ne porte
    pas sur l'objet entier."""
    for rule in rules:
        if rule.target == "field-name":
            continue
        for value in _object_values(schema, rule.target):
            if _matches(rule, value):
                return rule
    return None


def field_excluded(name: str, rules: list[TriRule]) -> TriRule | None:
    """Première règle de champ qui écarte ce champ, ou None."""
    for rule in rules:
        if rule.target == "field-name" and _matches(rule, name):
            return rule
    return None
