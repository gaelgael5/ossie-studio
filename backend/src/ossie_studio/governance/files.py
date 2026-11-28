"""Les fichiers de `governance/`, lus sur le disque ou dans l'index des documents
(ticket bb279dd6, « Recherche par l'index (5/6) »).

Les lecteurs de la gouvernance (tags, classifications, certifications, règles, profils
de relation, état des règles fournies) lisent par cet accès : une seule façon de lire,
nourrie par les fichiers de la copie (`DiskFiles`) ou par le texte que l'index garde de
chaque fichier de gouvernance (`IndexFiles`, des fichiers de configuration courts).
"""

from __future__ import annotations

import functools
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

GOVERNANCE_PREFIX = "governance/"


class GovernanceFiles(Protocol):
    def yaml_files(self, folder: str, *, recursive: bool) -> list[str]:
        """Les fichiers `*.yaml` de `folder` (chemins depuis la racine), triés comme
        `sorted(Path.glob(...))`."""
        ...

    def is_file(self, rel: str) -> bool: ...

    def read_text(self, rel: str) -> str:
        """Le texte du fichier ; lève OSError s'il manque, UnicodeDecodeError s'il
        n'est pas en UTF-8, comme `Path.read_text`."""
        ...


class DiskFiles:
    """Les fichiers de gouvernance d'une copie de travail."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def yaml_files(self, folder: str, *, recursive: bool) -> list[str]:
        base = self.root / folder
        if not base.is_dir():
            return []
        found = base.rglob("*.yaml") if recursive else base.glob("*.yaml")
        return [p.relative_to(self.root).as_posix() for p in sorted(found)]

    def is_file(self, rel: str) -> bool:
        return (self.root / rel).is_file()

    def read_text(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")


@functools.cache
def _kept() -> tuple[tuple[str, ...], frozenset[str]]:
    """Ce que les listes lisent : les dossiers des objets et des règles, et l'état des
    règles fournies. Le reste de `governance/` (les retraits de classification, qui
    grossissent avec l'usage) n'est pas gardé."""
    from ossie_studio.governance import classification_rules, relation_profiles, repo, tag_rules

    folders = (
        repo.TAGS_DIR,
        repo.CERTIFICATIONS_DIR,
        repo.CLASSIFICATIONS_DIR,
        tag_rules.TAG_RULES_DIR,
        classification_rules.CLASSIFICATION_RULES_DIR,
        relation_profiles.RELATION_PROFILES_DIR,
    )
    states = frozenset({tag_rules.BUILTIN_STATE_FILE, classification_rules.BUILTIN_STATE_FILE})
    return tuple(f"{folder}/" for folder in folders), states


def file_facts(path: str, data: bytes) -> dict[str, Any] | None:
    """Le texte d'un fichier de gouvernance que les listes lisent (None s'il n'est pas en
    UTF-8) ; None pour les autres fichiers."""
    if not path.startswith(GOVERNANCE_PREFIX):
        return None
    folders, states = _kept()
    if path not in states and not (path.endswith(".yaml") and path.startswith(folders)):
        return None
    try:
        return {"governance_text": data.decode("utf-8")}
    except UnicodeDecodeError:
        return {"governance_text": None}


Files = Mapping[str, tuple[str, dict[str, Any] | None]]


def _path_order(rel: str) -> tuple[str, ...]:
    return tuple(rel.split("/"))


class IndexFiles:
    """Les fichiers de gouvernance d'une vue de l'index des documents."""

    def __init__(self, files: Files) -> None:
        self._files = {p: f for p, f in files.items() if p.startswith(GOVERNANCE_PREFIX)}

    def yaml_files(self, folder: str, *, recursive: bool) -> list[str]:
        prefix = f"{folder}/"
        out = []
        for rel in self._files:
            if not rel.startswith(prefix) or not rel.endswith(".yaml"):
                continue
            if not recursive and "/" in rel[len(prefix) :]:
                continue
            out.append(rel)
        return sorted(out, key=_path_order)

    def is_file(self, rel: str) -> bool:
        return rel in self._files

    def read_text(self, rel: str) -> str:
        found = self._files.get(rel)
        if found is None:
            raise FileNotFoundError(rel)
        facts = found[1] or {}
        if "governance_text" not in facts:
            raise LookupError(f"{rel} : texte non gardé par l'index (`file_facts`)")
        text = facts["governance_text"]
        if not isinstance(text, str):
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "invalid start byte")
        return text
