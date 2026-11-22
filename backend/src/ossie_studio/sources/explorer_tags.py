"""Explorateur raw : application des règles de tags aux objets de l'espace.

Le calcul se fait à la construction de l'index (une fois par état du dépôt et
du brouillon), sur **toutes les sources à la fois** : une chaîne d'héritage
peut traverser deux sources de l'espace. Le résultat (tags et provenance) est
mis en index avec les objets ; l'affichage ne recalcule rien.

Deux temps :
1. correspondance directe — chaque règle active regarde l'objet lui-même (ses
   bases directes, ses attributs, son nom, son fichier, ses champs) ;
2. transmission — pour une règle `inherit_tags`, le tag descend aux classes qui
   dérivent de l'objet reconnu (ou implémentent l'interface reconnue), à toute
   profondeur. Une correspondance directe l'emporte sur un tag hérité (le tag
   n'est alors pas « hérité » : il ne suivra pas un retrait sur la classe mère).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import replace

from ossie_studio.governance.tag_rules import (
    COND_ANNOTATION,
    COND_FIELD,
    COND_INHERITS,
    COND_NAME,
    COND_PATH,
    MATCH_ALL,
    TagRule,
    name_matches,
    path_matches,
)
from ossie_studio.sources.explorer import ExplorerObject, ObjectTag, SourceEntries
from ossie_studio.sources.explorer_detail import SpaceObjects

ObjKey = tuple[str, str]  # (slug, fichier)


def _base_names(obj: ExplorerObject, titles: dict[ObjKey, str], space: SpaceObjects) -> list[str]:
    """Noms des bases directes : le titre de l'objet visé si la base est dans
    l'espace, le nom tel qu'écrit si elle est externe (`ControllerBase`)."""
    names: list[str] = []
    for ref in obj.refs:
        if ref.kind != "inherits":
            continue
        if ref.external:
            names.append(ref.target)
            continue
        found = space.by_ref(ref.target)
        names.append(titles.get((found.slug, found.file), "") if found else ref.target)
    return [n for n in names if n]


def _match(rule: TagRule, obj: ExplorerObject, bases: list[str]) -> tuple[str, str] | None:
    """(condition, valeur reconnue) si la règle s'applique à l'objet."""
    if path_matches(obj.origin, rule.files) is None:
        return None
    hits: list[tuple[str, str]] = []
    for kind, patterns in rule.conditions.items():
        hit: str | None = None
        if kind == COND_INHERITS:
            hit = next((b for b in bases if name_matches(b, patterns)), None)
        elif kind == COND_ANNOTATION:
            hit = next((a for a in obj.facts.annotations if name_matches(a, patterns)), None)
        elif kind == COND_NAME:
            hit = obj.title if name_matches(obj.title, patterns) else None
        elif kind == COND_PATH:
            hit = obj.origin if path_matches(obj.origin, patterns) else None
        elif kind == COND_FIELD:
            hit = next((f for f in obj.facts.field_names if name_matches(f, patterns)), None)
        if hit is not None:
            hits.append((kind, hit))
        elif rule.match == MATCH_ALL:
            return None
    return hits[0] if hits else None


def apply_tag_rules(
    sources: Iterable[SourceEntries],
    rules: Iterable[TagRule],
    removals: Iterable[tuple[str, str]] = (),
) -> list[SourceEntries]:
    """Les sources avec, sur chaque objet, les tags posés par les règles (et
    seulement eux). `removals` : (« source/fichier », tag) retirés par
    l'utilisateur — le tag ne se pose pas sur cet objet et ne descend pas par
    lui ; un tag hérité reste ainsi aligné sur la classe dont il vient. Un
    descendant qui satisfait lui-même une règle garde le tag (il n'est pas
    hérité)."""
    source_list = list(sources)
    active = [r for r in rules if r.enabled]
    if not active:
        return [
            replace(s, objects=[replace(o, tags=(), removed_tags=()) for o in s.objects])
            for s in source_list
        ]
    removed_set = set(removals)
    space = SpaceObjects(source_list, "origin")
    objects: dict[ObjKey, ExplorerObject] = {
        (s.slug, o.file): o for s in source_list for o in s.objects
    }
    titles = {key: obj.title for key, obj in objects.items()}
    bases = {key: _base_names(obj, titles, space) for key, obj in objects.items()}

    def is_removed(key: ObjKey, tag: str) -> bool:
        return (f"{key[0]}/{key[1]}", tag) in removed_set

    # Classes qui dérivent directement de chaque objet de l'espace.
    children: dict[ObjKey, list[ObjKey]] = {}
    for key, obj in objects.items():
        for ref in obj.refs:
            if ref.kind == "inherits" and not ref.external:
                found = space.by_ref(ref.target)
                if found is not None and (found.slug, found.file) != key:
                    children.setdefault((found.slug, found.file), []).append(key)

    tags: dict[ObjKey, dict[str, ObjectTag]] = {}
    removed: dict[ObjKey, dict[str, ObjectTag]] = {}
    transmitted: list[tuple[ObjKey, TagRule, str]] = []
    for rule in active:
        for key, obj in objects.items():
            hit = _match(rule, obj, bases[key])
            if hit is None:
                continue
            for tag in rule.tags:
                found_tag = ObjectTag(tag, rule.id, rule.name, hit[0], hit[1])
                if is_removed(key, tag):
                    removed.setdefault(key, {}).setdefault(tag, found_tag)
                    continue  # retiré ici : rien ne descend par lui
                tags.setdefault(key, {}).setdefault(tag, found_tag)
                if rule.inherit_tags:
                    transmitted.append((key, rule, tag))

    for origin, rule, tag in transmitted:
        # Parcours en largeur : la classe la plus proche est retenue comme
        # provenance ; `seen` coupe les cycles (deux classes homonymes).
        seen = {origin}
        queue = deque([origin])
        while queue:
            parent = queue.popleft()
            for child in children.get(parent, []):
                if child in seen:
                    continue
                seen.add(child)
                inherited = ObjectTag(
                    tag,
                    rule.id,
                    rule.name,
                    via=f"{parent[0]}/{parent[1]}",
                    via_title=titles[parent],
                )
                if is_removed(child, tag):
                    # Retiré sur cette classe : ni elle ni ses descendantes ne
                    # le tiennent par elle.
                    if tag not in tags.get(child, {}):
                        removed.setdefault(child, {}).setdefault(tag, inherited)
                    continue
                tags.setdefault(child, {}).setdefault(tag, inherited)
                queue.append(child)

    def ordered(found: dict[str, ObjectTag] | None) -> tuple[ObjectTag, ...]:
        return tuple(sorted((found or {}).values(), key=lambda t: t.tag))

    # Les tags sont toujours recalculés en entier : des tags déjà présents
    # (index relu pour un aperçu) ne doivent pas survivre à la règle modifiée.
    return [
        replace(
            s,
            objects=[
                replace(
                    o,
                    tags=ordered(tags.get((s.slug, o.file))),
                    removed_tags=ordered(removed.get((s.slug, o.file))),
                )
                for o in s.objects
            ],
        )
        for s in source_list
    ]


def index_removals(sources: Iterable[SourceEntries]) -> set[tuple[str, str]]:
    """Retraits déjà appliqués dans un index (relu pour un aperçu) : on les
    retrouve sur les objets, sans relire le dépôt."""
    return {
        (f"{s.slug}/{o.file}", t.tag) for s in sources for o in s.objects for t in o.removed_tags
    }


def dependents_losing(sources: Iterable[SourceEntries], obj_key: str, tag: str) -> int:
    """Nombre d'objets qui perdraient `tag` si on le retirait de `obj_key` : ceux
    qui le tiennent par héritage d'une chaîne passant par cet objet."""
    via: dict[str, str] = {}
    for s in sources:
        for o in s.objects:
            for t in o.tags:
                if t.tag == tag and t.via:
                    via[f"{s.slug}/{o.file}"] = t.via
    count = 0
    for start in via:
        current, steps = via.get(start), 0
        while current and steps <= len(via):
            if current == obj_key:
                count += 1
                break
            current, steps = via.get(current), steps + 1
    return count
