"""Vue « Modifications » : mon brouillon regroupé par source et par nature.

Ticket docflow aad1d3c6. Gael, 23/11/2026 : « une vue qui liste les
modifications est importante, mais plutôt à côté des infos de publication ».
Le brouillon EST l'écart entre ma vue et la branche publiée (après publication
il ne garde que ce qui diffère encore) : la vue le range au lieu d'aligner des
chemins de fichiers. S'y ajoutent les assets orphelins (objet raw absent de ma
vue), qu'ils soient dans mon brouillon ou déjà publiés : sans cela, une
disparition publiée ne se verrait nulle part, l'explorateur n'ayant plus de
vignette à montrer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ossie_studio.sources.collect_core import RAW_DIR
from ossie_studio.sources.explorer import list_source_slugs
from ossie_studio.sources.explorer_graph import ReferenceGraph
from ossie_studio.sources.paths import ASSETS_DIR, SOURCES_DIR, safe_assets_path


@dataclass(frozen=True)
class ChangedObject:
    file: str
    title: str
    state: str  # created | modified | deleted
    folder: str | None  # dossier dans l'explorateur (mode par défaut) ; None : plus d'objet


@dataclass(frozen=True)
class ChangedFile:
    path: str
    state: str


@dataclass
class SourceChanges:
    slug: str
    label: str
    raw: list[ChangedObject] = field(default_factory=list)
    assets: list[ChangedObject] = field(default_factory=list)
    orphans: list[ChangedObject] = field(default_factory=list)
    other: list[ChangedFile] = field(default_factory=list)


def asset_names(workdir: Path) -> dict[str, set[str]]:
    """Fichiers d'assets de chaque source, dans la copie lue (ma vue)."""
    out: dict[str, set[str]] = {}
    for slug in list_source_slugs(workdir):
        try:
            directory = safe_assets_path(workdir, slug)
        except ValueError:
            continue
        if directory.is_dir():
            out[slug] = {p.name for p in directory.iterdir() if p.suffix == ".json"}
    return out


def _direct_child(rest: str, directory: str) -> str | None:
    """Nom du fichier `.json` directement sous `directory/` dans `rest`, sinon None."""
    prefix = f"{directory}/"
    if not rest.startswith(prefix):
        return None
    name = rest[len(prefix) :]
    return name if name.endswith(".json") and "/" not in name else None


def group_changes(
    objects: list[tuple[str, str]],
    graph: ReferenceGraph,
    labels: dict[str, str],
    assets: dict[str, set[str]],
) -> tuple[list[SourceChanges], list[ChangedFile]]:
    """(changements par source, fichiers hors des sources). `objects` : (chemin,
    état) des objets du brouillon ; `assets` : fichiers d'assets de ma vue."""
    by_source: dict[str, SourceChanges] = {}

    def source(slug: str) -> SourceChanges:
        if slug not in by_source:
            by_source[slug] = SourceChanges(slug=slug, label=labels.get(slug) or slug)
        return by_source[slug]

    def changed(slug: str, name: str, state: str) -> ChangedObject:
        target = graph.target((slug, name)) if state != "deleted" else None
        return ChangedObject(
            file=name,
            title=target["title"] if target else name,
            state=state,
            folder=target["folder"] if target else None,
        )

    outside: list[ChangedFile] = []
    for path, state in objects:
        parts = path.split("/", 2)
        if len(parts) < 3 or parts[0] != SOURCES_DIR:
            outside.append(ChangedFile(path=path, state=state))
            continue
        slug, rest = parts[1], parts[2]
        if (name := _direct_child(rest, RAW_DIR)) is not None:
            source(slug).raw.append(changed(slug, name, state))
        elif (name := _direct_child(rest, ASSETS_DIR)) is not None:
            source(slug).assets.append(changed(slug, name, state))
        else:
            source(slug).other.append(ChangedFile(path=path, state=state))
    for slug, names in sorted(assets.items()):
        for name in sorted(names):
            if graph.located((slug, name)) is None:
                source(slug).orphans.append(
                    ChangedObject(file=name, title=name, state="orphan", folder=None)
                )
    return [by_source[s] for s in sorted(by_source)], outside
