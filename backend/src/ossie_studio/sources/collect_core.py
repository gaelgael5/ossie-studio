from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

import yaml

from ossie_studio.parsers.base import Parser, ParserError

TECH_MANIFEST_VERSION = 1
RAW_DIR = "raw"
TECH_MANIFEST_NAME = "manifest.yaml"


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class TechEntry:
    checksum: str
    destinations: list[str]
    branche: str
    parseur: str


@dataclass
class TechManifest:
    """Le manifeste technique de `raw/` : versions des parseurs + une entrée par
    fichier original lu."""

    parseurs: dict[str, str] = field(default_factory=dict)
    entries: dict[str, TechEntry] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "version": TECH_MANIFEST_VERSION,
            "parseurs": dict(sorted(self.parseurs.items())),
            "entrees": [
                {
                    "source": source,
                    "checksum": e.checksum,
                    "destination": list(e.destinations),
                    "branche": e.branche,
                    "parseur": e.parseur,
                }
                for source, e in sorted(self.entries.items())
            ],
        }


def parse_tech_manifest(text: str) -> TechManifest:
    data = yaml.safe_load(text) or {}
    parseurs = dict(data.get("parseurs") or {})
    entries: dict[str, TechEntry] = {}
    for row in data.get("entrees") or []:
        entries[str(row["source"])] = TechEntry(
            checksum=str(row.get("checksum", "")),
            destinations=[str(d) for d in (row.get("destination") or [])],
            branche=str(row.get("branche", "")),
            parseur=str(row.get("parseur", "")),
        )
    return TechManifest(parseurs=parseurs, entries=entries)


def dump_tech_manifest(manifest: TechManifest) -> str:
    return yaml.safe_dump(
        manifest.to_mapping(), sort_keys=False, allow_unicode=True, default_flow_style=False
    )


@dataclass
class CollectError:
    source: str
    cause: str


@dataclass
class CollectionPlan:
    """Résultat d'une collecte, prêt à écrire : fichiers schémas à (ré)écrire,
    fichiers à supprimer, nouveau manifeste technique, compteurs et erreurs."""

    writes: dict[str, str]
    deletes: list[str]
    manifest: TechManifest
    added: int = 0
    modified: int = 0
    deleted: int = 0
    unchanged: int = 0
    retained: int = 0
    read: int = 0
    errors: list[CollectError] = field(default_factory=list)


def _destination_name(key: str) -> str:
    return f"{key}.json"


def run_collection(
    source_files: dict[str, bytes],
    parsers: tuple[Parser, ...],
    previous: TechManifest,
    existing_schemas: dict[str, str],
    *,
    branche: str = "",
    force: bool = False,
) -> CollectionPlan:
    """Cœur pur de la collecte. `source_files` : fichiers originaux présents
    (chemin → contenu) ; `existing_schemas` : schémas déjà dans `raw/`
    (chemin relatif → contenu). Ne touche à aucun disque : rend un plan."""
    versions = {p.id: p.version for p in parsers}
    version_changed = {pid for pid, ver in versions.items() if previous.parseurs.get(pid) != ver}

    # Fichiers pris en charge et leur parseur (le premier qui les reconnaît).
    handled: dict[str, Parser] = {}
    for path in source_files:
        for parser in parsers:
            if parser.handles(path):
                handled[path] = parser
                break

    checks = {path: checksum(source_files[path]) for path in handled}

    def is_dirty(path: str) -> bool:
        if force:
            return True
        old = previous.entries.get(path)
        if old is None or old.checksum != checks[path]:
            return True
        return old.parseur in version_changed

    dirty = {path for path in handled if is_dirty(path)}
    deleted_sources = [s for s in previous.entries if s not in handled]

    # Groupes de régénération : par destination connue, l'ensemble des sources
    # qui la composent ; plus les nouvelles sources, parsées seules.
    dest_to_sources: dict[str, set[str]] = {}
    for source, entry in previous.entries.items():
        for dest in entry.destinations:
            dest_to_sources.setdefault(dest, set()).add(source)

    groups: list[set[str]] = []
    seen: set[str] = set()
    for source in sorted(dirty):
        if source in seen:
            continue
        old = previous.entries.get(source)
        group: set[str] = {source}
        if old is not None:
            for dest in old.destinations:
                group |= {s for s in dest_to_sources.get(dest, set()) if s in handled}
        groups.append(group)
        seen |= group

    plan = CollectionPlan(writes={}, deletes=[], manifest=TechManifest(parseurs=versions))
    plan.retained = len(handled)
    # On repart des entrées inchangées (sources non régénérées, toujours présentes).
    regenerated: set[str] = set(s for g in groups for s in g)
    plan.read = len(regenerated)
    for source, entry in previous.entries.items():
        if source in handled and source not in regenerated:
            plan.manifest.entries[source] = entry
            plan.unchanged += 1

    old_dest_content = dict(existing_schemas)
    produced_dest: set[str] = set()

    for group in groups:
        parser = handled[next(iter(group))]
        files = {s: source_files[s] for s in sorted(group)}
        try:
            objects = parser.parse(files)
        except ParserError as exc:
            for source in sorted(group):
                plan.errors.append(CollectError(source=source, cause=exc.code))
            continue
        by_source: dict[str, list[str]] = {s: [] for s in group}
        for obj in objects:
            dest = _destination_name(obj.key)
            produced_dest.add(dest)
            plan.writes[dest] = obj.schema
            for s in obj.sources:
                by_source.setdefault(s, []).append(dest)
        for source in sorted(group):
            plan.manifest.entries[source] = TechEntry(
                checksum=checks[source],
                destinations=sorted(set(by_source.get(source, []))),
                branche=branche,
                parseur=parser.id,
            )

    # Comptage ajouts / modifications sur les destinations produites.
    for dest, content in plan.writes.items():
        before = old_dest_content.get(dest)
        if before is None:
            plan.added += 1
        elif before != content:
            plan.modified += 1
        else:
            # Régénéré à l'identique (sortie déterministe) : rien à signaler.
            pass

    # Destinations des sources disparues : supprimées si plus aucune source
    # présente ne les produit.
    dead_dests: set[str] = set()
    for source in deleted_sources:
        for dest in previous.entries[source].destinations:
            dead_dests.add(dest)
    for dest in sorted(dead_dests):
        still_used = any(
            dest in e.destinations for s, e in plan.manifest.entries.items()
        )
        if not still_used and dest in existing_schemas:
            plan.deletes.append(dest)
            plan.deleted += 1

    return plan
