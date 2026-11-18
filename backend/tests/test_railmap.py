from __future__ import annotations

import time

from ossie_studio.spaces.railmap import (
    BranchInput,
    CommitInput,
    VersionAt,
    build_railmap,
)


def _c(sha: str, parents: tuple[str, ...], ts: int) -> CommitInput:
    return CommitInput(
        sha=sha, parents=parents, author="a", date=f"2026-01-{ts:02d}T00:00:00Z",
        timestamp=ts, subject=f"commit {sha}",
    )


# Graphe : racine (c1<-c2), base tirée de c2 (b1<-b2), fille-a tirée de b1
# (a1<-a2), fille-b tirée de b2 (d1). Les timestamps croissent avec la récence.
def _base_graph() -> list[CommitInput]:
    return [
        _c("c1", (), 1),
        _c("c2", ("c1",), 2),
        _c("b1", ("c2",), 3),
        _c("b2", ("b1",), 4),
        _c("a1", ("b1",), 5),
        _c("a2", ("a1",), 6),
        _c("d1", ("b2",), 7),
    ]


def _base_branches() -> list[BranchInput]:
    return [
        BranchInput("origin/main", "main", "", "root", "c2"),
        BranchInput("work/principale", "principale", "principale", "base", "b2"),
        BranchInput("work/feature-a", "feature-a", "feature-a", "child", "a2"),
        BranchInput("work/feature-b", "feature-b", "feature-b", "child", "d1"),
    ]


def test_trois_lignes_plus_la_racine_et_aiguillages() -> None:
    versions = [
        VersionAt("c2", 1, "socle", "Alice"),
        VersionAt("b2", 2, "jalon", "Alice"),
        VersionAt("a2", 3, "démo", "Bob"),
    ]
    rm = build_railmap(_base_graph(), _base_branches(), versions, [])
    # Quatre voies : racine + trois branches de travail.
    assert [ln.lane for ln in rm.lines] == [0, 1, 2, 3]
    lane = {n.sha: n.lane for n in rm.nodes}
    assert lane == {"c1": 0, "c2": 0, "b1": 1, "b2": 1, "a1": 2, "a2": 2, "d1": 3}
    # Aiguillages au premier commit propre de chaque branche.
    switches = {n.sha for n in rm.nodes if n.is_switch}
    assert switches == {"b1", "a1", "d1"}
    # Trois gares, avec leurs numéros.
    stations = {n.sha: n.number for n in rm.nodes if n.kind == "version"}
    assert stations == {"c2": 1, "b2": 2, "a2": 3}


def test_placement_deterministe_quel_que_soit_l_ordre_d_entree() -> None:
    graph = _base_graph()
    shuffled = list(reversed(graph))
    rm1 = build_railmap(graph, _base_branches(), [], [])
    rm2 = build_railmap(shuffled, _base_branches(), [], [])
    rows1 = [(n.sha, n.row, n.lane) for n in rm1.nodes]
    rows2 = [(n.sha, n.row, n.lane) for n in rm2.nodes]
    assert rows1 == rows2
    # Le plus récent d'abord (row 0 = commit le plus récent).
    assert rm1.nodes[0].sha == "d1"


def test_fusion_donne_une_jonction_et_commits_restent_apres_suppression() -> None:
    # fille-a supprimée hors d'Ossie, mais fusionnée dans la base : ses commits
    # restent visibles, portés par la ligne de la base.
    graph = [
        _c("c1", (), 1),
        _c("c2", ("c1",), 2),
        _c("b1", ("c2",), 3),
        _c("b2", ("b1",), 4),
        _c("a1", ("b1",), 5),
        _c("a2", ("a1",), 6),
        _c("m1", ("b2", "a2"), 7),  # fusion de fille-a dans la base
    ]
    branches = [
        BranchInput("origin/main", "main", "", "root", "c2"),
        BranchInput("work/principale", "principale", "principale", "base", "m1"),
    ]
    rm = build_railmap(graph, branches, [], [])
    assert [ln.lane for ln in rm.lines] == [0, 1]
    lane = {n.sha: n.lane for n in rm.nodes}
    # a1/a2 ne sont plus une ligne vivante mais restent, portés par la base.
    assert lane["a1"] == 1
    assert lane["a2"] == 1
    # L'arête m1 -> a2 (second parent) est une jonction.
    merge_edges = {(e.child, e.parent) for e in rm.edges if e.kind == "merge"}
    assert ("m1", "a2") in merge_edges
    assert any(n.sha == "m1" and n.is_merge for n in rm.nodes)


def test_sans_version_aucune_gare() -> None:
    rm = build_railmap(_base_graph(), _base_branches(), [], [])
    assert all(n.kind != "version" for n in rm.nodes)


def test_commits_non_publies_distingues() -> None:
    rm = build_railmap(_base_graph(), _base_branches(), [], ["d1"])
    kinds = {n.sha: n.kind for n in rm.nodes}
    assert kinds["d1"] == "unpublished"
    assert kinds["a2"] == "commit"


def test_cinq_mille_commits_reste_rapide() -> None:
    # Chaîne linéaire de 5 000 commits : le calcul des voies est O(n) et doit
    # rester largement sous 2 s ; la fenêtre ne renvoie que le haut de l'histoire.
    commits = [_c("c0", (), 0)]
    for i in range(1, 5000):
        commits.append(_c(f"c{i}", (f"c{i - 1}",), i))
    branches = [BranchInput("origin/main", "main", "", "root", "c4999")]
    start = time.perf_counter()
    rm = build_railmap(commits, branches, [], [], offset=0, limit=200)
    assert time.perf_counter() - start < 2.0
    assert rm.total == 5000
    assert len(rm.nodes) == 200
    assert rm.has_more is True
    assert rm.nodes[0].sha == "c4999"  # le plus récent en premier


def test_fenetrage_et_has_more() -> None:
    rm = build_railmap(_base_graph(), _base_branches(), [], [], offset=0, limit=3)
    assert len(rm.nodes) == 3
    assert rm.total == 7
    assert rm.has_more is True
    # Une arête vers un parent hors fenêtre a to_row None (la ligne continue).
    assert any(e.to_row is None for e in rm.edges)
