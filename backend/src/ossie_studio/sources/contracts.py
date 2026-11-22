"""Contrats OpenAPI déposés dans une source : rangement et index.

Un contrat déposé **ne peut pas se re-télécharger** : il est donc versionné dans
l'espace, sous `sources/<slug>/contrats/`, et non sur le volume des sources. Les
versions coexistent (`v1.yaml`, `v2.yaml`). Le fichier est rangé tel qu'il a été
déposé (le JSON étant du YAML valide, un contenu JSON est rangé sous `.yaml`
sans retouche) ; l'index `contrats.yaml` garde les faits de dépôt que ni le
fichier ni Git ne portent (nom d'origine, analyse figée, date, auteur).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ossie_studio.parsers.openapi import OPENAPI_PARSER_ID
from ossie_studio.sources.collect_core import (
    RAW_DIR,
    checksum,
    dump_tech_manifest,
    parse_tech_manifest,
    schema_index_name,
)
from ossie_studio.sources.paths import CONTRATS_INDEX, safe_contrats_path, safe_source_path

# Version 2 : clés en anglais. Une version 1 (clés françaises) se relit grâce au
# repli de `_field` ; on réécrit toujours en version 2.
CONTRATS_INDEX_VERSION = 2
# Clés de la version 1 (françaises), relues en repli pour qu'un dépôt existant
# reste lisible sans migration.
_LEGACY_ENTRY_KEYS = {
    "file": "fichier",
    "original_file": "fichier_origine",
    "declared_version": "version_declaree",
    "schema_count": "nb_schemas",
    "operation_count": "nb_operations",
    "uploaded_at": "depose_le",
    "author": "auteur",
}
# Les contrats sont transformés par le plugin openapi : son index technique.
_OPENAPI_INDEX = schema_index_name(OPENAPI_PARSER_ID)


class ContractExistsError(Exception):
    """La version existe déjà et le remplacement n'a pas été confirmé."""

    def __init__(self, version: str) -> None:
        super().__init__(version)
        self.version = version


class ContractNotFoundError(Exception):
    """Aucun contrat de cette version dans la source."""

    def __init__(self, version: str) -> None:
        super().__init__(version)
        self.version = version


@dataclass(frozen=True)
class ContractEntry:
    version: str
    file: str
    original_file: str
    openapi: str
    declared_version: str
    schema_count: int
    operation_count: int
    uploaded_at: str
    author: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "file": self.file,
            "original_file": self.original_file,
            "openapi": self.openapi,
            "declared_version": self.declared_version,
            "schema_count": self.schema_count,
            "operation_count": self.operation_count,
            "uploaded_at": self.uploaded_at,
            "author": self.author,
        }


@dataclass(frozen=True)
class ContractView:
    """Une entrée d'index enrichie de son état de collecte, pour l'affichage."""

    entry: ContractEntry
    collected: bool


def _field(row: dict[str, Any], key: str, default: Any) -> Any:
    """Valeur d'une clé d'entrée, avec repli sur son ancien nom français."""
    if key in row:
        return row[key]
    return row.get(_LEGACY_ENTRY_KEYS.get(key, key), default)


def _entry_from_row(row: dict[str, Any]) -> ContractEntry:
    return ContractEntry(
        version=str(row.get("version", "")),
        file=str(_field(row, "file", "")),
        original_file=str(_field(row, "original_file", "")),
        openapi=str(row.get("openapi", "")),
        declared_version=str(_field(row, "declared_version", "")),
        schema_count=int(_field(row, "schema_count", 0) or 0),
        operation_count=int(_field(row, "operation_count", 0) or 0),
        uploaded_at=str(_field(row, "uploaded_at", "")),
        author=str(_field(row, "author", "")),
    )


def parse_index(text: str) -> list[ContractEntry]:
    data = yaml.safe_load(text) or {}
    # `contrats` : liste de la version 1, relue en repli.
    rows = data.get("contracts") or data.get("contrats") or []
    return [_entry_from_row(row) for row in rows if isinstance(row, dict)]


def dump_index(entries: list[ContractEntry]) -> str:
    mapping = {
        "version": CONTRATS_INDEX_VERSION,
        "contracts": [e.to_mapping() for e in sorted(entries, key=lambda e: e.version)],
    }
    return yaml.safe_dump(mapping, sort_keys=False, allow_unicode=True, default_flow_style=False)


def _read_index_dir(contrats_dir: Path) -> list[ContractEntry]:
    index_file = contrats_dir / CONTRATS_INDEX
    if not index_file.is_file():
        return []
    return parse_index(index_file.read_text(encoding="utf-8"))


def read_contracts_view(workdir: Path, slug: str) -> list[ContractView]:
    """Liste des contrats d'une source, avec leur état de collecte (comparaison du
    checksum du fichier au manifeste technique de `raw/`). Lecture seule."""
    try:
        contrats_dir = safe_contrats_path(workdir, slug)
    except ValueError:
        return []
    entries = _read_index_dir(contrats_dir)
    tech_file = safe_source_path(workdir, slug) / RAW_DIR / _OPENAPI_INDEX
    tech = (
        parse_tech_manifest(tech_file.read_text(encoding="utf-8")) if tech_file.is_file() else None
    )
    views: list[ContractView] = []
    for entry in entries:
        collected = False
        contract_file = contrats_dir / entry.file
        if tech is not None and contract_file.is_file():
            te = tech.entries.get(entry.file)
            collected = te is not None and te.checksum == checksum(contract_file.read_bytes())
        views.append(ContractView(entry, collected))
    return views


def read_contract_bytes(workdir: Path, slug: str, version: str) -> bytes | None:
    """Octets du contrat rangé pour cette version, ou None s'il n'existe pas."""
    try:
        contrats_dir = safe_contrats_path(workdir, slug)
    except ValueError:
        return None
    for entry in _read_index_dir(contrats_dir):
        if entry.version == version:
            path = contrats_dir / entry.file
            return path.read_bytes() if path.is_file() else None
    return None


def deposit_contract_fn(
    slug: str, entry: ContractEntry, content: bytes, *, replace: bool
) -> Callable[[Path], None]:
    """Écrit le contrat déposé (`contrats/<version>.yaml`) et met à jour l'index,
    en un commit. Lève ContractExistsError si la version existe déjà et que le
    remplacement n'a pas été confirmé."""

    def write(workdir: Path) -> None:
        contrats_dir = safe_contrats_path(workdir, slug)
        entries = _read_index_dir(contrats_dir)
        exists = any(e.version == entry.version for e in entries)
        if exists and not replace:
            raise ContractExistsError(entry.version)
        contrats_dir.mkdir(parents=True, exist_ok=True)
        (contrats_dir / entry.file).write_bytes(content)
        kept = [e for e in entries if e.version != entry.version]
        kept.append(entry)
        (contrats_dir / CONTRATS_INDEX).write_text(dump_index(kept), encoding="utf-8")

    return write


def remove_contract_fn(slug: str, version: str) -> Callable[[Path], None]:
    """Retire un contrat : son fichier, son entrée d'index **et ses schémas
    générés** dans `raw/` (décision : les schémas partent avec le contrat), en un
    commit. Lève ContractNotFoundError si la version est absente."""

    def write(workdir: Path) -> None:
        contrats_dir = safe_contrats_path(workdir, slug)
        entries = _read_index_dir(contrats_dir)
        target = next((e for e in entries if e.version == version), None)
        if target is None:
            raise ContractNotFoundError(version)
        (contrats_dir / target.file).unlink(missing_ok=True)
        kept = [e for e in entries if e.version != version]
        (contrats_dir / CONTRATS_INDEX).write_text(dump_index(kept), encoding="utf-8")
        _drop_schemas(workdir, slug, target.file)

    return write


def _drop_schemas(workdir: Path, slug: str, contract_file: str) -> None:
    """Supprime de `raw/` les schémas tirés de ce contrat et met à jour le
    manifeste technique. Un schéma n'est supprimé que si plus aucune autre source
    ne le produit (il est version-scopé, donc propre au contrat)."""
    raw = safe_source_path(workdir, slug) / RAW_DIR
    tech_file = raw / _OPENAPI_INDEX
    if not tech_file.is_file():
        return
    tech = parse_tech_manifest(tech_file.read_text(encoding="utf-8"))
    entry = tech.entries.pop(contract_file, None)
    if entry is None:
        return
    for dest in entry.destinations:
        still_used = any(dest in e.destinations for e in tech.entries.values())
        if not still_used:
            (raw / dest).unlink(missing_ok=True)
    tech_file.write_text(dump_tech_manifest(tech), encoding="utf-8")
