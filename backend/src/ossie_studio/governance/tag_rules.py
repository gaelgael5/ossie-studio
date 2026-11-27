"""Règles de tags : poser des tags gouvernés sur les objets raw d'après ce que
le code dit d'eux (classe de base, attribut, nom, chemin, champ).

Décisions du cadrage (epic « Tags de gouvernance posés par règles », 22/11/2026) :

- une règle vaut pour **tout l'espace** et cible des fichiers source par motif,
  comme un parseur (`*.cs`) ;
- elle vit dans le **Git de l'espace** (`governance/tag-rules/<id>.yaml`), donc
  dans le brouillon, publiable comme le reste ; son état activé/désactivé est
  dans le même fichier ;
- `inherit_tags` : le tag se transmet aux classes qui dérivent de l'objet tagué
  (ou implémentent l'interface taguée), à toute profondeur — c'est ce paramètre,
  et non la condition `inherits`, qui suit la chaîne d'héritage (la condition ne
  regarde que les bases DIRECTES : sinon une classe petite-fille satisferait la
  règle elle-même et le paramètre ne voudrait plus rien dire) ;
- conditions d'un même type : l'une ou l'autre ; entre types : `match: any`
  (défaut — « hérite de ControllerBase OU porte [ApiController] » est le cas
  courant) ou `match: all`.

Format :

    id: aspnet-controllers
    name: Contrôleurs ASP.NET
    description: …
    enabled: true
    files: ["*.cs"]
    match: any
    when:
      inherits: [ControllerBase, Controller]
      annotation: [ApiController]
    tags: [technique/service]
    inherit_tags: true

Les motifs sont des globs (`*`, `?`) sensibles à la casse, comme les noms dans
le code ; un nom qualifié (`Microsoft.AspNetCore.Mvc.ControllerBase`) se compare
aussi par son nom court.
"""

from __future__ import annotations

import fnmatch
import functools
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ossie_studio.governance.repo import GOVERNANCE_DIR, Tag, read_tags, write_tag
from ossie_studio.governance.tags import is_valid_tag_id, normalize_tag_id

TAG_RULES_DIR = f"{GOVERNANCE_DIR}/tag-rules"

COND_INHERITS = "inherits"
COND_ANNOTATION = "annotation"
COND_NAME = "name"
COND_PATH = "path"
COND_FIELD = "field"
COND_KIND = "kind"  # nature de l'objet (`x-kind`) : class, enum, model (pydantic)…
CONDITION_KINDS: tuple[str, ...] = (
    COND_INHERITS,
    COND_ANNOTATION,
    COND_NAME,
    COND_PATH,
    COND_FIELD,
    COND_KIND,
)
MATCH_ANY = "any"
MATCH_ALL = "all"

_RULE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
_KNOWN_KEYS = {
    "id",
    "name",
    "description",
    "enabled",
    "files",
    "match",
    "when",
    "tags",
    "inherit_tags",
    "sources",
}

# Types de source qu'une règle peut viser (ticket 39e009e0) : ceux que le
# serveur collecte (sources/manifest.py). Un objet de catalogue SQL n'a pas
# d'extension de fichier : `files` ne permettait pas de le viser sans attraper
# aussi le code.
SOURCE_TYPES: tuple[str, ...] = ("git", "openapi", "postgres", "sqlserver", "snowflake")


@dataclass(frozen=True)
class TagRule:
    id: str
    name: str
    files: tuple[str, ...]
    conditions: dict[str, tuple[str, ...]] = field(hash=False)
    tags: tuple[str, ...]
    match: str = MATCH_ANY
    inherit_tags: bool = False
    enabled: bool = True
    description: str = ""
    sources: tuple[str, ...] = ()


def is_valid_rule_id(rule_id: str) -> bool:
    return bool(_RULE_ID.fullmatch(rule_id))


def rule_path(rule_id: str) -> str:
    # Garde de sécurité : seul un id valide ([a-z0-9_-], jamais « / » ni « .. »)
    # devient un nom de fichier sous governance/tag-rules.
    if not is_valid_rule_id(rule_id):
        raise ValueError(rule_id)
    return f"{TAG_RULES_DIR}/{rule_id}.yaml"


def _patterns(value: Any) -> tuple[list[str], bool]:
    """Liste de motifs non vides ; un motif seul est accepté. (motifs, valide)."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return [], False
    out = [str(v).strip() for v in value if isinstance(v, (str, int)) and str(v).strip()]
    return out, len(out) == len(value)


BUILTIN_PREFIX = "ossie-"


def parse_rule(
    rule_id: str, data: Any, *, builtin: bool = False
) -> tuple[TagRule | None, list[str]]:
    """Règle lue d'un fichier, ou None et les erreurs (codes stables, affichés
    dans le journal de validation du brouillon)."""
    if not isinstance(data, dict):
        return None, ["tag_rule_not_mapping"]
    errors: list[str] = []
    unknown = sorted(set(data) - _KNOWN_KEYS)
    if unknown:
        errors.append("tag_rule_unknown_key")
    declared = str(data.get("id") or rule_id)
    if declared != rule_id or not is_valid_rule_id(rule_id):
        errors.append("tag_rule_id_invalid")
    elif rule_id.startswith(BUILTIN_PREFIX) and not builtin:
        # Préfixe réservé aux règles fournies par Ossie : un fichier du dépôt ne
        # peut pas en masquer une.
        errors.append("tag_rule_id_reserved")
    # `files` ou `sources` (ou les deux, combinés en ET) : une règle doit dire
    # ce qu'elle vise.
    files: list[str] = []
    if data.get("files") not in (None, [], ""):
        files, ok = _patterns(data.get("files"))
        if not files or not ok:
            errors.append("tag_rule_files_invalid")
    sources: list[str] = []
    if data.get("sources") not in (None, [], ""):
        sources, ok = _patterns(data.get("sources"))
        if not sources or not ok or any(src not in SOURCE_TYPES for src in sources):
            errors.append("tag_rule_sources_invalid")
    if not files and not sources and "tag_rule_files_invalid" not in errors:
        errors.append("tag_rule_files_invalid")
    if any(not is_valid_pattern(f) for f in files):
        errors.append("tag_rule_pattern_invalid")
    match = str(data.get("match") or MATCH_ANY)
    if match not in (MATCH_ANY, MATCH_ALL):
        errors.append("tag_rule_match_invalid")
    when = data.get("when")
    conditions: dict[str, tuple[str, ...]] = {}
    if not isinstance(when, dict) or not when:
        errors.append("tag_rule_conditions_missing")
    else:
        for kind, value in when.items():
            if kind not in CONDITION_KINDS:
                errors.append("tag_rule_condition_unknown")
                continue
            values, ok = _patterns(value)
            if not values or not ok:
                errors.append("tag_rule_condition_invalid")
                continue
            if any(not is_valid_pattern(v) for v in values):
                errors.append("tag_rule_pattern_invalid")
                continue
            conditions[kind] = tuple(values)
    raw_tags, ok = _patterns(data.get("tags"))
    tags = tuple(dict.fromkeys(normalize_tag_id(t) for t in raw_tags))
    if not tags or not ok or not all(is_valid_tag_id(t) for t in tags):
        errors.append("tag_rule_tags_invalid")
    for flag in ("enabled", "inherit_tags"):
        if flag in data and not isinstance(data[flag], bool):
            errors.append("tag_rule_flag_invalid")
    if errors:
        return None, list(dict.fromkeys(errors))
    return (
        TagRule(
            id=rule_id,
            name=str(data.get("name") or rule_id),
            description=str(data.get("description") or ""),
            files=tuple(files),
            conditions=conditions,
            tags=tags,
            match=match,
            inherit_tags=bool(data.get("inherit_tags", False)),
            enabled=bool(data.get("enabled", True)),
            sources=tuple(dict.fromkeys(sources)),
        ),
        [],
    )


@dataclass(frozen=True)
class RuleFile:
    rel: str  # chemin dans le dépôt
    rule: TagRule | None
    errors: tuple[str, ...]


def read_tag_rules(root: Path) -> list[RuleFile]:
    """Toutes les règles du dépôt, valides ou non (les invalides portent leurs
    erreurs et ne posent rien)."""
    base = root / TAG_RULES_DIR
    if not base.is_dir():
        return []
    out: list[RuleFile] = []
    for file in sorted(base.glob("*.yaml")):
        rel = file.relative_to(root).as_posix()
        try:
            data = yaml.safe_load(file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            out.append(RuleFile(rel, None, ("tag_rule_unreadable",)))
            continue
        rule, errors = parse_rule(file.stem, data)
        out.append(RuleFile(rel, rule, tuple(errors)))
    return out


def active_rules(root: Path) -> list[TagRule]:
    """Règles qui posent des tags : celles fournies par Ossie (sauf celles que
    l'espace a désactivées) puis celles du dépôt, actives."""
    from ossie_studio.governance.builtin_tag_rules import builtin_rules

    disabled = read_builtin_disabled(root)
    own = [f.rule for f in read_tag_rules(root) if f.rule is not None and f.rule.enabled]
    return [r for r in builtin_rules() if r.id not in disabled] + own


# --- État des règles fournies par Ossie ------------------------------------------
#
# Les règles fournies font partie du produit ; seul leur état vit dans le Git de
# l'espace : la liste de celles qu'il a désactivées. Une règle ajoutée par une
# nouvelle version d'Ossie est donc active d'office, une règle retirée du produit
# disparaît (son id resté dans la liste est sans effet).

BUILTIN_STATE_FILE = f"{GOVERNANCE_DIR}/tag-rules-builtin.yaml"


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
    file.write_text(
        yaml.safe_dump({"disabled": sorted(disabled)}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def rule_document(rule: TagRule) -> dict[str, Any]:
    doc: dict[str, Any] = {"id": rule.id, "name": rule.name}
    if rule.description:
        doc["description"] = rule.description
    doc["enabled"] = rule.enabled
    if rule.sources:
        doc["sources"] = list(rule.sources)
    doc["files"] = list(rule.files)
    doc["match"] = rule.match
    doc["when"] = {k: list(v) for k, v in rule.conditions.items()}
    doc["tags"] = list(rule.tags)
    doc["inherit_tags"] = rule.inherit_tags
    return doc


def write_tag_rule(root: Path, rule: TagRule) -> None:
    target = root / rule_path(rule.id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(
            rule_document(rule), sort_keys=False, allow_unicode=True, default_flow_style=False
        ),
        encoding="utf-8",
    )


# Enregistrer une règle crée les tags gouvernés qu'elle cite et qui n'existent pas encore
# (décision de Gael, cadrage du 22/11/2026), à l'écran comme par l'agent.


def ensure_tags(root: Path, rule: TagRule) -> list[str]:
    """Crée les tags de la règle absents du vocabulaire ; rend ceux créés."""
    known = {t.id for t in read_tags(root)}
    created = [t for t in rule.tags if t not in known]
    for tag_id in created:
        write_tag(root, Tag(tag_id, description=f"Créé par la règle de tags « {rule.name} »."))
    return created


def ensure_builtin_tags(root: Path) -> list[str]:
    """Tags des règles fournies actives absents du vocabulaire : créés à la
    première écriture de règles de l'espace (une lecture n'écrit jamais)."""
    from ossie_studio.governance.builtin_tag_rules import builtin_rules

    disabled = read_builtin_disabled(root)
    created: list[str] = []
    for rule in builtin_rules():
        if rule.id not in disabled:
            created += ensure_tags(root, rule)
    return created


def delete_tag_rule(root: Path, rule_id: str) -> bool:
    target = root / rule_path(rule_id)
    if not target.is_file():
        return False
    target.unlink()
    return True


# --- Correspondance ---------------------------------------------------------------


def short_name(name: str) -> str:
    """`Microsoft.AspNetCore.Mvc.ControllerBase` → `ControllerBase` ;
    `Repository<Order>` → `Repository`."""
    base = name.split("<", 1)[0].strip()
    for sep in ("::", "."):
        base = base.rsplit(sep, 1)[-1]
    return base


# --- Motifs : globs, ou expressions régulières entre barres obliques ----------
#
# Ticket 39e009e0 : `FACT_[A-Z]+` ne s'exprime pas en glob. Un motif écrit
# `/…/` est une expression régulière qui doit couvrir TOUT le texte
# (fullmatch) ; sans barres, c'est un glob, comme avant — les règles existantes
# ne changent pas de sens.
#
# Garde contre les expressions catastrophiques : le motif est saisi par une
# personne et appliqué à chaque objet à chaque reconstruction d'index, et le
# module `re` n'a pas de délai. On borne la longueur, on refuse les
# quantificateurs imbriqués (`(a+)+`, `(a*)*`, `(\d{2,})+`…, cause classique du
# retour arrière exponentiel) et on compile une seule fois.
REGEX_MAX_LENGTH = 200
_TEXT_MAX_LENGTH = 512
_NESTED_QUANTIFIER = re.compile(r"\([^()]*(?:[+*]|\{\d*,?\d*\})[^()]*\)\s*(?:[+*]|\{)")


def is_regex(pattern: str) -> bool:
    return len(pattern) >= 2 and pattern.startswith("/") and pattern.endswith("/")


@functools.lru_cache(maxsize=1024)
def _compiled(pattern: str) -> re.Pattern[str] | None:
    body = pattern[1:-1]
    if not body or len(body) > REGEX_MAX_LENGTH or _NESTED_QUANTIFIER.search(body):
        return None
    try:
        return re.compile(body)
    except re.error:
        return None


def is_valid_pattern(pattern: str) -> bool:
    return not is_regex(pattern) or _compiled(pattern) is not None


def pattern_matches(text: str, pattern: str, *, fold: bool = False) -> bool:
    """Le motif (glob ou /regex/) reconnaît-il tout le texte ?"""
    if is_regex(pattern):
        rx = _compiled(pattern)
        if rx is None:
            return False
        flags_text = text[:_TEXT_MAX_LENGTH]
        if fold:
            return re.fullmatch(rx.pattern, flags_text, rx.flags | re.IGNORECASE) is not None
        return rx.fullmatch(flags_text) is not None
    if fold:
        return fnmatch.fnmatchcase(text.casefold(), pattern.casefold())
    return fnmatch.fnmatchcase(text, pattern)


def name_matches(name: str, patterns: tuple[str, ...]) -> str | None:
    """Premier motif qui reconnaît le nom (complet ou court), ou None."""
    if not name:
        return None
    candidates = {name, short_name(name)}
    for pattern in patterns:
        if any(pattern_matches(c, pattern) for c in candidates):
            return pattern
    return None


def path_matches(path: str, patterns: tuple[str, ...]) -> str | None:
    """Motif de chemin : sans « / », il porte sur le nom du fichier (`*.cs`) ;
    avec, sur le chemin depuis la racine du dépôt (`src/Api/*`)."""
    if not path:
        return None
    file_name = path.rsplit("/", 1)[-1]
    for pattern in patterns:
        # Une expression régulière porte sur le chemin entier ; un glob sans
        # « / » sur le seul nom du fichier, comme avant.
        target = path if is_regex(pattern) or "/" in pattern else file_name
        if pattern_matches(target, pattern):
            return pattern
    return None


# --- Retraits ------------------------------------------------------------------------
#
# Un tag posé par règle que l'utilisateur a retiré d'un objet. Décision du
# cadrage : le retrait est mémorisé dans le Git de l'espace (une nouvelle
# collecte ne remet pas le tag) et rattaché à l'identité de l'objet raw,
# « source/fichier » — le fichier raw dérive du nom qualifié de la classe : une
# classe renommée ou déplacée est un nouvel objet et le tag revient (accepté).

TAG_REMOVALS_FILE = f"{GOVERNANCE_DIR}/tag-removals.yaml"


@dataclass(frozen=True)
class TagRemoval:
    object: str  # « source/fichier »
    tag: str
    title: str = ""  # titre de l'objet au moment du retrait (lisibilité du fichier)


def read_tag_removals(root: Path) -> list[TagRemoval]:
    """Retraits du dépôt ; un fichier illisible ou une entrée mal formée sont
    ignorés (signalés par la validation) plutôt que de bloquer l'indexation."""
    file = root / TAG_REMOVALS_FILE
    if not file.is_file():
        return []
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return []
    items = data.get("removals") if isinstance(data, dict) else None
    out: list[TagRemoval] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        obj, tag = str(item.get("object") or ""), normalize_tag_id(str(item.get("tag") or ""))
        if "/" in obj and is_valid_tag_id(tag):
            out.append(TagRemoval(obj, tag, str(item.get("title") or "")))
    return out


def removal_errors(root: Path) -> list[str]:
    """Codes de validation du fichier des retraits (vide s'il est absent ou sain)."""
    file = root / TAG_REMOVALS_FILE
    if not file.is_file():
        return []
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return ["tag_removals_unreadable"]
    items = data.get("removals") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return ["tag_removals_invalid"]
    valid = len(read_tag_removals(root))
    return [] if valid == len(items) else ["tag_removals_invalid"]


def write_tag_removals(root: Path, removals: list[TagRemoval]) -> None:
    target = root / TAG_REMOVALS_FILE
    unique = {(r.object, r.tag): r for r in removals}
    if not unique:
        if target.is_file():
            target.unlink()
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "removals": [
            {"object": r.object, "tag": r.tag, **({"title": r.title} if r.title else {})}
            for r in sorted(unique.values(), key=lambda r: (r.object, r.tag))
        ]
    }
    target.write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )


def field_matches(name: str, patterns: tuple[str, ...]) -> str | None:
    """Motif de champ, comparé sans la casse."""
    for pattern in patterns:
        if pattern_matches(name, pattern, fold=True):
            return pattern
    return None


def rule_tag_sources(root: Path) -> dict[str, list[str]]:
    """Tags cités par les règles ACTIVES (fournies par Ossie et de l'espace) →
    noms des règles qui les posent. Sert à montrer dans l'onglet Tags les tags
    que les règles posent sans qu'ils soient encore enregistrés dans le
    vocabulaire (bug 44931826) : une lecture n'écrit jamais."""
    from ossie_studio.governance.builtin_tag_rules import builtin_rules

    disabled = read_builtin_disabled(root)
    rules = [r for r in builtin_rules() if r.id not in disabled]
    rules += [f.rule for f in read_tag_rules(root) if f.rule is not None and f.rule.enabled]
    sources: dict[str, list[str]] = {}
    for rule in rules:
        for tag in rule.tags:
            sources.setdefault(tag, []).append(rule.name)
    return sources
