"""Règles de classification : classer les propriétés (champs) des objets raw
d'après leur nom, leur type, les tags de leur classe, la classe ou le fichier.

Décisions du cadrage (epic 80805bef, Gael, 24/11/2026) :
- la règle s'applique aux **champs des objets raw**, dès la collecte (visible
  dans l'explorateur) ; l'asset retenu en hérite (ticket suivant) ;
- conditions : nom du champ, type du champ, tag posé, classe, fichiers ;
- la personne gagne : la valeur posée par règle est calculée (jamais écrite dans
  les `.extend.yaml`) et cède à une valeur déclarée à la main.

Même forme que les règles de tags (`governance/tag_rules.py`), pour qu'on les
lise de la même façon : un fichier par règle dans le Git de l'espace,
`governance/classification-rules/<id>.yaml` (brouillon, publiable).

Écarts volontaires avec les règles de tags :
- `match: all` par défaut — affiner un nom par un type (« birth* ET date ») est
  le cas courant, alors que pour les tags « hérite de X OU porte [Y] » l'était ;
- les motifs de nom de champ, de type et de classe se comparent **sans la
  casse** (`Email`, `email`, `EMAIL_ADDRESS`) ;
- `files` est facultatif : sans motif, tous les fichiers.

Format :
    id: rgpd-emails
    name: Emails
    enabled: true
    files: ["*.cs"]          # facultatif
    match: all
    when:
      field: ["*email*", "*mail"]
      type: [string]
      tag: [technique/dto]
      class: ["*Dto"]
    scheme: rgpd
    values: [Donnée personnelle]
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ossie_studio.governance.repo import GOVERNANCE_DIR, Scheme

CLASSIFICATION_RULES_DIR = f"{GOVERNANCE_DIR}/classification-rules"

COND_FIELD = "field"
COND_TYPE = "type"
COND_TAG = "tag"
COND_CLASS = "class"
CONDITION_KINDS: tuple[str, ...] = (COND_FIELD, COND_TYPE, COND_TAG, COND_CLASS)
# Conditions qui regardent le champ lui-même ; les autres regardent sa classe.
FIELD_CONDITIONS = frozenset({COND_FIELD, COND_TYPE})

MATCH_ANY = "any"
MATCH_ALL = "all"

_RULE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
_KNOWN_KEYS = {"id", "name", "description", "enabled", "files", "match", "when", "scheme", "values"}


@dataclass(frozen=True)
class ClassificationRule:
    id: str
    name: str
    conditions: dict[str, tuple[str, ...]] = field(hash=False)
    scheme: str
    values: tuple[str, ...]
    files: tuple[str, ...] = ()
    match: str = MATCH_ALL
    enabled: bool = True
    description: str = ""


def is_valid_rule_id(rule_id: str) -> bool:
    return bool(_RULE_ID.fullmatch(rule_id))


def rule_path(rule_id: str) -> str:
    # Garde de sécurité : seul un id valide ([a-z0-9_-], jamais « / » ni « .. »)
    # devient un nom de fichier sous governance/classification-rules.
    if not is_valid_rule_id(rule_id):
        raise ValueError(rule_id)
    return f"{CLASSIFICATION_RULES_DIR}/{rule_id}.yaml"


def _patterns(value: Any) -> tuple[list[str], bool]:
    """Liste de motifs non vides ; un motif seul est accepté. (motifs, valide)."""
    if value is None:
        return [], True
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return [], False
    out = [str(v).strip() for v in value if isinstance(v, (str, int)) and str(v).strip()]
    return out, len(out) == len(value)


def parse_rule(rule_id: str, data: Any) -> tuple[ClassificationRule | None, list[str]]:
    """Règle lue d'un fichier ou d'un formulaire, ou None et les erreurs (codes
    stables, traduits par l'interface). La cohérence avec les schémas de
    classification se vérifie à part (`effect_errors`) : un schéma supprimé
    après coup rend la règle « invalide » sans la rendre illisible."""
    if not isinstance(data, dict):
        return None, ["classification_rule_not_mapping"]
    errors: list[str] = []
    if set(data) - _KNOWN_KEYS:
        errors.append("classification_rule_unknown_key")
    declared = str(data.get("id") or rule_id)
    if declared != rule_id or not is_valid_rule_id(rule_id):
        errors.append("classification_rule_id_invalid")
    files, ok = _patterns(data.get("files"))
    if not ok:
        errors.append("classification_rule_files_invalid")
    match = str(data.get("match") or MATCH_ALL)
    if match not in (MATCH_ANY, MATCH_ALL):
        errors.append("classification_rule_match_invalid")
    when = data.get("when")
    conditions: dict[str, tuple[str, ...]] = {}
    if isinstance(when, dict):
        for kind, value in when.items():
            if kind not in CONDITION_KINDS:
                errors.append("classification_rule_condition_unknown")
                continue
            values, ok = _patterns(value)
            if not ok:
                errors.append("classification_rule_condition_invalid")
            elif values:
                conditions[kind] = tuple(values)
    elif when is not None:
        errors.append("classification_rule_condition_invalid")
    if not conditions:
        # Une règle sans condition classerait tous les champs de l'espace.
        errors.append("classification_rule_conditions_missing")
    scheme = str(data.get("scheme") or "").strip()
    raw_values, ok = _patterns(data.get("values"))
    if not scheme or not raw_values or not ok:
        errors.append("classification_rule_effect_missing")
    if "enabled" in data and not isinstance(data["enabled"], bool):
        errors.append("classification_rule_flag_invalid")
    if errors:
        return None, list(dict.fromkeys(errors))
    return (
        ClassificationRule(
            id=rule_id,
            name=str(data.get("name") or rule_id),
            description=str(data.get("description") or ""),
            files=tuple(files),
            conditions=conditions,
            scheme=scheme,
            values=tuple(dict.fromkeys(raw_values)),
            match=match,
            enabled=bool(data.get("enabled", True)),
        ),
        [],
    )


def effect_errors(rule: ClassificationRule, schemes: list[Scheme]) -> list[str]:
    """Codes si l'effet de la règle ne correspond plus aux schémas : schéma
    absent, valeur absente du schéma, plusieurs valeurs pour un schéma à valeur
    unique. Une règle dans ce cas ne classe rien (badge « invalide »)."""
    scheme = next((s for s in schemes if s.id == rule.scheme), None)
    if scheme is None:
        return ["classification_rule_scheme_unknown"]
    names = {lv.name for lv in scheme.levels}
    errors: list[str] = []
    if any(v not in names for v in rule.values):
        errors.append("classification_rule_value_unknown")
    if not scheme.multi_value and len(rule.values) > 1:
        errors.append("classification_rule_single_value")
    return errors


@dataclass(frozen=True)
class RuleFile:
    rel: str  # chemin dans le dépôt
    rule: ClassificationRule | None
    errors: tuple[str, ...]


def read_classification_rules(root: Path) -> list[RuleFile]:
    """Toutes les règles du dépôt, valides ou non (les invalides portent leurs
    erreurs et ne classent rien)."""
    base = root / CLASSIFICATION_RULES_DIR
    if not base.is_dir():
        return []
    out: list[RuleFile] = []
    for file in sorted(base.glob("*.yaml")):
        rel = file.relative_to(root).as_posix()
        try:
            data = yaml.safe_load(file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            out.append(RuleFile(rel, None, ("classification_rule_unreadable",)))
            continue
        rule, errors = parse_rule(file.stem, data)
        out.append(RuleFile(rel, rule, tuple(errors)))
    return out


def rule_document(rule: ClassificationRule) -> dict[str, Any]:
    doc: dict[str, Any] = {"id": rule.id, "name": rule.name}
    if rule.description:
        doc["description"] = rule.description
    doc["enabled"] = rule.enabled
    if rule.files:
        doc["files"] = list(rule.files)
    doc["match"] = rule.match
    doc["when"] = {k: list(v) for k, v in rule.conditions.items()}
    doc["scheme"] = rule.scheme
    doc["values"] = list(rule.values)
    return doc


def write_classification_rule(root: Path, rule: ClassificationRule) -> None:
    target = root / rule_path(rule.id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(
            rule_document(rule), sort_keys=False, allow_unicode=True, default_flow_style=False
        ),
        encoding="utf-8",
    )


def delete_classification_rule(root: Path, rule_id: str) -> bool:
    target = root / rule_path(rule_id)
    if not target.is_file():
        return False
    target.unlink()
    return True


# --- Correspondance ---------------------------------------------------------------


def folded_match(value: str, patterns: tuple[str, ...]) -> str | None:
    """Premier motif glob qui reconnaît la valeur, sans la casse ; None sinon."""
    if not value:
        return None
    folded = value.casefold()
    for pattern in patterns:
        if fnmatch.fnmatchcase(folded, pattern.casefold()):
            return pattern
    return None


def tag_matches(tags: tuple[str, ...], patterns: tuple[str, ...]) -> str | None:
    """Un tag posé reconnu par un motif : glob, ou préfixe hiérarchique
    (`technique` reconnaît `technique/service`, comme le filtre des tags)."""
    for tag in tags:
        for pattern in patterns:
            p = pattern.casefold().rstrip("/")
            if tag == p or tag.startswith(f"{p}/") or fnmatch.fnmatchcase(tag, p):
                return tag
    return None
