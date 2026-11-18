"""Export et import du vocabulaire de tags gouvernés en YAML, au format repris
d'Entropy Data (un tag = id + info{owner, description} + links + properties)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from ossie_studio.governance.tags import normalize_tag_id, validate_tag_fields
from ossie_studio.models import GovernedTag


class TagImportError(Exception):
    """Le document YAML n'est pas un vocabulaire de tags valide."""


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
