"""Explorateur raw : vue graphe des références d'un objet.

Couche raw : les liens sont les **références lues dans les schémas** (code ou
catalogue), jamais des relations validées de la couche sémantique. Les
références sortantes viennent de l'index (calculées à sa construction) ; les
entrantes d'un index inverse bâti une fois par état de l'index (critère 8 : on
ne relit pas tous les schémas de l'espace à chaque affichage).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ossie_studio.sources.explorer import ExplorerObject, SchemaRef, SourceEntries, object_folder
from ossie_studio.sources.explorer_detail import SpaceObjects, Target

MAX_DEPTH = 3
# Au-delà, le graphe est tronqué et l'écran propose de filtrer (critère 7).
MAX_NODES = 40

NodeKey = tuple[str, str]  # (slug, fichier) ; ("", "ext:Nom") pour un type externe


@dataclass(frozen=True)
class _Located:
    entries: SourceEntries
    obj: ExplorerObject
    folder: str


class ReferenceGraph:
    """Objets de l'espace et index inverse de leurs références."""

    def __init__(self, sources: Iterable[SourceEntries], mode: str) -> None:
        source_list = list(sources)
        self.space = SpaceObjects(source_list, mode)
        self._objects: dict[NodeKey, _Located] = {}
        for entries in source_list:
            for obj in entries.objects:
                self._objects[(entries.slug, obj.file)] = _Located(
                    entries, obj, object_folder(mode, entries, obj)
                )
        self._incoming: dict[NodeKey, list[tuple[NodeKey, SchemaRef]]] = {}
        for key, located in self._objects.items():
            for ref in located.obj.refs:
                target = self._resolve(ref)
                if target is not None:
                    self._incoming.setdefault(target, []).append((key, ref))

    def _resolve(self, ref: SchemaRef) -> NodeKey | None:
        if ref.external:
            return None
        found: Target | None = (
            self.space.by_table(ref.target)
            if ref.kind == "foreign_key"
            else self.space.by_ref(ref.target)
        )
        return (found.slug, found.file) if found else None

    def incoming(self, key: NodeKey) -> list[dict[str, Any]]:
        """Références entrantes d'un objet : qui le référence, par quel champ,
        avec la cardinalité du champ dans l'objet d'origine."""
        out = []
        for source, ref in self._incoming.get(key, []):
            located = self._objects[source]
            out.append(
                {
                    "field": ref.field,
                    "kind": ref.kind,
                    "cardinality": ref.cardinality,
                    "target": {
                        "slug": source[0],
                        "file": source[1],
                        "title": located.obj.title,
                        "folder": located.folder,
                    },
                }
            )
        return sorted(out, key=lambda r: (r["target"]["title"], r["field"]))

    def _node(self, key: NodeKey, level: int) -> dict[str, Any]:
        located = self._objects.get(key)
        if located is None:  # type externe ou référence absente de l'espace
            return {
                "id": f"{key[0]}/{key[1]}",
                "slug": key[0],
                "file": "",
                "title": key[1].removeprefix("ext:"),
                "kind": "",
                "visibility": "",
                "folder": "",
                "description": "",
                "fields": [],
                "external": True,
                "level": level,
            }
        obj = located.obj
        return {
            "id": f"{key[0]}/{key[1]}",
            "slug": key[0],
            "file": obj.file,
            "title": obj.title,
            "kind": obj.kind,
            "visibility": obj.visibility,
            "folder": located.folder,
            "description": obj.description,
            "fields": list(obj.top_fields),
            "external": False,
            "level": level,
        }

    def build(self, center: NodeKey, depth: int = 1) -> dict[str, Any] | None:
        """Graphe centré sur `center` jusqu'à `depth` (1 à 3). Les sortantes
        s'étendent vers la droite (niveaux positifs), les entrantes vers la
        gauche ; un objet déjà placé ne l'est pas deux fois, un lien (cycle
        compris) n'est dessiné qu'une fois."""
        if center not in self._objects:
            return None
        depth = max(1, min(MAX_DEPTH, depth))
        levels: dict[NodeKey, int] = {center: 0}
        edges: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        truncated = False
        queue: deque[NodeKey] = deque([center])

        def place(key: NodeKey, level: int) -> bool:
            nonlocal truncated
            if key in levels:
                return True
            if len(levels) >= MAX_NODES:
                truncated = True
                return False
            levels[key] = level
            if abs(level) < depth and not key[1].startswith("ext:"):
                queue.append(key)
            return True

        def link(source: NodeKey, target: NodeKey, ref: SchemaRef, direction: str) -> None:
            ids = (f"{source[0]}/{source[1]}", f"{target[0]}/{target[1]}")
            edge_key = (*ids, ref.field, ref.kind)
            edges.setdefault(
                edge_key,
                {
                    "from": ids[0],
                    "to": ids[1],
                    "field": ref.field,
                    "kind": ref.kind,
                    "cardinality": ref.cardinality,
                    "direction": direction,
                },
            )

        while queue:
            key = queue.popleft()
            level = levels[key]
            out_level = level + 1 if level >= 0 else level - 1
            in_level = level - 1 if level <= 0 else level + 1
            for ref in self._objects[key].obj.refs:
                target = self._resolve(ref)
                if target is None:
                    target = ("", f"ext:{ref.target.rsplit('/', 1)[-1]}")
                if place(target, out_level):
                    link(key, target, ref, "outgoing")
            for source, ref in self._incoming.get(key, []):
                if place(source, in_level):
                    link(source, key, ref, "incoming")

        return {
            "center": f"{center[0]}/{center[1]}",
            "depth": depth,
            "truncated": truncated,
            "nodes": [self._node(k, lvl) for k, lvl in levels.items()],
            "edges": list(edges.values()),
        }
