"""Export et import du vocabulaire de tags gouvernés en YAML, au format repris
d'Entropy Data (un tag = id + info{owner, description} + links + properties)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from ossie_studio.governance.classifications import (
    derive_scheme_id,
    validate_level,
    validate_scheme,
)
from ossie_studio.governance.tags import normalize_tag_id, validate_tag_fields
from ossie_studio.models import ClassificationLevel, ClassificationScheme, GovernedTag


class TagImportError(Exception):
    """Le document YAML n'est pas un vocabulaire de tags valide."""


class SchemeImportError(Exception):
    """Le document YAML n'est pas un schéma de classification valide."""


@dataclass(frozen=True)
class ParsedTag:
    id: str
    owner: str
    description: str
    links: dict[str, str] = field(default_factory=dict)
    properties: dict[str, object] = field(default_factory=dict)


def _tag_to_doc(tag: GovernedTag) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "id": tag.id,
        "info": {"owner": tag.owner, "description": tag.description},
    }
    if tag.links:
        doc["links"] = dict(tag.links)
    if tag.properties:
        doc["properties"] = dict(tag.properties)
    return doc


def export_tags_yaml(tags: list[GovernedTag]) -> str:
    docs = [_tag_to_doc(t) for t in tags]
    return yaml.safe_dump(docs, sort_keys=False, allow_unicode=True, default_flow_style=False)


def parse_tags_yaml(text: str) -> list[ParsedTag]:
    """Analyse un vocabulaire YAML (liste de tags, ou un tag unique). Lève
    TagImportError au premier élément invalide — l'import est tout ou rien."""
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise TagImportError(str(exc)) from exc
    if loaded is None:
        return []
    items = loaded if isinstance(loaded, list) else [loaded]
    parsed: list[ParsedTag] = []
    for item in items:
        if not isinstance(item, dict):
            raise TagImportError("chaque entrée doit être un objet")
        raw_id = item.get("id")
        if not isinstance(raw_id, str):
            raise TagImportError("id manquant")
        tag_id = normalize_tag_id(raw_id)
        info = item.get("info") or {}
        info = info if isinstance(info, dict) else {}
        owner = str(info.get("owner", "") or "")
        description = str(info.get("description", "") or "")
        links = item.get("links") or {}
        properties = item.get("properties") or {}
        errors = validate_tag_fields(tag_id, links, properties)
        if errors:
            raise TagImportError(f"{raw_id}: {', '.join(errors)}")
        parsed.append(
            ParsedTag(
                id=tag_id,
                owner=owner,
                description=description,
                links=dict(links),
                properties=dict(properties),
            )
        )
    return parsed


@dataclass(frozen=True)
class ParsedLevel:
    name: str
    label: str
    short_name: str
    rank: int
    description: str = ""
    criteria: str = ""
    color: str = ""
    icon: str = ""


@dataclass(frozen=True)
class ParsedScheme:
    id: str
    name: str
    description: str
    badge_prefix: str
    multi_value: bool
    is_primary: bool
    levels: list[ParsedLevel] = field(default_factory=list)


def export_scheme_yaml(scheme: ClassificationScheme, levels: list[ClassificationLevel]) -> str:
    doc: dict[str, Any] = {
        "id": scheme.id,
        "name": scheme.name,
        "description": scheme.description,
        "badge_prefix": scheme.badge_prefix,
        "multi_value": scheme.multi_value,
        "is_primary": scheme.is_primary,
        "levels": [
            {
                "name": lv.name,
                "label": lv.label,
                "short_name": lv.short_name,
                "rank": lv.rank,
                "description": lv.description,
                "criteria": lv.criteria,
                "color": lv.color,
                "icon": lv.icon,
            }
            for lv in levels
        ],
    }
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=False)


def parse_scheme_yaml(text: str) -> ParsedScheme:
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SchemeImportError(str(exc)) from exc
    if not isinstance(loaded, dict):
        raise SchemeImportError("un schéma doit être un objet")
    name = str(loaded.get("name", "") or "")
    multi_value = bool(loaded.get("multi_value", False))
    is_primary = bool(loaded.get("is_primary", False))
    scheme_errors = validate_scheme(name, multi_value, is_primary)
    if scheme_errors:
        raise SchemeImportError(", ".join(scheme_errors))
    raw_id = loaded.get("id")
    has_id = isinstance(raw_id, str) and raw_id.strip() != ""
    scheme_id = str(raw_id) if has_id else derive_scheme_id(name)
    levels: list[ParsedLevel] = []
    for item in loaded.get("levels") or []:
        if not isinstance(item, dict):
            raise SchemeImportError("chaque niveau doit être un objet")
        level_name = str(item.get("name", "") or "")
        short_name = str(item.get("short_name", "") or "")
        level_errors = validate_level(level_name, short_name)
        if level_errors:
            raise SchemeImportError(f"{level_name or '?'}: {', '.join(level_errors)}")
        rank_value = item.get("rank", 0)
        rank = rank_value if isinstance(rank_value, int) and not isinstance(rank_value, bool) else 0
        levels.append(
            ParsedLevel(
                name=level_name,
                label=str(item.get("label", level_name) or level_name),
                short_name=short_name,
                rank=rank,
                description=str(item.get("description", "") or ""),
                criteria=str(item.get("criteria", "") or ""),
                color=str(item.get("color", "") or ""),
                icon=str(item.get("icon", "") or ""),
            )
        )
    return ParsedScheme(
        id=scheme_id,
        name=name.strip(),
        description=str(loaded.get("description", "") or ""),
        badge_prefix=str(loaded.get("badge_prefix", "") or ""),
        multi_value=multi_value,
        is_primary=is_primary,
        levels=levels,
    )
