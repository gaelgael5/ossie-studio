"""Format d'un modèle de cartographie : le modèle, son fichier de règles et son
fichier graphique (enabler e338fe14, epic « Cartographie — modèles et vues »).

Les trois structures sont **les mêmes** que celles que lit et rend le composant
`urban-archimate` : son contrat (`docs/contrat.md` du dépôt) et son JSON Schema,
livré avec le paquet Python et relu ici. Une seule définition des formes pour les
deux dépôts ; le modèle stocké est le modèle hydraté sans les `data`.

**Le modèle** — `cartographie/modeles/<slug>.modele.yaml` : identifiant, nom,
description, chemin du fichier de règles et du fichier graphique, `documents`
(chemins des objets chargés), ce qu'on en **retient** (`elements`,
`relationships` : des clés) et les `vues`, dont les nœuds et connexions désignent
les objets par leur clé (notes et cadres n'existent que dans la vue). Ni
coordonnée ni contenu d'objet (décisions de Gael : « pas de graphique dans ce
document », les objets ne sont jamais recopiés).

**Le fichier de règles** — `cartographie/regles/<slug>.rules.yaml` : comment un
objet devient un élément ArchiMate, rangé par type de l'espace (à défaut, par type
ArchiMate). La règle d'un objet : celle de son type, sinon celle du type ArchiMate
sur lequel ce type repose (`x-archimate`), sinon la règle par défaut.

**Le fichier graphique** — `cartographie/modeles/<slug>.graphique.yaml`, un par
modèle : le dessin seul (positions absolues ou relatives, tailles, ancres, coudes,
position des libellés), par vue puis par nœud ou connexion.

Contrôles à la lecture, chacun avec l'identifiant en cause (`detail`) et sa place
dans le fichier (`field`) : une clé retenue absente des documents ; une relation
retenue dont la source ou la cible n'est pas retenue ; un nœud ou une connexion
qui désigne une clé non retenue, ou un nœud inconnu ; un document dont aucun objet
n'est retenu ; une entrée du graphique sans nœud ni connexion (ignorée et
signalée : un avertissement, qui ne refuse rien).

Écriture déterministe : même contenu, mêmes octets. La géométrie d'un nœud tient
sur une ligne : déplacer un nœud ne change qu'une ligne, du seul fichier graphique.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path
from typing import Any

import urban_archimate
import yaml
from jsonschema import Draft202012Validator

from ossie_studio.cartographie.catalog import Catalog, catalog
from ossie_studio.cartographie.errors import CartographieError, Problem
from ossie_studio.cartographie.model import CARTO_DIR, CartoObject, object_path, type_path
from ossie_studio.cartographie.rules import viewpoints
from ossie_studio.cartographie.space import Cartographie, read_cartographie, save_object
from ossie_studio.cartographie.space import delete_object as delete_space_object
from ossie_studio.parsers.conventions import SchemaReadError, load_schema
from ossie_studio.roundtrip import write_document
from ossie_studio.slugs import is_valid_slug

MODELS_DIR = f"{CARTO_DIR}/modeles"
RULES_DIR = f"{CARTO_DIR}/regles"
MODEL_SUFFIX = ".modele.yaml"
GRAPHIC_SUFFIX = ".graphique.yaml"
RULES_SUFFIX = ".rules.yaml"
# Le JSON Schema du modèle hydraté, tel que livré avec le composant.
CONTRACT_SCHEMA = urban_archimate.STATIC_DIR / "hydrated-model.schema.json"
# Un chemin de valeur du fichier de règles (`$.nom`, `$.proprietes.acces`) : la même
# grammaire que le composant (`src/rules/paths.ts`).
VALUE_PATH = re.compile(r"^\$(?:\.[A-Za-z0-9_-]+)+$")
# Le fichier de règles proposé à la création d'un modèle, et sa langue.
STANDARD_RULES = "standard"
DEFAULT_LANG = "fr"
# Signalé sans refuser : une entrée du graphique qui ne correspond à rien est ignorée.
WARNINGS = frozenset({"graphic_entry_unknown"})
# N'empêche pas d'ouvrir un modèle : le document en trop n'est pas transmis.
CLEANED_ON_OPEN = frozenset({"model_document_unused"})

# Ordre d'écriture des clés (celui du contrat).
_NODE_GEOMETRY = ("x", "y", "relatif", "dx", "dy", "l", "h")
_CONNECTION_GEOMETRY = ("ancre_source", "ancre_cible", "points", "libelle")
_LABEL = ("position", "dx", "dy")
_BLOCK = ("type", "name", "documentation", "properties")


def diagram_path(slug: str) -> str:
    return f"{MODELS_DIR}/{slug}{MODEL_SUFFIX}"


def graphic_path(slug: str) -> str:
    """Le fichier graphique d'un modèle : un par modèle, à côté de lui."""
    return f"{MODELS_DIR}/{slug}{GRAPHIC_SUFFIX}"


def rules_path(slug: str) -> str:
    return f"{RULES_DIR}/{slug}{RULES_SUFFIX}"


def is_warning(problem: Problem) -> bool:
    return problem.code in WARNINGS


# --- Schémas (ceux du composant) ----------------------------------------------------------


@cache
def contract_schema() -> dict[str, Any]:
    """Le JSON Schema du modèle hydraté livré avec `urban-archimate`."""
    data: dict[str, Any] = json.loads(CONTRACT_SCHEMA.read_text(encoding="utf-8"))
    return data


def _validator(schema: dict[str, Any]) -> Draft202012Validator:
    return Draft202012Validator({"$defs": contract_schema()["$defs"], **schema})


@cache
def _validators() -> dict[str, Draft202012Validator]:
    """Les formes des trois fichiers, tirées du schéma du composant : le modèle
    stocké est le modèle hydraté sans les `data` (chemins seuls, sans `types`)."""
    hydrated = contract_schema()
    path = {"type": "string", "minLength": 1}
    properties = {k: v for k, v in hydrated["properties"].items() if k != "types"}
    properties.update(regles=path, graphique=path, documents={"type": "array", "items": path})
    model = {
        "type": "object",
        "properties": properties,
        "required": hydrated["required"],
        "additionalProperties": False,
    }
    return {
        "model": _validator(model),
        "rules": _validator({"$ref": "#/$defs/regles"}),
        "graphic": _validator({"$ref": "#/$defs/graphique"}),
        "relation": _validator({"$ref": "#/$defs/objet"}),
    }


def _where(parts: list[str | int]) -> str:
    """`vues[0].noeuds[1].element` : la place d'une valeur dans le fichier."""
    out = ""
    for part in parts:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


def _shape(kind: str, path: str, data: Any) -> list[Problem]:
    validator = _validators()[kind]
    return [
        Problem(path, f"{kind}_invalid", error.message, field=_where(list(error.absolute_path)))
        for error in sorted(validator.iter_errors(data), key=lambda e: list(map(str, e.path)))
    ]


def _load(kind: str, path: str, text: str) -> Any:
    try:
        data = load_schema(text)
    except SchemaReadError as exc:
        raise CartographieError(f"{kind}_unreadable", detail=str(exc)) from None
    if data is None and kind != "model":
        return {}  # un fichier de règles ou un graphique vide : rien de propre
    if not isinstance(data, dict):
        raise CartographieError(f"{kind}_unreadable", detail="not a mapping")
    problems = _shape(kind, path, data)
    if problems:
        raise CartographieError(f"{kind}_invalid", problems=problems)
    return data


# --- Écriture déterministe ----------------------------------------------------------------

_BaseDumper = getattr(yaml, "CSafeDumper", yaml.SafeDumper)


class _Flow(dict[str, Any]):
    """Une géométrie : écrite sur une seule ligne."""


class _Dumper(_BaseDumper):  # type: ignore[misc,valid-type]
    """Texte sur plusieurs lignes en bloc littéral `|`, géométries sur une ligne."""


def _str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


def _flow(dumper: yaml.SafeDumper, value: _Flow) -> yaml.MappingNode:
    return dumper.represent_mapping("tag:yaml.org,2002:map", value, flow_style=True)


_Dumper.add_representer(str, _str)
_Dumper.add_representer(_Flow, _flow)


def _dump(doc: dict[str, Any]) -> str:
    return yaml.dump(
        doc,
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=1_000_000,
    )


def _number(value: Any) -> Any:
    """40.0 s'écrit 40 : un nombre entier ne change pas d'écriture au déplacement."""
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _ordered(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Les clés connues dans l'ordre du contrat, puis les autres dans leur ordre."""
    known = {k: data[k] for k in keys if k in data}
    return known | {k: v for k, v in data.items() if k not in keys}


# --- Le modèle ----------------------------------------------------------------------------


@dataclass
class Node:
    """Un nœud de vue : place un objet (`element`), une note ou un cadre. Son
    identifiant est propre à la vue : un même objet peut y figurer deux fois."""

    id: str
    element: str | None = None
    note: str | None = None
    frame: str | None = None  # cadre
    children: list[Node] = field(default_factory=list)

    def document(self) -> dict[str, Any]:
        doc: dict[str, Any] = {"id": self.id}
        for key, value in (("element", self.element), ("note", self.note), ("cadre", self.frame)):
            if value is not None:
                doc[key] = value
        if self.children:
            doc["enfants"] = [child.document() for child in self.children]
        return doc

    @staticmethod
    def parse(data: dict[str, Any]) -> Node:
        return Node(
            id=data["id"],
            element=data.get("element"),
            note=data.get("note"),
            frame=data.get("cadre"),
            children=[Node.parse(child) for child in data.get("enfants") or []],
        )


@dataclass
class Connection:
    """Une connexion de vue : place une relation (`relationship`) entre deux nœuds."""

    id: str
    relationship: str
    source: str  # de : identifiant du nœud
    target: str  # vers

    def document(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "relationship": self.relationship,
            "de": self.source,
            "vers": self.target,
        }


@dataclass
class View:
    id: str
    name: str = ""
    viewpoint: str = ""  # point de vue ArchiMate, facultatif
    nodes: list[Node] = field(default_factory=list)
    connections: list[Connection] = field(default_factory=list)

    def document(self) -> dict[str, Any]:
        doc: dict[str, Any] = {"id": self.id}
        if self.name:
            doc["nom"] = self.name
        if self.viewpoint:
            doc["point_de_vue"] = self.viewpoint
        if self.nodes:
            doc["noeuds"] = [node.document() for node in self.nodes]
        if self.connections:
            doc["connexions"] = [c.document() for c in self.connections]
        return doc

    def walk(self) -> Iterator[tuple[str, Node]]:
        """Tous les nœuds, imbriqués compris, avec leur place dans le fichier."""

        def visit(nodes: list[Node], where: str) -> Iterator[tuple[str, Node]]:
            for i, node in enumerate(nodes):
                place = f"{where}[{i}]"
                yield place, node
                yield from visit(node.children, f"{place}.enfants")

        yield from visit(self.nodes, "noeuds")


@dataclass
class Diagram:
    """Le document du modèle : des chemins et des clés, jamais de contenu d'objet."""

    id: str
    rules: str  # chemin du fichier de règles
    graphic: str  # chemin du fichier graphique
    name: str = ""
    description: str = ""
    documents: list[str] = field(default_factory=list)
    elements: list[str] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)
    views: list[View] = field(default_factory=list)

    def document(self) -> dict[str, Any]:
        doc: dict[str, Any] = {"id": self.id}
        if self.name:
            doc["nom"] = self.name
        if self.description:
            doc["description"] = self.description
        doc["regles"] = self.rules
        doc["graphique"] = self.graphic
        for key, values in (
            ("documents", self.documents),
            ("elements", self.elements),
            ("relationships", self.relationships),
        ):
            if values:
                doc[key] = list(values)
        if self.views:
            doc["vues"] = [view.document() for view in self.views]
        return doc


def parse_diagram(text: str, path: str = "") -> Diagram:
    """Lit le document d'un modèle. Lève CartographieError « model_unreadable » ou
    « model_invalid » (problèmes de forme, avec leur place)."""
    return diagram_from(_load("model", path, text))


def diagram_from(data: dict[str, Any]) -> Diagram:
    """Le modèle d'un document déjà contrôlé (forme du contrat)."""
    return Diagram(
        id=data["id"],
        rules=data["regles"],
        graphic=data["graphique"],
        name=data.get("nom") or "",
        description=data.get("description") or "",
        documents=list(data.get("documents") or []),
        elements=list(data.get("elements") or []),
        relationships=list(data.get("relationships") or []),
        views=[
            View(
                id=v["id"],
                name=v.get("nom") or "",
                viewpoint=v.get("point_de_vue") or "",
                nodes=[Node.parse(n) for n in v.get("noeuds") or []],
                connections=[
                    Connection(c["id"], c["relationship"], c["de"], c["vers"])
                    for c in v.get("connexions") or []
                ],
            )
            for v in data.get("vues") or []
        ],
    )


def dump_diagram(diagram: Diagram) -> str:
    return _dump(diagram.document())


# --- Le fichier graphique -----------------------------------------------------------------


@dataclass
class ViewGraphic:
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    connections: dict[str, dict[str, Any]] = field(default_factory=dict)

    def document(self) -> dict[str, Any]:
        doc: dict[str, Any] = {}
        if self.nodes:
            doc["noeuds"] = {
                k: _Flow({g: _number(v) for g, v in _ordered(geo, _NODE_GEOMETRY).items()})
                for k, geo in self.nodes.items()
            }
        if self.connections:
            doc["connexions"] = {k: _connection(geo) for k, geo in self.connections.items()}
        return doc


def _connection(geo: dict[str, Any]) -> dict[str, Any]:
    out = _ordered(geo, _CONNECTION_GEOMETRY)
    if "points" in out:
        out["points"] = [_Flow({k: _number(p[k]) for k in ("x", "y")}) for p in out["points"]]
    if "libelle" in out:
        out["libelle"] = _Flow({k: _number(v) for k, v in _ordered(out["libelle"], _LABEL).items()})
    return out


@dataclass
class Graphic:
    """Le dessin d'un modèle, par vue (clé : l'identifiant de la vue)."""

    views: dict[str, ViewGraphic] = field(default_factory=dict)

    def document(self) -> dict[str, Any]:
        return {"vues": {k: v.document() for k, v in self.views.items()}}

    def pruned(self, diagram: Diagram) -> Graphic:
        """Le graphique sans les entrées qui ne correspondent à aucune vue, aucun nœud
        ni aucune connexion du modèle, dans l'ordre du modèle."""
        out = Graphic()
        for view in diagram.views:
            current = self.views.get(view.id)
            if current is None:
                continue
            nodes = [node.id for _w, node in view.walk()]
            out.views[view.id] = ViewGraphic(
                nodes={n: current.nodes[n] for n in nodes if n in current.nodes},
                connections={
                    c.id: current.connections[c.id]
                    for c in view.connections
                    if c.id in current.connections
                },
            )
        return out


def parse_graphic(text: str, path: str = "") -> Graphic:
    """Lit un fichier graphique (vide : aucun dessin). Lève CartographieError
    « graphic_unreadable » ou « graphic_invalid »."""
    return graphic_from(_load("graphic", path, text))


def graphic_from(data: dict[str, Any]) -> Graphic:
    """Le graphique d'un document déjà contrôlé (forme du contrat)."""
    return Graphic(
        views={
            view_id: ViewGraphic(
                nodes=dict(view.get("noeuds") or {}),
                connections=dict(view.get("connexions") or {}),
            )
            for view_id, view in (data.get("vues") or {}).items()
        }
    )


def dump_graphic(graphic: Graphic) -> str:
    return _dump(graphic.document())


# --- Le fichier de règles -----------------------------------------------------------------


@dataclass
class Rules:
    """Les blocs de traduction : `elements` et `relationships` par type (de l'espace,
    à défaut ArchiMate), et un bloc `defaut` facultatif. Un bloc porte `name`,
    `documentation`, `properties` et des attributs ArchiMate (`accessType`…) dont
    les valeurs sont des chemins (`$.proprietes.acces`) ou des littéraux."""

    lang: str = ""
    default: dict[str, Any] | None = None
    elements: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[dict[str, Any]] = field(default_factory=list)

    def document(self) -> dict[str, Any]:
        doc: dict[str, Any] = {}
        if self.lang:
            doc["lang"] = self.lang
        if self.default is not None:
            doc["defaut"] = _block(self.default)
        if self.elements:
            doc["elements"] = [_block(b) for b in self.elements]
        if self.relationships:
            doc["relationships"] = [_block(b) for b in self.relationships]
        return doc

    def blocks(self) -> Iterator[tuple[str, dict[str, Any]]]:
        """Chaque bloc avec sa place dans le fichier."""
        if self.default is not None:
            yield "defaut", self.default
        for section in ("elements", "relationships"):
            for i, block in enumerate(getattr(self, section)):
                yield f"{section}[{i}]", block


def _block(block: dict[str, Any]) -> dict[str, Any]:
    out = _ordered(block, _BLOCK)
    if "properties" in out:
        out["properties"] = [
            _ordered(p, ("propertyDefinitionRef", "value")) for p in out["properties"]
        ]
    return out


def parse_rules(text: str, path: str = "") -> Rules:
    """Lit un fichier de règles (vide : aucune règle propre, la règle par défaut
    s'applique). Lève CartographieError « rules_unreadable » ou « rules_invalid »."""
    data = _load("rules", path, text)
    return Rules(
        lang=data.get("lang") or "",
        default=data.get("defaut"),
        elements=list(data.get("elements") or []),
        relationships=list(data.get("relationships") or []),
    )


def dump_rules(rules: Rules) -> str:
    return _dump(rules.document())


def _is_relation(space: Cartographie, cat: Catalog, obj: CartoObject) -> bool:
    ctype = space.types.get(obj.type)
    base = cat.get(ctype.archimate) if ctype else None
    return base.is_relation if base is not None else obj.is_relation


def default_rule(space: Cartographie, cat: Catalog | None = None) -> dict[str, Any]:
    """La règle par défaut, livrée avec Ossie (ticket 00d7cd6d) : elle s'applique à
    tout objet sans règle propre, si bien qu'un premier modèle s'affiche sans rien
    écrire. `name` ← nom, `documentation` ← documentation ; chaque attribut propre à un
    type ArchiMate (sens d'un accès, force d'une influence…) ← la même propriété de
    l'objet, déjà en valeur ArchiMate ; chaque propriété ajoutée à un type de l'espace
    ← une propriété ArchiMate du même nom, dont le composant déduit la définition du
    schéma du type. Un chemin qui ne mène à rien pour un objet n'y fait rien paraître."""
    cat = cat or catalog()
    block: dict[str, Any] = {"name": "$.nom", "documentation": "$.documentation"}
    for archimate_type in cat.types.values():
        for attribute in archimate_type.attributes:
            if attribute.exchange:
                block.setdefault(attribute.exchange, f"$.proprietes.{attribute.name}")
    names = dict.fromkeys(
        name for slug in sorted(space.types) for name in space.types[slug].properties
    )
    if names:
        block["properties"] = [
            {"propertyDefinitionRef": name, "value": f"$.proprietes.{name}"} for name in names
        ]
    return block


def with_default(rules: Rules, space: Cartographie, cat: Catalog | None = None) -> Rules:
    """Les règles telles que le composant les reçoit : le bloc `defaut` est toujours là
    (celui du fichier s'il en a un, sinon la règle par défaut d'Ossie)."""
    if rules.default is not None:
        return rules
    return replace(rules, default=default_rule(space, cat))


def rule_block(
    rules: Rules, obj: CartoObject, space: Cartographie, cat: Catalog | None = None
) -> dict[str, Any] | None:
    """Le bloc de règles d'un objet : celui de son type de l'espace, sinon celui du
    type ArchiMate sur lequel ce type repose, sinon le bloc par défaut (None : aucun)."""
    cat = cat or catalog()
    section = rules.relationships if _is_relation(space, cat, obj) else rules.elements
    by_type = {str(b.get("type")): b for b in section}
    ctype = space.types.get(obj.type)
    for candidate in (obj.type, ctype.archimate if ctype else None):
        if candidate is not None and candidate in by_type:
            return by_type[candidate]
    return rules.default


def check_rules(rules: Rules, path: str, space: Cartographie, cat: Catalog) -> list[Problem]:
    """Les problèmes d'un fichier de règles : un type inconnu (ni de l'espace ni
    ArchiMate), rangé dans la mauvaise section ou réglé deux fois, un chemin de
    valeur mal écrit."""
    problems: list[Problem] = []
    for section in ("elements", "relationships"):
        seen: set[str] = set()
        for i, block in enumerate(getattr(rules, section)):
            where, slug = f"{section}[{i}]", str(block.get("type"))
            if slug in seen:
                problems.append(Problem(path, "rules_type_duplicate", slug, field=where))
            seen.add(slug)
            ctype = space.types.get(slug)
            base = cat.get(ctype.archimate if ctype else slug)
            if base is None:
                problems.append(Problem(path, "rules_type_unknown", slug, field=where))
            elif base.is_relation != (section == "relationships"):
                problems.append(Problem(path, "rules_type_wrong_section", slug, field=where))
    for where, block in rules.blocks():
        values = [(k, v) for k, v in block.items() if k not in ("type", "properties")]
        values += [
            (f"properties[{i}].value", p.get("value"))
            for i, p in enumerate(block.get("properties") or [])
        ]
        for key, value in values:
            if isinstance(value, str) and value.startswith("$") and not VALUE_PATH.fullmatch(value):
                problems.append(Problem(path, "rules_path_invalid", value, field=f"{where}.{key}"))
    return problems


# --- Contrôles du modèle ------------------------------------------------------------------


def check_diagram(
    diagram: Diagram, path: str, slug: str, space: Cartographie, cat: Catalog
) -> list[Problem]:
    """Les contrôles du modèle contre la cartographie de la branche (les quatre
    premiers contrôles du contrat, et ceux qui garantissent qu'il se lit)."""
    problems: list[Problem] = []

    def add(code: str, detail: str, where: str) -> None:
        problems.append(Problem(path, code, detail, field=where))

    if not (diagram.rules.startswith(f"{RULES_DIR}/") and diagram.rules.endswith(RULES_SUFFIX)):
        add("model_rules_path_invalid", diagram.rules, "regles")
    if diagram.graphic != graphic_path(slug):
        add("model_graphic_path_invalid", diagram.graphic, "graphique")

    by_path = {p: o for p, o in space.objects.values()}
    loaded: dict[str, str] = {}  # clé -> document
    for i, doc in enumerate(diagram.documents):
        obj = by_path.get(doc)
        if doc in diagram.documents[:i]:
            add("model_document_duplicate", doc, f"documents[{i}]")
        elif obj is None:
            add("model_document_missing", doc, f"documents[{i}]")
        else:
            loaded[obj.key] = doc

    retained = set(diagram.elements) | set(diagram.relationships)
    for section, keys, relation in (
        ("elements", diagram.elements, False),
        ("relationships", diagram.relationships, True),
    ):
        for i, key in enumerate(keys):
            where = f"{section}[{i}]"
            if key in keys[:i]:
                add("model_key_repeated", key, where)
            elif key not in loaded:  # contrôle 1
                add("model_key_without_document", key, where)
            elif _is_relation(space, cat, by_path[loaded[key]]) != relation:
                if relation:
                    add("model_relationship_not_relation", key, where)
                else:
                    add("model_element_is_relation", key, where)
            elif relation:  # contrôle 2
                obj = by_path[loaded[key]]
                for end in (obj.source, obj.target):
                    if end not in retained:
                        add("model_relation_end_not_retained", key, where)

    known_viewpoints = viewpoints()
    view_ids: set[str] = set()
    for vi, view in enumerate(diagram.views):
        place = f"vues[{vi}]"
        if view.id in view_ids:
            add("model_view_duplicate", view.id, place)
        view_ids.add(view.id)
        if view.viewpoint and view.viewpoint not in known_viewpoints:
            add("model_viewpoint_unknown", view.viewpoint, f"{place}.point_de_vue")
        node_ids: set[str] = set()
        for where, node in view.walk():  # contrôle 3 (nœuds)
            if node.id in node_ids:
                add("model_node_duplicate", node.id, f"{place}.{where}")
            node_ids.add(node.id)
            if node.element is not None and node.element not in diagram.elements:
                add("model_node_not_retained", node.id, f"{place}.{where}.element")
        connection_ids: set[str] = set()
        for ci, connection in enumerate(view.connections):  # contrôle 3 (connexions)
            where = f"{place}.connexions[{ci}]"
            if connection.id in connection_ids:
                add("model_connection_duplicate", connection.id, where)
            connection_ids.add(connection.id)
            if connection.relationship not in diagram.relationships:
                add("model_connection_not_retained", connection.id, f"{where}.relationship")
            for end, node_id in (("de", connection.source), ("vers", connection.target)):
                if node_id not in node_ids:
                    add("model_connection_node_unknown", connection.id, f"{where}.{end}")

    for key, doc in loaded.items():  # contrôle 4
        if key not in retained:
            add("model_document_unused", doc, f"documents[{diagram.documents.index(doc)}]")
    return problems


def check_graphic(graphic: Graphic, path: str, diagram: Diagram) -> list[Problem]:
    """Contrôle 5 : une entrée du graphique sans vue, nœud ou connexion du modèle
    (ignorée et signalée, sans refus)."""
    problems: list[Problem] = []
    views = {view.id: view for view in diagram.views}
    for view_id, drawing in graphic.views.items():
        view = views.get(view_id)
        if view is None:
            problems.append(
                Problem(path, "graphic_entry_unknown", view_id, field=f"vues.{view_id}")
            )
            continue
        nodes = {node.id for _w, node in view.walk()}
        connections = {c.id for c in view.connections}
        for section, ids, known in (
            ("noeuds", drawing.nodes, nodes),
            ("connexions", drawing.connections, connections),
        ):
            for entry in ids:
                if entry not in known:
                    where = f"vues.{view_id}.{section}.{entry}"
                    problems.append(Problem(path, "graphic_entry_unknown", entry, field=where))
    return problems


# --- Lecture et écriture dans une copie de travail ----------------------------------------


@dataclass
class DiagramRead:
    """Un modèle lu avec ses fichiers et tout ce qui a été trouvé : `diagram` est
    None si le document du modèle est illisible, `rules` si les règles le sont."""

    slug: str
    path: str
    diagram: Diagram | None
    rules: Rules | None
    graphic: Graphic
    problems: list[Problem]

    @property
    def errors(self) -> list[Problem]:
        """Les problèmes qui empêchent d'ouvrir ou d'enregistrer le modèle."""
        return [p for p in self.problems if not is_warning(p)]


def list_diagrams(root: Path) -> list[str]:
    """Les slugs des modèles de la branche, par nom de fichier."""
    folder = root / MODELS_DIR
    if not folder.is_dir():
        return []
    return sorted(f.name[: -len(MODEL_SUFFIX)] for f in folder.glob(f"*{MODEL_SUFFIX}"))


def _read_file[T](
    root: Path, path: str, parse: Callable[[str, str], T], problems: list[Problem], kind: str
) -> T | None:
    """Lit un des trois fichiers ; un fichier absent ou illisible devient un problème."""
    file = root / path
    if not file.is_file():
        problems.append(Problem(path, f"{kind}_missing"))
        return None
    try:
        result = parse(file.read_text(encoding="utf-8"), path)
    except UnicodeDecodeError as exc:
        problems.append(Problem(path, f"{kind}_unreadable", str(exc)))
        return None
    except CartographieError as exc:
        problems.extend(
            exc.problems or [Problem(path, exc.code, str(exc.detail.get("detail", "")))]
        )
        return None
    return result


def read_diagram(
    root: Path, slug: str, space: Cartographie | None = None, cat: Catalog | None = None
) -> DiagramRead:
    """Lit un modèle, ses règles et son graphique, avec tous les contrôles.
    Refus nommé « model_not_found » si le modèle n'existe pas."""
    cat = cat or catalog()
    path = diagram_path(slug)
    if not is_valid_slug(slug) or not (root / path).is_file():
        raise CartographieError("model_not_found", status=404, slug=slug)
    problems: list[Problem] = []
    diagram = _read_file(root, path, parse_diagram, problems, "model")
    if diagram is None:
        return DiagramRead(slug, path, None, None, Graphic(), sorted(set(problems)))
    space = space if space is not None else read_cartographie(root, cat)
    problems.extend(check_diagram(diagram, path, slug, space, cat))
    rules = _read_file(root, diagram.rules, parse_rules, problems, "rules")
    if rules is not None:
        problems.extend(check_rules(rules, diagram.rules, space, cat))
    graphic = _read_file(root, diagram.graphic, parse_graphic, problems, "graphic")
    if graphic is not None:
        problems.extend(check_graphic(graphic, diagram.graphic, diagram))
    return DiagramRead(slug, path, diagram, rules, graphic or Graphic(), sorted(set(problems)))


def save_diagram(
    root: Path,
    slug: str,
    diagram: Diagram,
    graphic: Graphic,
    *,
    create: bool,
    cat: Catalog | None = None,
) -> list[Problem]:
    """Écrit le modèle et son graphique. Refus nommé (rien n'est écrit) si le modèle
    ne passe pas les contrôles (« model_invalid »), si son identifiant est déjà pris
    par un autre modèle, s'il existe déjà (création) ou n'existe pas (modification).
    À la création, un fichier de règles absent est créé vide (`lang` seule).

    Les entrées du graphique qui ne correspondent à rien ne sont pas écrites ; elles
    sont rendues en avertissement."""
    cat = cat or catalog()
    path = diagram_path(slug)
    if not is_valid_slug(slug):
        raise CartographieError("model_slug_invalid", slug=slug)
    exists = (root / path).exists()
    if create and exists:
        raise CartographieError("model_exists", status=409, slug=slug)
    if not create and not exists:
        raise CartographieError("model_not_found", status=404, slug=slug)
    for other in list_diagrams(root):
        if other != slug and _diagram_id(root, other) == diagram.id:
            raise CartographieError("model_id_taken", status=409, id=diagram.id, slug=other)
    problems = _shape("model", path, diagram.document())
    problems += _shape("graphic", diagram.graphic, graphic.document())
    missing_rules = not (root / diagram.rules).is_file()
    if not problems:
        space = read_cartographie(root, cat)
        problems = check_diagram(diagram, path, slug, space, cat)
        if missing_rules and not create:
            problems.append(Problem(diagram.rules, "rules_missing"))
    if problems:
        raise CartographieError("model_invalid", problems=problems)
    warnings = check_graphic(graphic, diagram.graphic, diagram)
    if missing_rules:  # création : le fichier de règles est créé vide, la règle par défaut suffit
        (root / RULES_DIR).mkdir(parents=True, exist_ok=True)
        (root / diagram.rules).write_text(dump_rules(Rules(lang=DEFAULT_LANG)), encoding="utf-8")
    (root / MODELS_DIR).mkdir(parents=True, exist_ok=True)
    _write_if_changed(root / path, diagram, parse_diagram, dump_diagram, Diagram.document)
    _write_if_changed(
        root / diagram.graphic,
        graphic.pruned(diagram),
        parse_graphic,
        dump_graphic,
        Graphic.document,
    )
    return sorted(set(warnings))


def _write_if_changed[T](
    file: Path,
    value: T,
    parse: Callable[[str, str], T],
    dump: Callable[[T], str],
    document: Callable[[T], Any],
) -> None:
    """Écrit `value` sauf si le fichier dit déjà la même chose : un fichier écrit à la
    main garde sa mise en forme (et ses commentaires) tant que son contenu ne change
    pas, et enregistrer un déplacement ne touche que le graphique."""
    if file.is_file():
        try:
            if parse(file.read_text(encoding="utf-8"), "") == value:
                return
        except (CartographieError, UnicodeDecodeError):
            pass  # illisible : réécrit
    # Contenu changé : aller-retour (14bb8eb8), seules les lignes changées bougent.
    write_document(file, document(value), lambda _d: dump(value))


def _diagram_id(root: Path, slug: str) -> str | None:
    try:
        return parse_diagram((root / diagram_path(slug)).read_text(encoding="utf-8")).id
    except (CartographieError, UnicodeDecodeError):
        return None


def save_rules(root: Path, slug: str, rules: Rules, cat: Catalog | None = None) -> Rules:
    """Écrit un fichier de règles ; refus nommé « rules_invalid » s'il ne passe pas
    les contrôles."""
    cat = cat or catalog()
    if not is_valid_slug(slug):
        raise CartographieError("rules_slug_invalid", slug=slug)
    path = rules_path(slug)
    problems = _shape("rules", path, rules.document())
    if not problems:
        problems = check_rules(rules, path, read_cartographie(root, cat), cat)
    if problems:
        raise CartographieError("rules_invalid", problems=problems)
    write_document(root / path, rules.document(), lambda _d: dump_rules(rules))
    return rules


def delete_diagram(root: Path, slug: str) -> None:
    """Supprime un modèle et son graphique ; jamais les objets, ni le fichier de
    règles (d'autres modèles peuvent s'en servir)."""
    path = diagram_path(slug)
    if not is_valid_slug(slug) or not (root / path).is_file():
        raise CartographieError("model_not_found", status=404, slug=slug)
    (root / path).unlink()
    graphic = root / graphic_path(slug)
    if graphic.is_file():
        graphic.unlink()


def list_rules(root: Path) -> list[str]:
    """Les slugs des fichiers de règles de la branche."""
    folder = root / RULES_DIR
    if not folder.is_dir():
        return []
    return sorted(f.name[: -len(RULES_SUFFIX)] for f in folder.glob(f"*{RULES_SUFFIX}"))


def models_retaining(root: Path, key: str) -> list[tuple[str, str]]:
    """Les modèles (slug, nom) qui retiennent un objet ou une relation."""
    out: list[tuple[str, str]] = []
    for slug in list_diagrams(root):
        try:
            diagram = parse_diagram((root / diagram_path(slug)).read_text(encoding="utf-8"))
        except (CartographieError, UnicodeDecodeError):
            continue
        if key in diagram.elements or key in diagram.relationships:
            out.append((slug, diagram.name or diagram.id))
    return out


def delete_object(root: Path, key: str, cat: Catalog | None = None) -> None:
    """Supprime un objet : refusé tant qu'un modèle le retient (« object_in_model »,
    avec les modèles), puis comme `space.delete_object` (relations qui l'utilisent)."""
    models = models_retaining(root, key)
    if models:
        raise CartographieError(
            "object_in_model",
            status=409,
            key=key,
            models=", ".join(name for _slug, name in models),
            slugs=[slug for slug, _name in models],
        )
    delete_space_object(root, key, cat)


# --- L'aperçu d'une vignette ----------------------------------------------------------------

# Taille d'un nœud sans `l`/`h` et pas de la mise en place de secours : ceux du
# composant (`src/model/geometry.ts`), pour que l'aperçu ressemble au dessin.
DEFAULT_NODE_SIZE = (120, 55)
_FALLBACK_STEP = (160, 95)


def resolve_geometry(view: View, drawing: ViewGraphic | None) -> dict[str, tuple[float, ...]]:
    """La géométrie absolue (x, y, largeur, hauteur) de chaque nœud d'une vue :
    position absolue, relative à un autre nœud, ou, sans géométrie, une grille
    dans l'ordre du modèle. Une géométrie circulaire retombe sur la grille."""
    geometries = drawing.nodes if drawing else {}
    order = [node.id for _w, node in view.walk()]
    resolved: dict[str, tuple[float, ...]] = {}

    def resolve(node_id: str, seen: frozenset[str]) -> tuple[float, ...]:
        if node_id in resolved:
            return resolved[node_id]
        geo = geometries.get(node_id) or {}
        width = geo.get("l", DEFAULT_NODE_SIZE[0])
        height = geo.get("h", DEFAULT_NODE_SIZE[1])
        parent = geo.get("relatif")
        if parent in order and parent not in seen:
            px, py, *_ = resolve(parent, seen | {node_id})
            x, y = px + geo.get("dx", 0), py + geo.get("dy", 0)
        elif "x" in geo or "y" in geo:
            x, y = geo.get("x", 0), geo.get("y", 0)
        else:
            index = order.index(node_id) if node_id in order else 0
            x = 20 + (index % 5) * _FALLBACK_STEP[0]
            y = 20 + (index // 5) * _FALLBACK_STEP[1]
        resolved[node_id] = (x, y, width, height)
        return resolved[node_id]

    for node_id in order:
        resolve(node_id, frozenset())
    return resolved


def preview(diagram: Diagram, graphic: Graphic, space: Cartographie) -> dict[str, Any]:
    """L'aperçu de la première vue, pour une vignette : des cadres (géométrie
    absolue, libellé, nature) et des traits d'un centre à l'autre."""
    if not diagram.views:
        return {"boxes": [], "links": []}
    view = diagram.views[0]
    geometry = resolve_geometry(view, graphic.views.get(view.id))
    boxes: list[dict[str, Any]] = []
    for _w, node in view.walk():
        x, y, width, height = geometry[node.id]
        if node.element is not None:
            found = space.objects.get(node.element)
            label, kind = (found[1].name if found else node.element), "element"
        elif node.note is not None:
            label, kind = node.note, "note"
        else:
            label, kind = node.frame or "", "cadre"
        boxes.append({"x": x, "y": y, "l": width, "h": height, "label": label, "kind": kind})
    links = []
    for connection in view.connections:
        ends = [geometry.get(connection.source), geometry.get(connection.target)]
        if ends[0] is None or ends[1] is None:
            continue
        (x1, y1, w1, h1), (x2, y2, w2, h2) = ends[0], ends[1]
        links.append({"x1": x1 + w1 / 2, "y1": y1 + h1 / 2, "x2": x2 + w2 / 2, "y2": y2 + h2 / 2})
    return {"boxes": boxes, "links": links}


# --- Boîte à outils et délégué de pose (ticket f5421cc7) ----------------------------------

# Le groupe des relations existantes : son libellé est donné par l'écran (traduit).
RELATIONS_GROUP = "relations"


def toolbox(
    space: Cartographie, viewpoint: str = "", cat: Catalog | None = None
) -> list[dict[str, Any]]:
    """La boîte à outils d'une vue : un outil (libellé, pictogramme, clé) par objet
    existant de l'espace, rangé par type, puis les relations existantes. Le point de
    vue de la vue (s'il en a un) filtre les types. Aucun outil de création d'objet
    (Gael : « juste ceux qui existent déjà ») ; notes et cadres viennent du composant."""
    cat = cat or catalog()
    vp = viewpoints().get(viewpoint) if viewpoint else None
    groups: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    for ctype in sorted(space.types.values(), key=lambda t: (t.title.casefold(), t.slug)):
        base = cat.get(ctype.archimate)
        if base is None or (vp is not None and not vp.allows(base.name)):
            continue
        objects = sorted(
            space.objects_of(ctype.slug), key=lambda po: (po[1].name.casefold(), po[1].key)
        )
        if base.is_relation:
            relations += [_relation_tool(space, obj, base.icon()) for _path, obj in objects]
        elif objects:
            tools = [
                {"cle": o.key, "libelle": o.name or o.key, "icone": base.icon()}
                for _p, o in objects
            ]
            groups.append({"id": ctype.slug, "libelle": ctype.title, "outils": tools})
    if relations:
        relations.sort(key=lambda tool: str(tool["libelle"]).casefold())
        groups.append({"id": RELATIONS_GROUP, "libelle": "", "outils": relations})
    return groups


def _relation_tool(space: Cartographie, obj: CartoObject, icon: str) -> dict[str, Any]:
    """Une relation existante : son nom, puis ses deux extrémités par leur nom."""

    def name(key: str | None) -> str:
        found = space.objects.get(key or "")
        return found[1].name if found else str(key)

    label = f"{obj.name or obj.key} ({name(obj.source)} → {name(obj.target)})"
    return {"cle": obj.key, "libelle": label, "icone": icon}


def drop(
    read: DiagramRead, space: Cartographie, key: str, cat: Catalog | None = None
) -> dict[str, Any]:
    """Ce que rend le délégué de pose pour une clé : l'objet (chemin et contenu), le
    schéma de son type, et son bloc de règles (règle propre au type, sinon celle de
    son type ArchiMate, sinon la règle par défaut). Refus nommé si l'objet n'existe pas."""
    found = space.objects.get(key)
    if found is None:
        raise CartographieError("object_not_found", status=404, key=key)
    path, obj = found
    out: dict[str, Any] = {"objet": {"chemin": path, "data": obj.document()}}
    ctype = space.types.get(obj.type)
    if ctype is not None:
        out["schemaType"] = {"chemin": type_path(ctype.slug), "data": ctype.document()}
    block = rule_block(with_default(read.rules or Rules(), space, cat), obj, space, cat)
    if block is not None:
        out["regles"] = block
    return out


# --- Enregistrer depuis le composant (ticket c3dd045c) ------------------------------------


def save_drawing(
    root: Path,
    slug: str,
    modele: Any,
    graphique: Any,
    new_relations: list[Any],
    cat: Catalog | None = None,
) -> tuple[list[str], list[Problem]]:
    """Enregistre ce que rend le composant : les nouvelles relations tracées (chacune
    créée comme tout objet : clé unique, schéma du type, relation permise), puis le
    modèle tel quel, les documents des nouvelles relations ajoutés, et son graphique,
    après les contrôles du format. Rend les clés créées et les avertissements.

    Une nouvelle relation refusée l'est avec sa clé (« model_relation_refused ») ;
    l'appelant écrit tout dans une seule opération du brouillon, si bien qu'un refus,
    à quelque étape que ce soit, n'écrit rien."""
    cat = cat or catalog()
    path = diagram_path(slug)
    if not is_valid_slug(slug) or not (root / path).is_file():
        raise CartographieError("model_not_found", status=404, slug=slug)
    problems = _shape("model", path, modele)
    problems += _shape("graphic", graphic_path(slug), graphique)
    if problems:
        raise CartographieError("model_invalid", problems=problems)
    created: list[CartoObject] = []
    for index, data in enumerate(new_relations):
        relation = _new_relation(data, index)
        try:
            save_object(root, relation, create=True, cat=cat)
        except CartographieError as exc:
            raise CartographieError(
                "model_relation_refused",
                status=exc.status,
                problems=exc.problems,
                key=relation.key,
                reason=exc.code,
            ) from None
        created.append(relation)
    diagram = diagram_from(modele)
    for relation in created:
        document = object_path(relation.type, relation.key)
        if document not in diagram.documents:
            diagram.documents.append(document)
    warnings = save_diagram(root, slug, diagram, graphic_from(graphique), create=False, cat=cat)
    return [relation.key for relation in created], warnings


def _new_relation(data: Any, index: int) -> CartoObject:
    """Une nouvelle relation rendue par le composant (forme d'un objet du contrat)."""
    key = str(data.get("cle") or "") if isinstance(data, dict) else ""
    problems = _shape("relation", f"nouvellesRelations[{index}]", data)
    if problems:
        raise CartographieError(
            "model_relation_refused", problems=problems, key=key, reason="relation_invalid"
        )
    properties = data.get("proprietes")
    return CartoObject(
        key=data["cle"],
        type=data["type"],
        name=str(data.get("nom") or "").strip(),
        documentation=str(data.get("documentation") or ""),
        properties=properties if isinstance(properties, dict) else {},
        source=data.get("source"),
        target=data.get("cible"),
    )


# --- Le modèle hydraté (entrée du composant) ----------------------------------------------


def hydrate(read: DiagramRead, space: Cartographie) -> dict[str, Any]:
    """Le modèle hydraté que reçoit `urban-archimate` : chaque référence (règles,
    graphique, schémas des types présents, objets) porte son contenu en `data`.

    Un modèle « à nettoyer » s'ouvre quand même : un document sans objet retenu n'est
    pas transmis (le composant le refuserait), et l'enregistrement le retire du
    modèle. Tout autre problème refuse l'ouverture (« model_invalid », avec eux)."""
    blocking = [p for p in read.errors if p.code not in CLEANED_ON_OPEN]
    if blocking or read.diagram is None or read.rules is None:
        raise CartographieError("model_invalid", status=409, problems=blocking, slug=read.slug)
    diagram = read.diagram
    by_path = {p: o for p, o in space.objects.values()}
    retained = set(diagram.elements) | set(diagram.relationships)
    objects = [(doc, by_path[doc]) for doc in diagram.documents if by_path[doc].key in retained]
    used = list(dict.fromkeys(obj.type for _doc, obj in objects))
    out: dict[str, Any] = {"id": diagram.id}
    if diagram.name:
        out["nom"] = diagram.name
    if diagram.description:
        out["description"] = diagram.description
    rules = with_default(read.rules, space)  # jamais sans bloc `defaut`
    out["regles"] = {"chemin": diagram.rules, "data": rules.document()}
    out["graphique"] = {"chemin": diagram.graphic, "data": read.graphic.document()}
    out["types"] = [
        {"chemin": type_path(slug), "data": space.types[slug].document()}
        for slug in used
        if slug in space.types
    ]
    out["documents"] = [{"chemin": doc, "data": obj.document()} for doc, obj in objects]
    stored = diagram.document()
    for key in ("elements", "relationships", "vues"):
        if key in stored:
            out[key] = stored[key]
    return out
