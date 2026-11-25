"""Dates et auteurs d'un objet de cartographie : tirés de l'historique Git de la
branche, jamais stockés dans le fichier (enabler f8167ae1).

`file_dates` part des commits publiés qui touchent `cartographie/` (du plus récent
au plus ancien, avec leurs fichiers : `WorkdirManager.path_log`). Un fichier
absent de cet historique n'existe que dans le brouillon : « non publié ».
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ossie_studio.spaces.workdir import PathCommit


@dataclass(frozen=True)
class FileDates:
    created_at: str
    created_by: str
    updated_at: str
    updated_by: str


def file_dates(commits: Iterable[PathCommit]) -> dict[str, FileDates]:
    """Par fichier encore présent : création (commit le plus ancien qui le touche)
    et dernière modification (le plus récent). Un fichier supprimé n'y est plus."""
    latest: dict[str, PathCommit] = {}
    oldest: dict[str, PathCommit] = {}
    gone: set[str] = set()
    for commit in commits:  # du plus récent au plus ancien
        for status, path in commit.files:
            if path not in latest:
                latest[path] = commit
                if status == "D":
                    gone.add(path)
            oldest[path] = commit
    return {
        path: FileDates(
            created_at=oldest[path].date,
            created_by=oldest[path].author,
            updated_at=commit.date,
            updated_by=commit.author,
        )
        for path, commit in latest.items()
        if path not in gone
    }
