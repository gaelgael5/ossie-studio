"""Profils de relations virtuelles des sources SQL (ticket b5f333b4).

Les entrepôts Snowflake de Gael (datamart, Data Vault) ne déclarent pas de clés
étrangères — elles ne sont jamais vérifiées par Snowflake, donc rarement posées —
mais leurs relations existent, portées par une convention de nommage :
`ACCOUNT_HK` pointe `ACCOUNT_HUB.ACCOUNT_HK`. Un profil décrit ces conventions ;
une source SQL en choisit zéro, un ou plusieurs (`relation_profiles` de son
manifeste) et un même profil sert à toutes les sources qui le citent.

Décisions (questions ouvertes du ticket, tranchées par la proposition le 25/11) :

- les relations sont **calculées à la construction de l'index** de
  l'explorateur, comme les tags par règle, jamais écrites dans le raw : changer
  un profil ne demande pas de recollecter, et le raw reste ce que la base a dit ;
- la table cible est cherchée **dans le même schéma, puis dans toute la
  source** ; plusieurs candidates → aucune relation (signalée dans l'aperçu) ;
- satellites et liens Data Vault sont couverts par la même règle : toute colonne
  `X_HK` hors du hub pointe `X_HUB.X_HK` ; la colonne cible elle-même ne se
  référence pas.

Format (`governance/relation-profiles/<id>.yaml`) :

    id: etoile
    name: Modèle en étoile
    description: …
    rules:
      - column: "(?P<nom>[A-Z0-9_]+)_ID"   # expression régulière, nom entier
        table: "DIM_{nom}"                 # gabarit de la table visée
        target_column: "ID"                # gabarit de la colonne visée
        kinds: [table, view]               # facultatif : natures qui portent la colonne

`(?<nom>…)` est accepté et lu comme `(?P<nom>…)`. Les règles d'un profil sont
essayées dans l'ordre ; la première qui trouve sa cible l'emporte.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ossie_studio.governance.repo import GOVERNANCE_DIR
from ossie_studio.governance.tag_rules import BUILTIN_PREFIX, _compiled, is_valid_rule_id

RELATION_PROFILES_DIR = f"{GOVERNANCE_DIR}/relation-profiles"
# À incrémenter quand le profil fourni change : les index se reconstruisent.
BUILTIN_PROFILES_VERSION = "1"
_KNOWN_KEYS = {"id", "name", "description", "rules"}
_RULE_KEYS = {"column", "table", "target_column", "kinds"}
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class RelationRule:
    column: str  # expression régulière (sans barres), nom de colonne entier
    table: str  # gabarit, ex. "{nom}_HUB"
    target_column: str  # gabarit, ex. "{nom}_HK"
    kinds: tuple[str, ...] = ()

    def regex(self) -> re.Pattern[str] | None:
        return _compiled(f"/{_python_groups(self.column)}/")


@dataclass(frozen=True)
class RelationProfile:
    id: str
    name: str
    rules: tuple[RelationRule, ...]
    description: str = ""
    builtin: bool = False


def _python_groups(pattern: str) -> str:
    """`(?<nom>…)` (syntaxe JavaScript / .NET) → `(?P<nom>…)` (Python)."""
    return re.sub(r"\(\?<([A-Za-z_][A-Za-z0-9_]*)>", r"(?P<\1>", pattern)


def _strip_slashes(pattern: str) -> str:
    return pattern[1:-1] if len(pattern) >= 2 and pattern[0] == pattern[-1] == "/" else pattern


def parse_profile(
    profile_id: str, data: Any, *, builtin: bool = False
) -> tuple[RelationProfile | None, list[str]]:
    if not isinstance(data, dict):
        return None, ["relation_profile_not_mapping"]
    errors: list[str] = []
    if set(data) - _KNOWN_KEYS:
        errors.append("relation_profile_unknown_key")
    declared = str(data.get("id") or profile_id)
    if declared != profile_id or not is_valid_rule_id(profile_id):
        errors.append("relation_profile_id_invalid")
    elif profile_id.startswith(BUILTIN_PREFIX) and not builtin:
        errors.append("relation_profile_id_reserved")
    raw_rules = data.get("rules")
    rules: list[RelationRule] = []
    if not isinstance(raw_rules, list) or not raw_rules:
        errors.append("relation_profile_rules_missing")
    else:
        for item in raw_rules:
            rule, rule_errors = _parse_rule(item)
            errors += rule_errors
            if rule is not None:
                rules.append(rule)
    if errors:
        return None, list(dict.fromkeys(errors))
    return (
        RelationProfile(
            id=profile_id,
            name=str(data.get("name") or profile_id),
            description=str(data.get("description") or ""),
            rules=tuple(rules),
            builtin=builtin,
        ),
        [],
    )


def _parse_rule(item: Any) -> tuple[RelationRule | None, list[str]]:
    if not isinstance(item, dict) or set(item) - _RULE_KEYS:
        return None, ["relation_rule_invalid"]
    column = _strip_slashes(str(item.get("column") or "").strip())
    table = str(item.get("table") or "").strip()
    target = str(item.get("target_column") or "").strip()
    kinds = item.get("kinds") or []
    if not column or not table or not target or not isinstance(kinds, list):
        return None, ["relation_rule_invalid"]
    rule = RelationRule(column, table, target, tuple(str(k) for k in kinds))
    rx = rule.regex()
    if rx is None:
        return None, ["relation_rule_pattern_invalid"]
    # Un gabarit ne peut citer qu'un groupe nommé de l'expression : sinon la
    # cible ne se calculerait jamais, en silence.
    groups = set(rx.groupindex)
    if not set(_PLACEHOLDER.findall(table + target)) <= groups:
        return None, ["relation_rule_template_invalid"]
    return rule, []


def profile_document(profile: RelationProfile) -> dict[str, Any]:
    doc: dict[str, Any] = {"id": profile.id, "name": profile.name}
    if profile.description:
        doc["description"] = profile.description
    doc["rules"] = [
        {
            "column": r.column,
            "table": r.table,
            "target_column": r.target_column,
            **({"kinds": list(r.kinds)} if r.kinds else {}),
        }
        for r in profile.rules
    ]
    return doc


def profile_path(profile_id: str) -> str:
    if not is_valid_rule_id(profile_id):
        raise ValueError(profile_id)
    return f"{RELATION_PROFILES_DIR}/{profile_id}.yaml"


@dataclass(frozen=True)
class ProfileFile:
    rel: str
    profile: RelationProfile | None
    errors: tuple[str, ...]


def read_profile_files(root: Path) -> list[ProfileFile]:
    base = root / RELATION_PROFILES_DIR
    if not base.is_dir():
        return []
    out: list[ProfileFile] = []
    for file in sorted(base.glob("*.yaml")):
        rel = file.relative_to(root).as_posix()
        try:
            data = yaml.safe_load(file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            out.append(ProfileFile(rel, None, ("relation_profile_unreadable",)))
            continue
        profile, errors = parse_profile(file.stem, data)
        out.append(ProfileFile(rel, profile, tuple(errors)))
    return out


def builtin_profiles() -> list[RelationProfile]:
    """Profil fourni par Ossie. Pas d'état « désactivé » à gérer : une source ne
    l'applique que si elle le choisit."""
    rules = (
        # Gael, 25/11 : « ACCOUNT_HK pointe la table ACCOUNT_HUB sur la clé
        # ACCOUNT_HK ». L'autre convention courante (HUB_ACCOUNT) en second.
        RelationRule("(?P<nom>[A-Za-z0-9_]+?)_HK", "{nom}_HUB", "{nom}_HK"),
        RelationRule("(?P<nom>[A-Za-z0-9_]+?)_HK", "HUB_{nom}", "{nom}_HK"),
    )
    return [
        RelationProfile(
            id=f"{BUILTIN_PREFIX}datavault",
            name="Data Vault 2.0",
            description=(
                "Une colonne <X>_HK (hors du hub lui-même) pointe la clé de hachage "
                "<X>_HK du hub <X>_HUB (ou HUB_<X>) : satellites et liens rattachés à leurs hubs."
            ),
            rules=rules,
            builtin=True,
        )
    ]


def all_profiles(root: Path) -> dict[str, RelationProfile]:
    profiles = {p.id: p for p in builtin_profiles()}
    for f in read_profile_files(root):
        if f.profile is not None:
            profiles[f.profile.id] = f.profile
    return profiles


def write_profile(root: Path, profile: RelationProfile) -> None:
    target = root / profile_path(profile.id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(profile_document(profile), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def delete_profile(root: Path, profile_id: str) -> bool:
    target = root / profile_path(profile_id)
    if not target.is_file():
        return False
    target.unlink()
    return True


def fill(template: str, groups: dict[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda m: groups.get(m.group(1), ""), template)


def source_relation_profiles(root: Path, slug: str) -> list[str]:
    """Profils choisis par une source (`parameters.relation_profiles` de son
    manifeste) ; [] si le manifeste est absent ou illisible."""
    from ossie_studio.sources.manifest import ManifestError, parse_manifest
    from ossie_studio.sources.paths import MANIFEST_FILENAME, SOURCES_DIR

    try:
        manifest = parse_manifest(
            (root / SOURCES_DIR / slug / MANIFEST_FILENAME).read_text("utf-8")
        )
    except (OSError, UnicodeDecodeError, ManifestError):
        return []
    value = manifest.parameters.get("relation_profiles")
    return [str(v) for v in value] if isinstance(value, list) else []
