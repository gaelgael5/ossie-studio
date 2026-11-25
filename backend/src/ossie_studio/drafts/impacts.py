"""Impacts d'un brouillon : les objets **absents du brouillon** qui lisent un
objet modifié, créé ou supprimé par le brouillon.

Décision (feature « Dépendances et section Impacts ») : un objet qui change
peut casser ceux qui le lisent, et ça ne se corrige pas tout seul. Chaque
impact se corrige (l'objet entre dans le brouillon) ou se marque « vérifié » ;
la publication est bloquée tant qu'il en reste. Les règles de dépendance sont
une **liste extensible** : la couche ontologique en ajoutera."""

from __future__ import annotations

import hashlib
import posixpath
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ossie_studio.drafts.store import DraftObject
from ossie_studio.parsers.conventions import SCHEMA_SUFFIX, load_schema

REF_KEY = "$ref"


class DependencyRule(Protocol):
    name: str

    def dependents(self, view: Path, changed: set[str]) -> dict[str, set[str]]:
        """Objet dépendant → objets qu'il lit et qui ont changé."""
        ...


def _source_json(view: Path, folder: str) -> Iterator[Path]:
    yield from (view / "sources").glob(f"*/{folder}/*{SCHEMA_SUFFIX}")


class RawAssetRule:
    """Un asset curé (`sources/<s>/assets/X.schema.yaml`) dépend du raw de même
    nom (`sources/<s>/raw/X.schema.yaml`) : même nom de fichier et même `$id`
    (sources/curation.py)."""

    name = "raw_asset"

    def dependents(self, view: Path, changed: set[str]) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for path in changed:
            parts = path.split("/")
            if len(parts) == 4 and parts[0] == "sources" and parts[2] == "raw":
                asset = f"sources/{parts[1]}/assets/{parts[3]}"
                if (view / asset).is_file():
                    out.setdefault(asset, set()).add(path)
        return out


def refs_in(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        ref = node.get(REF_KEY)
        if isinstance(ref, str):
            yield ref
        for value in node.values():
            yield from refs_in(value)
    elif isinstance(node, list):
        for item in node:
            yield from refs_in(item)


class RefRule:
    """Un schéma qui contient un `$ref` vers un autre dépend de la cible. Les
    `$ref` posés par le socle désignent un fichier relatif au répertoire du
    schéma (sources/schema_resolve.py). Index inverse en un seul parcours."""

    name = "ref"

    def dependents(self, view: Path, changed: set[str]) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for file in [*_source_json(view, "raw"), *_source_json(view, "assets")]:
            rel = file.relative_to(view).as_posix()
            try:
                data = load_schema(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for ref in refs_in(data):
                if ref.startswith("#") or "://" in ref:
                    continue
                target = posixpath.normpath(
                    posixpath.join(posixpath.dirname(rel), ref.split("#")[0])
                )
                if target in changed:
                    out.setdefault(rel, set()).add(target)
        return out


# Liste extensible : un domaine ajoute ses règles en s'enregistrant (la
# gouvernance : governance/rules.py ; plus tard la couche ontologique).
DEPENDENCY_RULES: list[DependencyRule] = [RawAssetRule(), RefRule()]


@dataclass
class Impact:
    path: str
    reads: list[str]
    rules: list[str] = field(default_factory=list)
    verified: bool = False

    def payload(self) -> dict[str, object]:
        return {
            "path": self.path,
            "reads": self.reads,
            "rules": self.rules,
            "verified": self.verified,
        }


def fingerprint(reads: list[str], objects: list[DraftObject]) -> str:
    """Empreinte de l'état des objets lus : un « vérifié » ne vaut que tant
    qu'ils ne changent plus."""
    by_path = {o.path: o.content for o in objects}
    digest = hashlib.sha256()
    for path in sorted(reads):
        content = by_path.get(path)
        digest.update(path.encode())
        digest.update(hashlib.sha256(content).digest() if content is not None else b"<supprime>")
    return digest.hexdigest()


def compute_impacts(
    view: Path,
    objects: list[DraftObject],
    verified: dict[str, str],
    rules: list[DependencyRule] | None = None,
) -> list[Impact]:
    """Impacts d'un brouillon dans sa vue fusionnée. `verified` : décisions
    « vérifié » (chemin → empreinte au moment de la décision)."""
    changed = {o.path for o in objects}
    found: dict[str, Impact] = {}
    for rule in rules if rules is not None else DEPENDENCY_RULES:
        for dependent, reads in rule.dependents(view, changed).items():
            if dependent in changed:
                continue  # déjà dans le brouillon : corrigé ou en cours
            impact = found.setdefault(dependent, Impact(dependent, []))
            impact.reads = sorted(set(impact.reads) | reads)
            if rule.name not in impact.rules:
                impact.rules.append(rule.name)
    for impact in found.values():
        impact.verified = verified.get(impact.path) == fingerprint(impact.reads, objects)
    return sorted(found.values(), key=lambda i: i.path)
