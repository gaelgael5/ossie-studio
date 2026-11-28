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
from ossie_studio.roundtrip import write_yaml

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


# Préfixe réservé aux règles fournies par Ossie (governance/builtin_classification_rules.py).
BUILTIN_PREFIX = "ossie-"


def parse_rule(
    rule_id: str, data: Any, *, builtin: bool = False
) -> tuple[ClassificationRule | None, list[str]]:
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
    elif rule_id.startswith(BUILTIN_PREFIX) and not builtin:
        # Un fichier du dépôt ne peut pas masquer une règle fournie.
        errors.append("classification_rule_id_reserved")
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
    write_yaml(target, rule_document(rule))


def delete_classification_rule(root: Path, rule_id: str) -> bool:
    target = root / rule_path(rule_id)
    if not target.is_file():
        return False
    target.unlink()
    return True


# --- État des règles fournies par Ossie ------------------------------------------
#
# Seul leur état vit dans le Git de l'espace : la liste de celles qu'il a
# désactivées. Une règle ajoutée par une nouvelle version d'Ossie est active
# d'office ; une règle retirée du produit disparaît.

BUILTIN_STATE_FILE = f"{GOVERNANCE_DIR}/classification-rules-builtin.yaml"


def read_builtin_disabled(root: Path) -> set[str]:
    file = root / BUILTIN_STATE_FILE
    if not file.is_file():
        return set()
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return set()
    items = data.get("disabled") if isinstance(data, dict) else None
    return {str(i) for i in items} if isinstance(items, list) else set()


def write_builtin_disabled(root: Path, disabled: set[str]) -> None:
    file = root / BUILTIN_STATE_FILE
    if not disabled:
        if file.is_file():
            file.unlink()
        return
    file.parent.mkdir(parents=True, exist_ok=True)
    write_yaml(file, {"disabled": sorted(disabled)})


def active_classification_rules(root: Path) -> list[ClassificationRule]:
    """Règles qui classent : celles fournies par Ossie (sauf celles que l'espace
    a désactivées), puis celles du dépôt (valides ; l'état « active » est
    regardé par le moteur)."""
    from ossie_studio.governance.builtin_classification_rules import (
        builtin_classification_rules,
    )

    disabled = read_builtin_disabled(root)
    provided = [r for r in builtin_classification_rules() if r.id not in disabled]
    own = [f.rule for f in read_classification_rules(root) if f.rule is not None]
    return provided + own


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


# --- Retraits ------------------------------------------------------------------------
#
# Ticket 77ce0d79 (Gael, 24/11/2026) : retirer la valeur posée par une règle sur
# un champ est **mémorisé**, comme les retraits de tags : la règle ne la repose
# plus sur ce champ. Rattaché à l'identité du champ raw (« source/fichier »,
# champ, schéma) : renommer le champ dans le code en fait un autre champ, et la
# règle repose la valeur (accepté, comme pour les tags).

CLASSIFICATION_REMOVALS_FILE = f"{GOVERNANCE_DIR}/classification-removals.yaml"


@dataclass(frozen=True)
class ClassificationRemoval:
    object: str  # « source/fichier » de l'objet raw
    field: str
    scheme: str
    # Règle(s) qui posaient la valeur au moment du retrait (ids séparés par des
    # virgules) : la page d'une règle compte ses retraits.
    rule: str = ""
    title: str = ""  # titre de l'objet au moment du retrait (lisibilité du fichier)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.object, self.field, self.scheme)


def read_classification_removals(root: Path) -> list[ClassificationRemoval]:
    """Retraits du dépôt ; une entrée mal formée est ignorée plutôt que de
    bloquer l'indexation."""
    file = root / CLASSIFICATION_REMOVALS_FILE
    try:
        text = file.read_text(encoding="utf-8") if file.is_file() else None
    except (OSError, UnicodeDecodeError):
        return []
    return parse_classification_removals(text)


def parse_classification_removals(text: str | None) -> list[ClassificationRemoval]:
    """Retraits lus d'un texte (fichier lu sans matérialiser la vue)."""
    if not text:
        return []
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return []
    items = data.get("removals") if isinstance(data, dict) else None
    out: list[ClassificationRemoval] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        obj = str(item.get("object") or "")
        name, scheme = str(item.get("field") or ""), str(item.get("scheme") or "")
        if "/" in obj and name and scheme:
            out.append(
                ClassificationRemoval(
                    obj, name, scheme, str(item.get("rule") or ""), str(item.get("title") or "")
                )
            )
    return out


def write_classification_removals(root: Path, removals: list[ClassificationRemoval]) -> None:
    target = root / CLASSIFICATION_REMOVALS_FILE
    unique = {r.key: r for r in removals}
    if not unique:
        if target.is_file():
            target.unlink()
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "removals": [
            {
                "object": r.object,
                "field": r.field,
                "scheme": r.scheme,
                **({"rule": r.rule} if r.rule else {}),
                **({"title": r.title} if r.title else {}),
            }
            for r in sorted(unique.values(), key=lambda r: r.key)
        ]
    }
    write_yaml(target, doc)


def removal_counts(removals: list[ClassificationRemoval]) -> dict[str, int]:
    """Nombre de retraits par règle (page des règles)."""
    counts: dict[str, int] = {}
    for r in removals:
        for rule_id in filter(None, r.rule.split(",")):
            counts[rule_id] = counts.get(rule_id, 0) + 1
    return counts
