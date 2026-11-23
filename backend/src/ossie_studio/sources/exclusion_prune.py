"""Exclure un répertoire retire tout de suite ses objets raw du brouillon.

Ticket docflow 64e7b42f. Gael, 23/11/2026 : les objets des répertoires exclus
« devraient être déduits » des modifications de mon brouillon, sans attendre la
collecte suivante — le brouillon est exactement l'écart avec la branche publiée
(ticket aad1d3c6). Réinclure ne restaure rien : « la collecte suivante » les
rajoute, on ne reconstruit pas un schéma sans relire le code.

La correspondance fichier d'origine → fichiers raw est lue dans les manifestes
techniques de `raw/` ; la règle d'exclusion est celle de la collecte
(`is_excluded`), pour que l'écran et la collecte suivante disent la même chose.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ossie_studio.parsers.markdown import DOCS_DIR, DOCS_INDEX, dump_docs_index, parse_docs_index
from ossie_studio.sources.collect import is_excluded
from ossie_studio.sources.collect_core import (
    RAW_DIR,
    TechManifest,
    dump_tech_manifest,
    parse_tech_manifest,
)
from ossie_studio.sources.paths import safe_source_path


def prune_excluded_raw(workdir: Path, slug: str, exclusions: list[str]) -> list[str]:
    """Retire de `raw/` ce qui ne vient que de fichiers exclus : schémas, leurs
    entrées de manifeste technique, documents Markdown et leur entrée d'index.
    Un schéma alimenté aussi par un fichier non exclu (classe partielle) reste.
    Rend les chemins retirés (relatifs à `raw/`). Idempotent."""
    raw_dir = safe_source_path(workdir, slug) / RAW_DIR
    if not raw_dir.is_dir():
        return []
    manifests: dict[Path, TechManifest] = {}
    excluded_dests: set[str] = set()
    kept_dests: set[str] = set()
    for index_file in sorted(raw_dir.glob("manifest*.yaml")):
        try:
            tech = parse_tech_manifest(index_file.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError, KeyError, TypeError, AttributeError):
            continue  # illisible : la collecte suivante le réécrira
        dropped = [src for src in tech.entries if is_excluded(src, exclusions)]
        for src, entry in tech.entries.items():
            (excluded_dests if src in dropped else kept_dests).update(entry.destinations)
        if dropped:
            for src in dropped:
                del tech.entries[src]
            manifests[index_file] = tech
    removed: list[str] = []
    for dest in sorted(excluded_dests - kept_dests):
        target = raw_dir / dest
        if target.is_file():
            target.unlink()
            removed.append(dest)
    for index_file, tech in manifests.items():
        index_file.write_text(dump_tech_manifest(tech), encoding="utf-8")
    removed.extend(_prune_docs(raw_dir, exclusions))
    return removed


def _prune_docs(raw_dir: Path, exclusions: list[str]) -> list[str]:
    index_file = raw_dir / DOCS_INDEX
    if not index_file.is_file():
        return []
    try:
        entries = parse_docs_index(index_file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    dropped = [path for path in entries if is_excluded(path, exclusions)]
    removed: list[str] = []
    for path in dropped:
        del entries[path]
        target = raw_dir / DOCS_DIR / path
        if target.is_file():
            target.unlink()
            removed.append(f"{DOCS_DIR}/{path}")
    if dropped:
        index_file.write_text(dump_docs_index(entries), encoding="utf-8")
    return removed
