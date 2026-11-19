from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import yaml

from ossie_studio.parsers.base import (
    STATUT_ERREUR,
    STATUT_INCHANGE,
    STATUT_TRAITE,
    OutputAction,
    Parser,
    ParserError,
    PluginContext,
    PluginError,
    PluginResult,
)
from ossie_studio.parsers.conventions import ConventionError, validate_schema

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
    statuses: dict[str, str] = field(default_factory=dict)


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

    # Destination connue par source (pour grouper les fichiers qui composent un
    # même objet, p. ex. des classes partielles). Pour une source déjà vue, on
    # relit le manifeste ; pour une source **nouvelle**, on la parse une fois pour
    # découvrir ses destinations, afin de grouper les parties dès la 1re collecte.
    dest_of: dict[str, set[str]] = {}
    for path in handled:
        old = previous.entries.get(path)
        if old is not None:
            dest_of[path] = set(old.destinations)
        elif path in dirty:
            try:
                produced = handled[path].parse({path: source_files[path]})
            except ParserError:
                produced = []
            dest_of[path] = {_destination_name(o.key) for o in produced}
        else:
            dest_of[path] = set()

    dest_to_sources: dict[str, set[str]] = {}
    for source, dests in dest_of.items():
        for dest in dests:
            dest_to_sources.setdefault(dest, set()).add(source)

    groups: list[set[str]] = []
    seen: set[str] = set()
    for source in sorted(dirty):
        if source in seen:
            continue
        group: set[str] = {source}
        for dest in dest_of.get(source, set()):
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
            plan.statuses[source] = STATUT_INCHANGE

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
                plan.statuses[source] = STATUT_ERREUR
            continue
        # Validateur partagé des conventions : un schéma non conforme fait échouer
        # la lecture du fichier concerné ; il n'est jamais écrit.
        try:
            for obj in objects:
                validate_schema(json.loads(obj.schema))
        except ConventionError as exc:
            for source in sorted(group):
                plan.errors.append(CollectError(source=source, cause=f"convention:{exc.rule}"))
                plan.statuses[source] = STATUT_ERREUR
            continue
        except json.JSONDecodeError:
            for source in sorted(group):
                plan.errors.append(CollectError(source=source, cause="convention:not_json"))
                plan.statuses[source] = STATUT_ERREUR
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
            plan.statuses[source] = STATUT_TRAITE

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


def schema_index_name(parser_id: str) -> str:
    """Nom du fichier d'index **propre à un plugin** dans `raw/`. Namespacé par
    l'identifiant du plugin : deux parseurs de schémas sur une même source (C# et
    un autre) ne se marchent jamais dessus."""
    return f"manifest-{parser_id}.yaml"


def schema_plugin_run(
    context: PluginContext, parser: Parser, *, index_name: str = ""
) -> PluginResult:
    """Adapte le moteur incrémental de schémas au contrat de plugin : lit l'index
    du plugin et ses sorties `.json` dans `raw/`, applique `run_collection`, et
    rend des actions d'écriture (dont l'index régénéré). Les plugins de schémas
    (démo, OpenAPI, C#…) délèguent ici ; chacun garde **son** fichier d'index."""
    index_name = index_name or schema_index_name(parser.id)
    prev_text = context.raw.read_index(index_name)
    previous = parse_tech_manifest(prev_text) if prev_text else TechManifest()
    existing = {
        path: content
        for path, content in context.raw.list_outputs().items()
        if path.endswith(".json") and "/" not in path
    }
    plan = run_collection(
        context.contents,
        (parser,),
        previous,
        existing,
        branche=context.branche,
        force=context.force,
    )
    writes = [OutputAction(path=dest, content=content) for dest, content in plan.writes.items()]
    writes.append(OutputAction(path=index_name, content=dump_tech_manifest(plan.manifest)))
    return PluginResult(
        writes=writes,
        deletes=list(plan.deletes),
        statuses=dict(plan.statuses),
        errors=[PluginError(source=e.source, cause=e.cause) for e in plan.errors],
        added=plan.added,
        modified=plan.modified,
        deleted=plan.deleted,
        unchanged=plan.unchanged,
        read=plan.read,
        retained=plan.retained,
    )
