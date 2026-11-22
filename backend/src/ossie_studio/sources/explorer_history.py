"""Explorateur raw : historique lisible d'un schéma (vue exclusive).

Chaque commit qui a touché le fichier schéma est comparé à la version
précédente, et la différence est dite en clair : champ ajouté, retiré, type
changé (« type de Total : decimal → Money »), obligation changée, description
changée. Un commit qui ne change rien de lisible (réordonnancement…) est omis.
Les changements sont des codes stables, traduits par l'interface.
"""

from __future__ import annotations

import json
from typing import Any


def _load(text: str | None) -> dict[str, Any] | None:
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def type_text(prop: dict[str, Any]) -> str:
    """Type d'un champ en une courte chaîne, sans résoudre les références (un
    historique compare des versions, pas l'espace d'aujourd'hui)."""
    if "$ref" in prop:
        return str(prop["$ref"]).rsplit("/", 1)[-1].removesuffix(".json")
    if "x-unresolved-type" in prop:
        return str(prop["x-unresolved-type"])
    items, extra = prop.get("items"), prop.get("additionalProperties")
    if prop.get("type") == "array" and isinstance(items, dict):
        return f"{type_text(items)}[]"
    if prop.get("type") == "object" and isinstance(extra, dict):
        return f"map<{type_text(extra)}>"
    kind = prop.get("type")
    text = "/".join(map(str, kind)) if isinstance(kind, list) else str(kind or "any")
    return f"{text} ({prop['format']})" if prop.get("format") else text


def _props(schema: dict[str, Any]) -> dict[str, Any]:
    value = schema.get("properties")
    return value if isinstance(value, dict) else {}


def schema_changes(before_text: str | None, after_text: str | None) -> list[dict[str, Any]]:
    before, after = _load(before_text), _load(after_text)
    if before is None and after is None:
        return []
    if before is None:
        return [{"code": "created"}]
    if after is None:
        return [{"code": "deleted"}]
    changes: list[dict[str, Any]] = []
    if (before.get("description") or "") != (after.get("description") or ""):
        changes.append({"code": "description_changed"})
    old_props = _props(before)
    new_props = _props(after)
    old_req, new_req = set(before.get("required") or []), set(after.get("required") or [])
    for name in new_props:
        if name not in old_props:
            changes.append({"code": "field_added", "field": name})
    for name in old_props:
        if name not in new_props:
            changes.append({"code": "field_removed", "field": name})
    for name, prop in new_props.items():
        old = old_props.get(name)
        if not isinstance(old, dict) or not isinstance(prop, dict):
            continue
        if type_text(old) != type_text(prop):
            changes.append(
                {
                    "code": "field_type",
                    "field": name,
                    "before": type_text(old),
                    "after": type_text(prop),
                }
            )
        if (name in old_req) != (name in new_req):
            changes.append({"code": "field_required", "field": name, "required": name in new_req})
        if (old.get("description") or "") != (prop.get("description") or ""):
            changes.append({"code": "field_description", "field": name})
    return changes
