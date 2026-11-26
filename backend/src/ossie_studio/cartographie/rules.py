"""Règles de relation et points de vue d'ArchiMate 3.2 (enabler 0e088eaa).

La source est la table d'Archi, recopiée telle quelle dans `archi/` (licence MIT,
voir `archi/README.md`) : on ne retape pas l'annexe des relations de la
spécification, on la lit. Rien n'est ajouté à ce que dit Archi.

- **Relations** : pour un couple (type ArchiMate source, type ArchiMate cible), les
  types de relation permis. Un type de relation pris comme extrémité (relation
  dont la cible est une relation) vaut le concept `Relationship` d'Archi.
- **Points de vue** : les éléments que chacun autorise. Comme dans Archi : un point
  de vue sans élément les autorise tous ; le regroupement et la jonction sont
  permis partout ; toutes les relations aussi (la table ci-dessus s'applique).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

from defusedxml import ElementTree as DefusedET  # type: ignore[import-untyped]

from ossie_studio.cartographie.catalog import Catalog, catalog

ARCHI_DIR = Path(__file__).with_name("archi")
RELATIONSHIP = "Relationship"
LETTERS = {
    "a": "Access",
    "c": "Composition",
    "f": "Flow",
    "g": "Aggregation",
    "i": "Assignment",
    "n": "Influence",
    "o": "Association",
    "r": "Realization",
    "s": "Specialization",
    "t": "Triggering",
    "v": "Serving",
}
LAYER_MACROS = {
    "$StrategyElements$": "strategy",
    "$BusinessElements$": "business",
    "$ApplicationElements$": "application",
    "$TechnologyElements$": "technology",
    "$PhysicalElements$": "physical",
    "$MotivationElements$": "motivation",
    "$ImplementationMigrationElements$": "implementation",
}
ALWAYS_ALLOWED = ("Grouping", "Junction")


class RulesError(ValueError):
    """Un fichier d'Archi que l'on ne sait pas lire (mise à jour à vérifier)."""


# --- Relations ----------------------------------------------------------------------------


@dataclass(frozen=True)
class RelationTable:
    version: str
    pairs: dict[tuple[str, str], frozenset[str]]

    def concepts(self) -> set[str]:
        return {c for pair in self.pairs for c in pair}


def parse_relationships(text: str) -> RelationTable:
    root = DefusedET.fromstring(text)
    pairs: dict[tuple[str, str], frozenset[str]] = {}
    for source in root.iter("source"):
        for target in source.iter("target"):
            letters = target.get("relations") or ""
            unknown = set(letters) - set(LETTERS)
            if unknown:
                raise RulesError(f"unknown relation letters {sorted(unknown)}")
            key = (str(source.get("concept")), str(target.get("concept")))
            pairs[key] = frozenset(LETTERS[letter] for letter in letters)
    return RelationTable(version=str(root.get("version") or ""), pairs=pairs)


@cache
def relation_table() -> RelationTable:
    return parse_relationships((ARCHI_DIR / "relationships.xml").read_text(encoding="utf-8"))


def _concept(name: str, cat: Catalog) -> str | None:
    found = cat.get(name)
    if found is None:
        return None
    return RELATIONSHIP if found.is_relation else found.name


def allowed_relations(source: str, target: str, cat: Catalog | None = None) -> list[str]:
    """Les types de relation permis du type ArchiMate `source` vers `target`, dans
    l'ordre du catalogue ; liste vide pour un type inconnu."""
    cat = cat or catalog()
    a, b = _concept(source, cat), _concept(target, cat)
    if a is None or b is None:
        return []
    permitted = relation_table().pairs.get((a, b), frozenset())
    return [r.name for r in cat.relations() if r.name in permitted]


def outgoing(source: str, cat: Catalog | None = None) -> dict[str, list[str]]:
    """Depuis le type `source` : chaque type cible possible → relations permises."""
    cat = cat or catalog()
    out = {t: allowed_relations(source, t, cat) for t in cat.types}
    return {t: rels for t, rels in out.items() if rels}


def incoming(target: str, cat: Catalog | None = None) -> dict[str, list[str]]:
    """Vers le type `target` : chaque type source possible → relations permises."""
    cat = cat or catalog()
    out = {s: allowed_relations(s, target, cat) for s in cat.types}
    return {s: rels for s, rels in out.items() if rels}


@cache
def involved_relations(name: str) -> tuple[str, ...]:
    """Les types de relation auxquels un type ArchiMate peut prendre part, comme
    source ou comme cible, dans l'ordre du catalogue (fenêtre « Ajouter un type »).
    Le regroupement et la jonction, qui acceptent toute relation, ne comptent pas
    comme autre extrémité : ils rendraient la liste identique pour tous."""
    cat = catalog()
    found: set[str] = set()
    for other in cat.types:
        if other in ALWAYS_ALLOWED and other != name:
            continue
        found.update(allowed_relations(name, other, cat))
        found.update(allowed_relations(other, name, cat))
    return tuple(r.name for r in cat.relations() if r.name in found)


# --- Points de vue ------------------------------------------------------------------------


@dataclass(frozen=True)
class Viewpoint:
    id: str
    label: str
    elements: tuple[str, ...]  # types ArchiMate des éléments autorisés, ordre du catalogue
    relations: tuple[str, ...]

    def allows(self, name: str) -> bool:
        return name in self.elements or name in self.relations


def viewpoint_id(archi_id: str) -> str:
    """L'identifiant d'un point de vue : celui d'Archi, tirets au lieu des soulignés."""
    return archi_id.replace("_", "-")


def parse_viewpoints(text: str, cat: Catalog) -> dict[str, Viewpoint]:
    root = DefusedET.fromstring(text)
    all_elements = [t.name for t in cat.elements()]
    relations = tuple(t.name for t in cat.relations())
    out: dict[str, Viewpoint] = {}
    for node in root.iter("viewpoint"):
        vid = viewpoint_id(str(node.get("id")))
        wanted: set[str] = set()
        for concept in node.iter("concept"):
            name = (concept.text or "").strip()
            if name in LAYER_MACROS:
                wanted.update(t.name for t in cat.elements(LAYER_MACROS[name]))
            elif name in all_elements:
                wanted.add(name)
            else:
                raise RulesError(f"viewpoint {vid}: unknown concept {name!r}")
        if wanted:
            wanted.update(ALWAYS_ALLOWED)
        else:
            wanted = set(all_elements)
        english = node.findtext("name") or vid
        out[vid] = Viewpoint(
            id=vid,
            label=cat.viewpoints.get(vid, english),
            elements=tuple(n for n in all_elements if n in wanted),
            relations=relations,
        )
    return out


@cache
def viewpoints() -> dict[str, Viewpoint]:
    """Les points de vue d'ArchiMate 3.2, par identifiant, dans l'ordre d'Archi."""
    text = (ARCHI_DIR / "viewpoints.xml").read_text(encoding="utf-8")
    return parse_viewpoints(text, catalog())
