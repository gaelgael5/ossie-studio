"""Calcul, côté serveur, du diagramme « réseau ferroviaire » des branches.

Fonction pure, sans git ni navigateur : à partir d'un graphe de commits en
mémoire, elle place chaque branche sur une **voie** stable, attribue chaque
commit à une voie, repère **aiguillages** (créations de branche) et
**jonctions** (fusions), marque les **gares** (versions) et les commits **non
publiés**. Le même calcul sert l'IHM, les rapports et les exports, et se teste
sans dépendance. Le placement est **déterministe** : deux appels sur les mêmes
données rendent le même résultat.
"""

from __future__ import annotations

import heapq
from collections.abc import Sequence
from dataclasses import dataclass

# Une forme par voie (en plus de la couleur) : lisible en cas de daltonisme.
SHAPES = ("circle", "square", "triangle", "diamond", "hexagon", "pentagon")


@dataclass(frozen=True)
class CommitInput:
    sha: str
    parents: tuple[str, ...]
    author: str
    date: str  # ISO, pour l'affichage
    timestamp: int  # date du commit (epoch), pour l'ordre
    subject: str


@dataclass(frozen=True)
class BranchInput:
    ref: str
    name: str
    slug: str
    kind: str  # 'root' | 'base' | 'child'
    tip: str  # sha de la tête


@dataclass(frozen=True)
class VersionAt:
    sha: str
    number: int
    label: str
    author: str


@dataclass(frozen=True)
class Line:
    ref: str
    name: str
    slug: str
    kind: str
    lane: int
    shape: str


@dataclass(frozen=True)
class Node:
    sha: str
    short: str
    lane: int
    row: int
    kind: str  # 'commit' | 'version' | 'unpublished'
    subject: str
    author: str
    date: str
    is_switch: bool
    is_merge: bool
    number: int | None
    label: str | None
    version_author: str | None


@dataclass(frozen=True)
class Edge:
    child: str
    parent: str
    from_lane: int
    to_lane: int
    from_row: int
    to_row: int | None  # None = parent hors fenêtre (la ligne continue plus bas)
    kind: str  # 'normal' | 'switch' | 'merge'


@dataclass(frozen=True)
class RailMap:
    lines: list[Line]
    nodes: list[Node]
    edges: list[Edge]
    total: int
    offset: int
    limit: int
    has_more: bool


def _topo_newest_first(commits: Sequence[CommitInput], by_sha: dict[str, CommitInput]) -> list[str]:
    """Ordre déterministe du plus récent au plus ancien, un enfant toujours
    avant ses parents (tri topologique de Kahn, départage par date puis sha)."""
    remaining: dict[str, int] = {c.sha: 0 for c in commits}
    for c in commits:
        for p in c.parents:
            if p in by_sha:
                remaining[p] += 1
    heap: list[tuple[int, str]] = [
        (-by_sha[s].timestamp, s) for s, left in remaining.items() if left == 0
    ]
    heapq.heapify(heap)
    order: list[str] = []
    while heap:
        _, sha = heapq.heappop(heap)
        order.append(sha)
        for p in by_sha[sha].parents:
            if p in by_sha:
                remaining[p] -= 1
                if remaining[p] == 0:
                    heapq.heappush(heap, (-by_sha[p].timestamp, p))
    return order


def _assign_lanes(
    branches: Sequence[BranchInput], by_sha: dict[str, CommitInput]
) -> tuple[list[Line], dict[str, int]]:
    """Une voie par branche (racine en 0, puis l'ordre fourni). Chaque commit est
    attribué à la voie de la branche de plus haute priorité qui l'atteint : le
    tronc commun reste sur la racine, le segment propre d'une branche sur sa
    voie."""
    lines = [
        Line(b.ref, b.name, b.slug, b.kind, lane, SHAPES[lane % len(SHAPES)])
        for lane, b in enumerate(branches)
    ]
    owner: dict[str, int] = {}
    for lane, b in enumerate(branches):
        if b.tip not in by_sha:
            continue  # branche supprimée sur la forge : pas de ligne vivante
        stack = [b.tip]
        while stack:
            sha = stack.pop()
            if sha in owner or sha not in by_sha:
                # Déjà attribué (priorité plus haute) → ses ancêtres le sont aussi.
                continue
            owner[sha] = lane
            stack.extend(by_sha[sha].parents)
    return lines, owner


def build_railmap(
    commits: Sequence[CommitInput],
    branches: Sequence[BranchInput],
    versions: Sequence[VersionAt],
    unpublished: Sequence[str],
    *,
    offset: int = 0,
    limit: int = 200,
) -> RailMap:
    by_sha = {c.sha: c for c in commits}
    lines, owner = _assign_lanes(branches, by_sha)
    order = _topo_newest_first(commits, by_sha)
    total = len(order)
    window = order[offset : offset + limit]
    win_row = {sha: i for i, sha in enumerate(window)}
    versions_by_sha = {v.sha: v for v in versions}
    unpub = set(unpublished)

    nodes: list[Node] = []
    for i, sha in enumerate(window):
        c = by_sha[sha]
        lane = owner.get(sha, 0)
        known_parents = [p for p in c.parents if p in by_sha]
        is_merge = len(known_parents) >= 2
        first_parent = c.parents[0] if c.parents else None
        is_switch = bool(
            first_parent is not None
            and first_parent in owner
            and owner[first_parent] != lane
        )
        version = versions_by_sha.get(sha)
        if sha in unpub:
            kind = "unpublished"
        elif version is not None:
            kind = "version"
        else:
            kind = "commit"
        nodes.append(
            Node(
                sha=sha,
                short=sha[:8],
                lane=lane,
                row=i,
                kind=kind,
                subject=c.subject,
                author=c.author,
                date=c.date,
                is_switch=is_switch,
                is_merge=is_merge,
                number=version.number if version else None,
                label=version.label if version else None,
                version_author=version.author if version else None,
            )
        )

    edges: list[Edge] = []
    for sha in window:
        c = by_sha[sha]
        lane = owner.get(sha, 0)
        for index, parent in enumerate(c.parents):
            if parent not in by_sha:
                continue
            parent_lane = owner.get(parent, lane)
            if index >= 1:
                # Second parent et au-delà : une fusion rejoint la voie (jonction).
                kind = "merge"
            elif parent_lane != lane:
                # Premier parent sur une autre voie : création de branche (aiguillage).
                kind = "switch"
            else:
                kind = "normal"
            edges.append(
                Edge(
                    child=sha,
                    parent=parent,
                    from_lane=lane,
                    to_lane=parent_lane,
                    from_row=win_row[sha],
                    to_row=win_row.get(parent),
                    kind=kind,
                )
            )

    return RailMap(
        lines=lines,
        nodes=nodes,
        edges=edges,
        total=total,
        offset=offset,
        limit=limit,
        has_more=offset + limit < total,
    )
