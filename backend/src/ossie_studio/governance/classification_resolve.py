"""Valeur effective des classifications d'un asset (ticket docflow 5e8680d7).

Repris d'Entropy Data, à la demande de Gael (17/11/2026) :
- **champ** : la valeur déclarée sur le champ ; à défaut, celle posée par une
  **règle de classification** sur le champ raw (ticket 568a0547, Gael : « la
  personne gagne ») — calculée, jamais écrite dans le `.extend.yaml` ; plus tard,
  à défaut, celle du concept métier lié (l'étape viendra se brancher ici) ;
- **asset** : une valeur déclarée sur l'asset l'emporte ; sinon elle est
  **dérivée** de ses champs : le niveau de rang le plus élevé (le plus sensible),
  ou l'**union** pour un schéma multi-valeurs ;
- **source** : dérivée de ses assets, de la même façon ;
- **valeur invalide** (schéma ou niveau inconnu) : **aucun badge**, jamais de
  repli silencieux sur la valeur dérivée ; elle est listée pour l'onglet
  Gouvernance, onglet « Valeurs invalides ».

Le travail humain vit dans `<clé>.extend.yaml` (clé `classification`, ticket
7a284e55) ; en mémoire, l'asset le porte sous `x-classification` (voir
`sources/asset_files.py`). Fonctions pures : rien n'est lu ni écrit ici.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ossie_studio.governance.repo import Level, Scheme

CLASSIFICATION_KEY = "x-classification"

UNKNOWN_SCHEME = "unknown_scheme"
UNKNOWN_LEVEL = "unknown_level"
NOT_SINGLE = "not_single"  # plusieurs valeurs pour un schéma à valeur unique


@dataclass(frozen=True)
class InvalidValue:
    field: str  # "" pour l'objet
    scheme: str
    value: str
    reason: str  # unknown_scheme | unknown_level | not_single


@dataclass(frozen=True)
class Gap:
    """Valeur déclarée sur l'asset moins sensible qu'un de ses champs."""

    field: str
    level: str


@dataclass(frozen=True)
class SchemeValue:
    """Valeur effective d'un schéma sur l'asset."""

    scheme: str
    values: tuple[str, ...]  # noms de niveaux, du plus sensible au moins sensible
    declared: bool  # True : déclarée sur l'asset ; False : dérivée des champs
    declared_value: tuple[str, ...]  # ce qui est déclaré (pour le sélecteur), même invalide
    extra: int  # « +N » : autres niveaux distincts présents sous la valeur affichée
    derived_from: tuple[str, ...]  # niveaux distincts des champs, du plus sensible
    gap: Gap | None
    invalid: bool  # déclaration invalide : aucun badge


@dataclass(frozen=True)
class RuleValue:
    """Valeur posée sur un champ par une règle de classification (calculée par
    l'index de l'explorateur raw, déjà validée contre les schémas)."""

    values: tuple[str, ...]
    rule: str  # identifiant(s) de règle, séparés par des virgules (union multi-valeurs)
    rule_name: str


@dataclass
class AssetClassification:
    object: list[SchemeValue]  # un par schéma, dans l'ordre des schémas
    # champ → schéma → valeurs effectives (déclarées, sinon par règle)
    fields: dict[str, dict[str, tuple[str, ...]]]
    levels: set[tuple[str, str]]  # (schéma, niveau) effectifs, champs compris (filtre)
    invalid: list[InvalidValue] = field(default_factory=list)
    # Valeurs par règle en vigueur (aucune déclaration ne les remplace) : badge
    # marqué « par règle » à l'écran (ticket 568a0547).
    by_rule: dict[str, dict[str, RuleValue]] = field(default_factory=dict)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, str) and v.strip()]
    return []


def _ordered(scheme: Scheme, names: Iterable[str]) -> tuple[str, ...]:
    """Noms de niveaux distincts, du rang le plus élevé au plus bas (puis ordre
    du schéma)."""
    by_name = {lv.name: lv for lv in scheme.levels}
    unique = {n for n in names if n in by_name}
    return tuple(sorted(unique, key=lambda n: (-by_name[n].rank, by_name[n].position)))


def _validate(
    declared: Any, schemes: dict[str, Scheme], where: str
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]], list[InvalidValue]]:
    """(valeurs valides par schéma, valeurs déclarées brutes par schéma, invalides)."""
    valid: dict[str, tuple[str, ...]] = {}
    raw: dict[str, tuple[str, ...]] = {}
    invalid: list[InvalidValue] = []
    if not isinstance(declared, dict):
        return valid, raw, invalid
    for scheme_id, value in declared.items():
        names = _as_list(value)
        if not names:
            continue
        raw[str(scheme_id)] = tuple(names)
        scheme = schemes.get(str(scheme_id))
        if scheme is None:
            invalid.extend(InvalidValue(where, str(scheme_id), n, UNKNOWN_SCHEME) for n in names)
            continue
        if not scheme.multi_value and len(names) > 1:
            invalid.extend(InvalidValue(where, scheme.id, n, NOT_SINGLE) for n in names)
            continue
        known = {lv.name for lv in scheme.levels}
        bad = [n for n in names if n not in known]
        invalid.extend(InvalidValue(where, scheme.id, n, UNKNOWN_LEVEL) for n in bad)
        good = _ordered(scheme, names)
        if good:
            valid[scheme.id] = good
    return valid, raw, invalid


def _rank(scheme: Scheme, name: str) -> int:
    return next((lv.rank for lv in scheme.levels if lv.name == name), 0)


def classify(
    asset: dict[str, Any],
    schemes: Sequence[Scheme],
    rules: Mapping[str, Mapping[str, RuleValue]] | None = None,
) -> AssetClassification:
    """Classification effective d'un asset (en mémoire, voir le module).
    `rules` : valeurs posées par règle sur les champs raw (champ → schéma)."""
    by_id = {s.id: s for s in schemes}
    invalid: list[InvalidValue] = []

    fields: dict[str, dict[str, tuple[str, ...]]] = {}
    by_rule: dict[str, dict[str, RuleValue]] = {}
    props = asset.get("properties")
    if isinstance(props, dict):
        for name, prop in props.items():
            if not isinstance(prop, dict):
                continue
            valid, raw, bad = _validate(prop.get(CLASSIFICATION_KEY), by_id, str(name))
            invalid.extend(bad)
            # Ordre d'un champ : déclarée à la main > par règle > rien. Une
            # déclaration, même invalide, écarte la règle (jamais de repli).
            for scheme_id, rv in (rules or {}).get(str(name), {}).items():
                scheme = by_id.get(scheme_id)
                if scheme is None or scheme_id in raw:
                    continue
                values = _ordered(scheme, rv.values)
                if values:
                    valid[scheme_id] = values if scheme.multi_value else values[:1]
                    by_rule.setdefault(str(name), {})[scheme_id] = rv
            if valid:
                fields[str(name)] = valid

    obj_valid, obj_raw, obj_bad = _validate(asset.get(CLASSIFICATION_KEY), by_id, "")
    invalid.extend(obj_bad)
    bad_schemes = {b.scheme for b in obj_bad}

    levels: set[tuple[str, str]] = set()
    results: list[SchemeValue] = []
    for scheme in schemes:
        under = [(f, v) for f, per in fields.items() for v in per.get(scheme.id, ())]
        derived_from = _ordered(scheme, (v for _, v in under))
        levels.update((scheme.id, v) for _, v in under)
        declared_value = obj_raw.get(scheme.id, ())
        if declared_value:
            # Une déclaration (même en partie invalide pour un schéma à valeur
            # unique) l'emporte : jamais de repli sur la valeur dérivée.
            values = obj_valid.get(scheme.id, ())
            is_invalid = scheme.id in bad_schemes and not values
            gap = None
            if values and not scheme.multi_value and derived_from:
                top = derived_from[0]
                if _rank(scheme, top) > _rank(scheme, values[0]):
                    gap_field = next(f for f, v in under if v == top)
                    gap = Gap(field=gap_field, level=top)
            results.append(
                SchemeValue(
                    scheme=scheme.id,
                    values=values,
                    declared=True,
                    declared_value=declared_value,
                    extra=0,
                    derived_from=derived_from,
                    gap=gap,
                    invalid=is_invalid,
                )
            )
        else:
            values = derived_from if scheme.multi_value else derived_from[:1]
            extra = 0 if scheme.multi_value else max(len(derived_from) - 1, 0)
            results.append(
                SchemeValue(
                    scheme=scheme.id,
                    values=values,
                    declared=False,
                    declared_value=(),
                    extra=extra,
                    derived_from=derived_from,
                    gap=None,
                    invalid=False,
                )
            )
        levels.update((scheme.id, v) for v in results[-1].values)
    return AssetClassification(
        object=results, fields=fields, levels=levels, invalid=invalid, by_rule=by_rule
    )


def source_levels(
    classifications: Iterable[AssetClassification], schemes: Sequence[Scheme]
) -> dict[str, tuple[str, ...]]:
    """Niveau d'une source par schéma : le plus sensible de ses assets (l'union
    pour un schéma multi-valeurs). Un schéma sans valeur est absent."""
    gathered: dict[str, list[str]] = {}
    for c in classifications:
        for sv in c.object:
            gathered.setdefault(sv.scheme, []).extend(sv.values)
    out: dict[str, tuple[str, ...]] = {}
    for scheme in schemes:
        ordered = _ordered(scheme, gathered.get(scheme.id, ()))
        if ordered:
            out[scheme.id] = ordered if scheme.multi_value else ordered[:1]
    return out


def check_declaration(scheme: Scheme | None, names: list[str]) -> str | None:
    """Motif de refus d'une déclaration saisie (écran ou agent), None si elle
    est acceptable. Une valeur vide (retrait) est toujours acceptable."""
    if not names:
        return None
    if scheme is None:
        return UNKNOWN_SCHEME
    if not scheme.multi_value and len(names) > 1:
        return NOT_SINGLE
    known = {lv.name for lv in scheme.levels}
    return UNKNOWN_LEVEL if any(n not in known for n in names) else None


def level_payload(level: Level) -> dict[str, object]:
    return {
        "name": level.name,
        "label": level.label or level.name,
        "short_name": level.short_name,
        "rank": level.rank,
        "color": level.color,
    }


def scheme_payload(scheme: Scheme) -> dict[str, object]:
    return {
        "id": scheme.id,
        "name": scheme.name,
        "badge_prefix": scheme.badge_prefix,
        "multi_value": scheme.multi_value,
        "levels": [level_payload(lv) for lv in scheme.levels],
    }
