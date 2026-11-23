"""Explorateur raw : détail d'un objet sélectionné (panneau d'aperçu).

Tout se lit dans le schéma de l'objet et dans l'index de l'espace (pour
résoudre les références vers les autres objets, toutes sources confondues).
L'héritage n'est **pas aplati** : les schémas ne le sont pas (collecte
incrémentale exacte), l'aperçu montre les champs propres et le parent comme
référence d'héritage.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ossie_studio.parsers.conventions import schema_key
from ossie_studio.sources.explorer import (
    VISIBILITY_SOURCE_KEY,
    VISIBILITY_UNKNOWN,
    ExplorerDocument,
    ExplorerObject,
    SourceEntries,
    object_folder,
    visibility_of,
)

# Natures de référence (clés stables, traduites côté interface).
REF_FIELD = "field"  # champ en `$ref` direct
REF_LIST = "list"  # tableau d'items `$ref`
REF_MAP = "map"  # dictionnaire (`additionalProperties` en `$ref`)
REF_FOREIGN_KEY = "foreign_key"  # colonne SQL avec `x-references`
REF_INHERITS = "inherits"  # `allOf` + `$ref`


@dataclass(frozen=True)
class Target:
    """Objet visé par une référence, là où l'explorateur peut l'ouvrir."""

    slug: str
    file: str
    title: str
    folder: str


class SpaceObjects:
    """Les objets de toutes les sources d'un espace, pour résoudre `$ref`
    (par `$id`) et `x-references` (par `schéma.table`)."""

    def __init__(self, sources: Iterable[SourceEntries], mode: str) -> None:
        self._by_id: dict[str, Target] = {}
        self._by_table: dict[str, Target] = {}
        self._by_title: dict[str, list[Target]] = {}
        for entries in sources:
            for obj in entries.objects:
                target = Target(
                    entries.slug, obj.file, obj.title, object_folder(mode, entries, obj)
                )
                for key in {obj.schema_id, obj.file}:
                    if key:
                        self._by_id.setdefault(key, target)
                # Une table SQL `schéma.table` a pour fichier `schéma.table.schema.yaml`.
                self._by_table.setdefault(schema_key(obj.file).casefold(), target)
                self._by_title.setdefault(obj.title, []).append(target)

    def by_ref(self, ref: str) -> Target | None:
        return self._by_id.get(ref) or self._by_id.get(ref.rsplit("/", 1)[-1])

    def by_table(self, table: str) -> Target | None:
        key = table.casefold()
        return self._by_table.get(key) or self._by_table.get(key.replace("_", "-"))

    def by_title(self, title: str) -> list[Target]:
        return self._by_title.get(title, [])


def _target_payload(target: Target | None) -> dict[str, str] | None:
    if target is None:
        return None
    return {
        "slug": target.slug,
        "file": target.file,
        "title": target.title,
        "folder": target.folder,
    }


def _annotations(node: dict[str, Any]) -> list[str]:
    value = node.get("x-annotations")
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [f"{k}: {v}" for k, v in value.items()]
    return []


def _field_type(prop: dict[str, Any], space: SpaceObjects) -> dict[str, Any]:
    """Type affichable d'un champ : type JSON Schema, objet référencé (cliquable)
    ou type non résolu tel qu'écrit (« externe », non cliquable)."""
    if "$ref" in prop:
        ref = str(prop["$ref"])
        target = space.by_ref(ref)
        return {"label": target.title if target else ref, "target": _target_payload(target)}
    if "x-unresolved-type" in prop:
        return {"label": str(prop["x-unresolved-type"]), "external": True}
    items = prop.get("items")
    if prop.get("type") == "array" and isinstance(items, dict):
        inner = _field_type(items, space)
        return {**inner, "label": f"{inner['label']}[]"}
    extra = prop.get("additionalProperties")
    if prop.get("type") == "object" and isinstance(extra, dict):
        inner = _field_type(extra, space)
        return {**inner, "label": f"map<{inner['label']}>"}
    if "enum" in prop:
        return {"label": "enum"}
    kind = prop.get("type")
    label = "/".join(str(k) for k in kind) if isinstance(kind, list) else str(kind or "any")
    if prop.get("format"):
        label = f"{label} ({prop['format']})"
    return {"label": label}


def _references(schema: dict[str, Any], space: SpaceObjects) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for part in schema.get("allOf") or []:
        if isinstance(part, dict) and "$ref" in part:
            ref = str(part["$ref"])
            refs.append(
                {
                    "field": "",
                    "kind": REF_INHERITS,
                    "label": ref,
                    "target": _target_payload(space.by_ref(ref)),
                }
            )
        elif isinstance(part, dict) and "x-unresolved-type" in part:
            refs.append(
                {
                    "field": "",
                    "kind": REF_INHERITS,
                    "label": str(part["x-unresolved-type"]),
                    "external": True,
                }
            )
    for name, prop in (schema.get("properties") or {}).items():
        if not isinstance(prop, dict):
            continue
        found = _prop_reference(name, prop, space)
        if found is not None:
            refs.append(found)
        fks = prop.get("x-references")
        for fk in fks if isinstance(fks, list) else [fks] if isinstance(fks, dict) else []:
            table = str(fk.get("table") or "")
            column = str(fk.get("column") or "")
            refs.append(
                {
                    "field": name,
                    "kind": REF_FOREIGN_KEY,
                    "label": f"{table}.{column}" if column else table,
                    "target": _target_payload(space.by_table(table)),
                }
            )
    return refs


def _prop_reference(name: str, prop: dict[str, Any], space: SpaceObjects) -> dict[str, Any] | None:
    node, kind = prop, REF_FIELD
    if prop.get("type") == "array" and isinstance(prop.get("items"), dict):
        node, kind = prop["items"], REF_LIST
    elif prop.get("type") == "object" and isinstance(prop.get("additionalProperties"), dict):
        node, kind = prop["additionalProperties"], REF_MAP
    if "$ref" in node:
        ref = str(node["$ref"])
        return {
            "field": name,
            "kind": kind,
            "label": ref,
            "target": _target_payload(space.by_ref(ref)),
        }
    if "x-unresolved-type" in node:
        return {
            "field": name,
            "kind": kind,
            "label": str(node["x-unresolved-type"]),
            "external": True,
        }
    return None


def _origin_line(schema: dict[str, Any], origin: str) -> int | None:
    """Ligne d'origine sous `x-source` (fichier principal), si le parseur la donne."""
    sources = schema.get("x-source")
    for item in sources if isinstance(sources, list) else [sources]:
        if isinstance(item, dict) and isinstance(item.get("line"), int):
            if not origin or item.get("file") == origin:
                return int(item["line"])
    return None


def _citing_documents(
    obj: ExplorerObject, documents: Iterable[ExplorerDocument]
) -> list[dict[str, str]]:
    """Documents dont l'index cite l'objet (son nom ou son `$id` sans extension)."""
    names = {obj.title, schema_key(obj.schema_id), schema_key(obj.file)}
    names.discard("")
    return [
        {"path": d.path, "title": d.title}
        for d in documents
        if names.intersection(d.mentions)
        or any(m.rsplit(".", 1)[-1] == obj.title for m in d.mentions)
    ]


def exposed_fields(asset: dict[str, Any] | None) -> set[str] | None:
    """Champs « exposés » d'un objet, ou None si l'objet n'est pas retenu.

    Décision du 18/11/2026 : tant que la couche dataset n'existe pas, un champ est
    exposé s'il est **retenu dans l'asset** (`sources/<slug>/assets/`). Quand
    l'epic « Modèle sémantique Ossie » existera, l'information viendra des
    `fields` du dataset : c'est cette seule fonction qui changera de source."""
    if asset is None:
        return None
    props = asset.get("properties")
    return set(props) if isinstance(props, dict) else set()


def _exposure(schema: dict[str, Any], asset: dict[str, Any] | None) -> dict[str, Any]:
    exposed = exposed_fields(asset)
    raw_fields = set((schema.get("properties") or {}).keys())
    if exposed is None:
        return {"retained": False, "exposed": 0, "hidden": len(raw_fields), "missing": []}
    return {
        "retained": True,
        "exposed": len(raw_fields & exposed),
        "hidden": len(raw_fields - exposed),
        # Exposé dans l'asset mais disparu du brut : en attente de revue.
        "missing": sorted(exposed - raw_fields),
    }


def object_detail(
    schema: dict[str, Any],
    text: str,
    obj: ExplorerObject,
    entries: SourceEntries,
    space: SpaceObjects,
    asset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    required = set(schema.get("required") or [])
    exposed = exposed_fields(asset) or set()
    fields = []
    for name, prop in (schema.get("properties") or {}).items():
        if not isinstance(prop, dict):
            continue
        fields.append(
            {
                "name": name,
                "type": _field_type(prop, space),
                "required": name in required,
                "description": str(prop.get("description") or ""),
                "annotations": _annotations(prop),
                # Un champ hérite du statut « code » de son objet : un champ de
                # classe sans x-visibility est « inconnu », jamais « vide ».
                "visibility": visibility_of(prop) or (VISIBILITY_UNKNOWN if obj.visibility else ""),
                "visibility_source": str(prop.get(VISIBILITY_SOURCE_KEY) or ""),
                "exposed": name in exposed,
            }
        )
    cardinality = {(r.field, r.kind): r.cardinality for r in obj.refs}
    references = [
        {**ref, "cardinality": cardinality.get((ref["field"], ref["kind"]), "")}
        for ref in _references(schema, space)
    ]
    return {
        "file": obj.file,
        "title": obj.title,
        "qualified_name": schema_key(obj.schema_id) or schema_key(obj.file),
        # Espace de noms tel qu'écrit dans le code (`x-namespace`) ; vide pour une
        # table ou un contrat, et pour un objet collecté avant que le parseur le pose.
        "namespace": str(schema.get("x-namespace") or ""),
        "kind": obj.kind,
        "badges": list(obj.badges),
        "excluded_by": obj.excluded_by,
        "visibility": obj.visibility,
        "visibility_source": obj.visibility_source,
        "description": str(schema.get("description") or ""),
        "enum": [str(v) for v in schema.get("enum") or []],
        "fields": fields,
        "references": references,
        "exposure": _exposure(schema, asset),
        "annotations": _annotations(schema),
        "origin": {
            "file": obj.origin,
            "line": _origin_line(schema, obj.origin),
            "checksum": obj.checksum,
            "parser": obj.parser,
            "branch": obj.branch,
            "raw_path": f"sources/{entries.slug}/raw/{obj.file}",
        },
        "documents": _citing_documents(obj, entries.documents),
        "json": text,
    }


def document_detail(doc: ExplorerDocument, space: SpaceObjects) -> dict[str, Any]:
    """Un document : titre, chemin, sections, et les objets qu'il cite (ceux que
    l'index sait rapprocher d'un objet de l'espace, cliquables)."""
    cited = []
    seen: set[tuple[str, str]] = set()
    for mention in doc.mentions:
        for target in space.by_title(mention.rsplit(".", 1)[-1]):
            if (target.slug, target.file) not in seen:
                seen.add((target.slug, target.file))
                cited.append(_target_payload(target))
    return {
        "path": doc.path,
        "title": doc.title,
        "sections": doc.sections,
        "mentions": list(doc.mentions),
        "objects": cited,
    }
