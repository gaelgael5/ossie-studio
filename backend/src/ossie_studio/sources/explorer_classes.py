"""Explorateur raw : application des règles de classification aux champs des
objets de l'espace (ticket docflow cfc33d51).

Calculé à la construction de l'index, après les tags (la condition « tag »
regarde les tags posés par les règles de tags, héritage compris) ; le résultat
est mis en index avec les objets, l'affichage ne recalcule rien.

Pour un champ : les conditions sur le champ (nom, type) se testent sur lui, les
conditions sur la classe (tag, classe) sur son objet. `match: all` exige toutes
les conditions présentes, `match: any` une seule. Si deux règles classent le
même champ dans le même schéma, le niveau de plus haut rang l'emporte (rang =
sensibilité, comme la dérivation d'un asset depuis ses champs) ; pour un schéma
multi-valeurs, l'union.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from collections.abc import Set as AbstractSet
from dataclasses import replace

from ossie_studio.governance.classification_rules import (
    COND_CLASS,
    COND_FIELD,
    COND_TAG,
    COND_TYPE,
    FIELD_CONDITIONS,
    MATCH_ALL,
    ClassificationRule,
    effect_errors,
    folded_match,
    tag_matches,
)
from ossie_studio.governance.repo import Scheme
from ossie_studio.governance.tag_rules import path_matches
from ossie_studio.sources.explorer import ExplorerObject, FieldClass, SourceEntries


def _object_hits(rule: ClassificationRule, obj: ExplorerObject) -> dict[str, bool]:
    """Conditions sur la classe : reconnue ou non (seulement celles présentes)."""
    hits: dict[str, bool] = {}
    if COND_TAG in rule.conditions:
        tags = tuple(t.tag for t in obj.tags)
        hits[COND_TAG] = tag_matches(tags, rule.conditions[COND_TAG]) is not None
    if COND_CLASS in rule.conditions:
        hits[COND_CLASS] = folded_match(obj.title, rule.conditions[COND_CLASS]) is not None
    return hits


def _field_hits(rule: ClassificationRule, name: str, types: tuple[str, ...]) -> dict[str, bool]:
    hits: dict[str, bool] = {}
    if COND_FIELD in rule.conditions:
        hits[COND_FIELD] = folded_match(name, rule.conditions[COND_FIELD]) is not None
    if COND_TYPE in rule.conditions:
        patterns = rule.conditions[COND_TYPE]
        hits[COND_TYPE] = any(folded_match(t, patterns) for t in types)
    return hits


def classified_fields(rule: ClassificationRule, obj: ExplorerObject) -> list[str]:
    """Champs de l'objet que la règle classe (sans regarder les autres règles)."""
    if rule.files and path_matches(obj.origin, rule.files) is None:
        return []
    object_hits = _object_hits(rule, obj)
    if rule.match == MATCH_ALL and not all(object_hits.values()):
        return []
    has_field_conditions = any(k in FIELD_CONDITIONS for k in rule.conditions)
    names = obj.facts.field_names
    types = obj.facts.field_types
    out: list[str] = []
    for i, name in enumerate(names):
        field_hits = _field_hits(rule, name, types[i] if i < len(types) else ())
        if rule.match == MATCH_ALL:
            # Sans condition sur le champ, une règle de classe classe tous ses champs.
            ok = all(field_hits.values()) if has_field_conditions else True
        else:
            ok = any(field_hits.values()) or any(object_hits.values())
        if ok:
            out.append(name)
    return out


def valid_rules(
    rules: Iterable[ClassificationRule], schemes: list[Scheme]
) -> list[ClassificationRule]:
    """Règles actives dont l'effet correspond encore aux schémas."""
    return [r for r in rules if r.enabled and not effect_errors(r, schemes)]


def apply_classification_rules(
    sources: Iterable[SourceEntries],
    rules: Iterable[ClassificationRule],
    schemes: list[Scheme],
    removed: AbstractSet[tuple[str, str, str]] = frozenset(),
) -> list[SourceEntries]:
    """Les sources avec, sur chaque objet, les classifications posées par les
    règles (toujours recalculées en entier : un index relu pour un aperçu ne
    garde pas celles d'une règle modifiée). `removed` : (« source/fichier »,
    champ, schéma) retirés par une personne, que les règles ne reposent plus
    (ticket 77ce0d79)."""
    active = valid_rules(rules, schemes)
    by_id = {s.id: s for s in schemes}
    rank = {(s.id, lv.name): lv.rank for s in schemes for lv in s.levels}
    out: list[SourceEntries] = []
    for source in sources:
        objects: list[ExplorerObject] = []
        for obj in source.objects:
            found: dict[tuple[str, str], FieldClass] = {}
            for rule in active:
                multi = by_id[rule.scheme].multi_value
                for name in classified_fields(rule, obj):
                    key = (name, rule.scheme)
                    new = FieldClass(name, rule.scheme, rule.values, rule.id, rule.name)
                    current = found.get(key)
                    if current is None:
                        found[key] = new
                    elif multi:
                        values = tuple(dict.fromkeys(current.values + rule.values))
                        names = ", ".join(dict.fromkeys([current.rule_name, rule.name]))
                        ids = ",".join(dict.fromkeys([*current.rule.split(","), rule.id]))
                        found[key] = replace(current, values=values, rule=ids, rule_name=names)
                    elif rank.get((rule.scheme, rule.values[0]), 0) > rank.get(
                        (rule.scheme, current.values[0]), 0
                    ):
                        found[key] = new
            ident = f"{source.slug}/{obj.file}"
            kept = [c for c in found.values() if (ident, c.field, c.scheme) not in removed]
            ordered = tuple(sorted(kept, key=lambda c: (c.field, c.scheme)))
            objects.append(replace(obj, field_classes=ordered))
        out.append(replace(source, objects=objects))
    return out


def rule_usage(
    sources: Iterable[SourceEntries],
    rule_id: str,
    folder_of: Callable[[SourceEntries, ExplorerObject], str] | None = None,
) -> list[dict[str, object]]:
    """Champs classés actuellement par une règle (après arbitrage entre règles),
    par objet : pour le détail de la règle. `folder_of` : dossier de l'objet
    dans l'explorateur (lien « ouvrir »)."""
    rows: list[dict[str, object]] = []
    for source in sources:
        for obj in source.objects:
            fields = [c.field for c in obj.field_classes if rule_id in _rule_ids(c)]
            if fields:
                rows.append(
                    {
                        "source": source.slug,
                        "file": obj.file,
                        "title": obj.title,
                        "origin": obj.origin,
                        "folder": folder_of(source, obj) if folder_of else "",
                        "fields": fields,
                    }
                )
    return rows


def _rule_ids(c: FieldClass) -> set[str]:
    # Union multi-valeurs : plusieurs règles, ids séparés par des virgules.
    return set(c.rule.split(","))


def field_counts(sources: Iterable[SourceEntries]) -> dict[str, int]:
    """Nombre de champs classés par règle (liste des règles)."""
    counts: dict[str, int] = {}
    for source in sources:
        for obj in source.objects:
            for c in obj.field_classes:
                for rule_id in _rule_ids(c):
                    counts[rule_id] = counts.get(rule_id, 0) + 1
    return counts
