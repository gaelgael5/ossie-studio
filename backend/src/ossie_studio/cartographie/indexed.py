"""La cartographie vue par l'index des documents (ticket 06171fd3, epic « Index des
documents et validation avant publication ») : types et objets lus dans l'index, sans
parcourir le dépôt.

L'index garde ce que le service de la cartographie lit d'un type et d'un objet
(`parse_type`, `parse_object`, aux chemins que lit `read_cartographie`, voir
`doc_index/extract.py`). Mêmes règles que `read_cartographie` et `Cartographie` :
- un fichier illisible est ignoré ;
- la première occurrence d'un identifiant `type/CLÉ` l'emporte (ordre des chemins) ;
- une extrémité écrite en clé seule se résout si un seul type porte cette clé.

« Qui utilise cet objet » (`users_from_index`) se calcule par recherche inverse sur les
références sortantes de l'index : seules les relations qui citent l'objet sont lues.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncpg

from ossie_studio.cartographie.model import object_ref, split_ref
from ossie_studio.doc_index import search
from ossie_studio.doc_index.extract import ANY_TYPE, doc_key
from ossie_studio.doc_index.search import IndexedDoc, Snapshot
from ossie_studio.doc_index.store import ViewKey

CARTO_TYPES = ("carto_type", "carto_objet")


@dataclass(frozen=True)
class IndexedObject:
    path: str
    blob: str
    key: str
    type: str
    name: str
    source: str | None
    target: str | None
    excerpt: str = ""
    properties: tuple[dict[str, str], ...] = ()

    @property
    def ref(self) -> str:
        return object_ref(self.type, self.key)

    @property
    def is_relation(self) -> bool:
        return self.source is not None or self.target is not None


def indexed_object(doc: IndexedDoc) -> IndexedObject | None:
    """L'objet que le service de la cartographie lit dans ce fichier ; None s'il est
    illisible."""
    found = doc.fields.get("object")
    if doc.document_type != "carto_objet" or not doc.fields.get("readable"):
        return None
    if not isinstance(found, dict):
        return None
    return IndexedObject(
        doc.path,
        doc.blob,
        str(found.get("key") or ""),
        str(found.get("type") or ""),
        str(found.get("name") or ""),
        found.get("source"),
        found.get("cible"),
        str(found.get("excerpt") or ""),
        tuple(found.get("properties") or ()),
    )


class IndexedCartographie:
    """Les types et objets lisibles d'une vue, rangés comme `Cartographie`."""

    def __init__(self, snap: Snapshot) -> None:
        self.types: dict[str, dict[str, Any]] = {}
        # La version (empreinte Git) du fichier de chaque type.
        self.type_versions: dict[str, str] = {}
        for doc in snap.of("carto_type"):
            if doc.fields.get("readable"):
                slug = str(doc.identity.get("slug"))
                self.types[slug] = doc.fields
                self.type_versions[slug] = doc.blob
        self.objects: dict[str, IndexedObject] = {}
        self._keys: dict[str, list[str]] = {}
        self._users: dict[str, list[str]] | None = None
        for doc in snap.of("carto_objet"):
            obj = indexed_object(doc)
            if obj is not None and obj.ref not in self.objects:
                self._keys.setdefault(obj.key, []).append(obj.ref)
                self.objects[obj.ref] = obj

    def resolve(self, value: str | None) -> str | None:
        """Comme `Cartographie.resolve`."""
        if not value:
            return None
        type_slug, key = split_ref(value)
        if type_slug is not None:
            return value if value in self.objects else None
        refs = self._keys.get(key, [])
        return refs[0] if len(refs) == 1 else None

    def users(self, ref: str) -> list[str]:
        """Comme `Cartographie.users` : les relations qui désignent cet objet."""
        if self._users is None:
            index: dict[str, list[str]] = {}
            for other, obj in self.objects.items():
                for end in {self.resolve(obj.source), self.resolve(obj.target)}:
                    if end is not None and end != other:
                        index.setdefault(end, []).append(other)
            self._users = {k: sorted(v) for k, v in index.items()}
        return list(self._users.get(ref, []))

    def end(self, value: str | None) -> dict[str, Any] | None:
        """Une extrémité de relation, comme la liste des objets la rend."""
        if value is None:
            return None
        ref = self.resolve(value)
        if ref is None:
            key = split_ref(value)[1]
            return {"ref": value, "key": key, "name": None, "type": None, "exists": False}
        found = self.objects[ref]
        return {
            "ref": found.ref,
            "key": found.key,
            "name": found.name,
            "type": found.type,
            "exists": True,
            "path": found.path,
        }


async def load(conn: asyncpg.Connection, key: ViewKey) -> IndexedCartographie:
    return IndexedCartographie(await search.snapshot(conn, key, CARTO_TYPES))


async def users_from_index(conn: asyncpg.Connection, key: ViewKey, ref: str) -> list[str]:
    """Les relations (`type/CLÉ`) qui désignent l'objet `ref` comme source ou cible,
    comme `Cartographie.users`, par recherche inverse : seules les relations qui le
    citent (`type/CLÉ`, ou sa clé seule) sont lues."""
    type_slug, obj_key = split_ref(ref)
    by_ref = doc_key("carto_objet", type_slug or ANY_TYPE, obj_key)
    by_key = doc_key("carto_objet", ANY_TYPE, obj_key)
    citing = await search.citing(conn, key, [by_ref, by_key])
    # Une clé seule ne désigne l'objet que si un seul type la porte.
    holders = await search.holding(conn, key, by_key)
    unique = len({o.ref for d in holders if (o := indexed_object(d)) is not None}) == 1
    out: set[str] = set()
    for docs in citing.values():
        for doc in docs:
            obj = indexed_object(doc)
            if obj is None or obj.ref == ref:
                continue
            for end in (obj.source, obj.target):
                if not end:
                    continue
                end_type, end_key = split_ref(end)
                if end == ref or (end_type is None and end_key == obj_key and unique):
                    out.add(obj.ref)
    return sorted(out)
