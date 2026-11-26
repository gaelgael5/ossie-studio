"""Format d'un type et d'un objet de cartographie (enabler f8167ae1).

**Type de l'espace** — `cartographie/types/<slug>.schema.yaml` : un type
ArchiMate 3.2 (`x-archimate`) et un nom donné par la personne (`title`), sous la
forme d'un JSON Schema 2020-12 écrit en YAML. `properties` ne porte que les
propriétés **ajoutées** ; la base (clé, nom, documentation, attributs propres au
type ArchiMate) vient du catalogue et n'est pas réécrite. Le slug (nom du
fichier) et `x-archimate` sont figés ; renommer change le `title`.

**Objet** — un fichier par objet, `cartographie/objets/<slug du type>/<clé en
minuscules>.yaml` : `cle`, `type`, `nom`, `documentation` (markdown), et
`proprietes` (attributs ArchiMate et propriétés ajoutées, validés par le schéma
du type). Une **relation** est un objet comme les autres, avec `source` et
`cible` (clés d'objets ou de relations). La clé fonctionnelle (majuscules,
chiffres, tirets) est unique dans l'espace, tous types confondus, et figée.

Écriture déterministe : même contenu, mêmes octets.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator

from ossie_studio.cartographie.catalog import ArchimateType, Catalog
from ossie_studio.cartographie.errors import CartographieError, Problem
from ossie_studio.parsers.conventions import SchemaReadError, load_schema
from ossie_studio.referentiels.model import check_property, dump_model
from ossie_studio.slugs import is_valid_slug

CARTO_DIR = "cartographie"
TYPES_DIR = f"{CARTO_DIR}/types"
OBJECTS_DIR = f"{CARTO_DIR}/objets"
TYPE_SUFFIX = ".schema.yaml"
OBJECT_SUFFIX = ".yaml"
ARCHIMATE_KEY = "x-archimate"
# La base de tout objet, hors `proprietes` : (nom, type, requis).
BASE_PROPERTIES: tuple[tuple[str, str, bool], ...] = (
    ("cle", "string", True),
    ("nom", "string", True),
    ("documentation", "string", False),
)
_KEY = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$")
_PROPERTY_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def is_valid_key(key: str) -> bool:
    """Clé fonctionnelle : majuscules, chiffres et tirets simples."""
    return bool(_KEY.fullmatch(key))


def type_path(slug: str) -> str:
    return f"{TYPES_DIR}/{slug}{TYPE_SUFFIX}"


def object_path(type_slug: str, key: str) -> str:
    """Le chemin d'un objet, déduit de son type et de sa clé (jamais choisi)."""
    return f"{OBJECTS_DIR}/{type_slug}/{key.lower()}{OBJECT_SUFFIX}"


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


# --- Types --------------------------------------------------------------------------------


@dataclass
class CartoType:
    slug: str
    title: str
    archimate: str
    properties: dict[str, Any] = field(default_factory=dict)  # propriétés ajoutées
    required: list[str] = field(default_factory=list)

    def document(self) -> dict[str, Any]:
        doc: dict[str, Any] = {"title": self.title, ARCHIMATE_KEY: self.archimate, "type": "object"}
        if self.properties:
            doc["properties"] = self.properties
        if self.required:
            doc["required"] = list(self.required)
        return doc


def parse_type(slug: str, text: str) -> CartoType:
    """Lit `<slug>.schema.yaml`. Lève CartographieError « type_unreadable »."""
    try:
        data = load_schema(text)
    except SchemaReadError as exc:
        raise CartographieError("type_unreadable", detail=str(exc)) from None
    if not isinstance(data, dict):
        raise CartographieError("type_unreadable", detail="not a mapping")
    properties = data.get("properties")
    required = data.get("required")
    return CartoType(
        slug=slug,
        title=str(data.get("title") or ""),
        archimate=str(data.get(ARCHIMATE_KEY) or ""),
        properties=properties if isinstance(properties, dict) else {},
        required=[str(r) for r in required] if isinstance(required, list) else [],
    )


def dump_type(ctype: CartoType) -> str:
    return dump_model(ctype.document())


def check_type(ctype: CartoType, catalog: Catalog) -> list[Problem]:
    """Les problèmes d'un type (liste vide : type valide)."""
    where = type_path(ctype.slug)
    problems: list[Problem] = []
    if not is_valid_slug(ctype.slug):
        problems.append(Problem(where, "type_slug_invalid", ctype.slug))
    if not ctype.title.strip():
        problems.append(Problem(where, "type_title_missing"))
    base = catalog.get(ctype.archimate)
    if base is None:
        problems.append(Problem(where, "type_archimate_unknown", ctype.archimate))
    reserved = {a.name for a in base.attributes} if base else set()
    for name, schema in ctype.properties.items():
        if not _PROPERTY_NAME.fullmatch(str(name)):
            problems.append(Problem(where, "property_name_invalid", field=str(name)))
        elif name in reserved:
            problems.append(Problem(where, "property_reserved", field=str(name)))
        problems.extend(
            Problem(where, p.code, p.detail, field=p.path)
            for p in check_property(schema, str(name))
        )
    for name in ctype.required:
        if name not in ctype.properties:
            problems.append(Problem(where, "required_unknown", field=name))
    return problems


def properties_schema(ctype: CartoType, base: ArchimateType | None) -> dict[str, Any]:
    """Le schéma des `proprietes` d'un objet de ce type : attributs propres au type
    ArchiMate, puis propriétés ajoutées ; rien d'autre n'est accepté."""
    properties: dict[str, Any] = {a.name: a.schema() for a in (base.attributes if base else ())}
    properties.update(ctype.properties)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if ctype.required:
        schema["required"] = list(ctype.required)
    return schema


# --- Objets -------------------------------------------------------------------------------


@dataclass
class CartoObject:
    key: str
    type: str  # slug d'un type de l'espace
    name: str
    documentation: str = ""
    properties: dict[str, Any] = field(default_factory=dict)
    source: str | None = None  # relation : clé de l'objet (ou de la relation) source
    target: str | None = None  # relation : clé de la cible

    @property
    def is_relation(self) -> bool:
        return self.source is not None or self.target is not None

    def document(self) -> dict[str, Any]:
        doc: dict[str, Any] = {"cle": self.key, "type": self.type}
        if self.source is not None:
            doc["source"] = self.source
        if self.target is not None:
            doc["cible"] = self.target
        doc["nom"] = self.name
        if self.documentation:
            doc["documentation"] = self.documentation
        properties = {k: v for k, v in self.properties.items() if not _empty(v)}
        if properties:
            doc["proprietes"] = properties
        return doc


def parse_object(text: str) -> CartoObject:
    """Lit le fichier d'un objet. Lève CartographieError « object_unreadable »."""
    try:
        data = load_schema(text)
    except SchemaReadError as exc:
        raise CartographieError("object_unreadable", detail=str(exc)) from None
    if not isinstance(data, dict):
        raise CartographieError("object_unreadable", detail="not a mapping")
    properties = data.get("proprietes")
    source, target = data.get("source"), data.get("cible")
    return CartoObject(
        key=str(data.get("cle") or ""),
        type=str(data.get("type") or ""),
        name=str(data.get("nom") or ""),
        documentation=str(data.get("documentation") or ""),
        properties=properties if isinstance(properties, dict) else {},
        source=str(source) if source is not None else None,
        target=str(target) if target is not None else None,
    )


def dump_object(obj: CartoObject) -> str:
    return dump_model(obj.document())


def check_object(
    obj: CartoObject, path: str, ctype: CartoType | None, catalog: Catalog
) -> list[Problem]:
    """Les problèmes propres à un objet (clé, nom, forme de relation, propriétés
    contre le schéma de son type) ; ceux qui dépendent des autres objets (clé en
    double, extrémités d'une relation) sont vus à la lecture de l'espace."""
    problems: list[Problem] = []
    if not is_valid_key(obj.key):
        problems.append(Problem(path, "object_key_invalid", obj.key))
    if not obj.name.strip():
        problems.append(Problem(path, "object_name_missing"))
    if ctype is None:
        problems.append(Problem(path, "object_type_unknown", obj.type))
        return problems
    base = catalog.get(ctype.archimate)
    relation = base is not None and base.is_relation
    if relation and (not obj.source or not obj.target):
        problems.append(Problem(path, "relation_endpoint_missing"))
    if not relation and obj.is_relation:
        problems.append(Problem(path, "object_not_relation"))
    properties = {k: v for k, v in obj.properties.items() if not _empty(v)}
    validator = Draft202012Validator(properties_schema(ctype, base))
    for error in sorted(validator.iter_errors(properties), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in error.absolute_path)
        if error.validator == "additionalProperties":
            for name in sorted(set(properties) - set(error.schema.get("properties", {}))):
                problems.append(Problem(path, "property_unknown", field=name))
        elif error.validator == "required":
            missing = re.findall(r"'([^']+)'", error.message)
            problems.append(Problem(path, "property_required", field=missing[0] if missing else ""))
        else:
            problems.append(Problem(path, "property_invalid", error.message, field=where))
    return problems


def object_location(path: str) -> tuple[str, str] | None:
    """(slug du type, nom de fichier sans extension) d'un chemin
    `cartographie/objets/<type>/<fichier>.yaml` ; None s'il est rangé ailleurs."""
    rel = path.removeprefix(f"{OBJECTS_DIR}/")
    parts = rel.split("/")
    if rel == path or len(parts) != 2 or not parts[1].endswith(OBJECT_SUFFIX):
        return None
    return parts[0], posixpath.splitext(parts[1])[0]
