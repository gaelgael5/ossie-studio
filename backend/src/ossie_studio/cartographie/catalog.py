"""Le catalogue des types ArchiMate 3.2, livré avec l'application (pas dans
l'espace Git) : `archimate-3.2.yaml`, à côté de ce module.

Un type de l'espace repose sur un type de ce catalogue (`x-archimate`) ; les
attributs propres à ce type ArchiMate (sens d'un accès, force d'une influence…)
font partie de la base de tout type qui repose dessus. Les valeurs stockées sont
celles d'ArchiMate (`Write`), l'écran affiche leur libellé français
(« écriture »).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import yaml

CATALOG_FILE = Path(__file__).with_name("archimate-3.2.yaml")
ELEMENT = "element"
RELATION = "relation"


@dataclass(frozen=True)
class Attribute:
    """Un attribut propre à un type ArchiMate."""

    name: str
    label: str
    type: str = "string"  # string | boolean
    values: dict[str, str] = field(default_factory=dict)  # valeur ArchiMate → libellé

    def schema(self) -> dict[str, Any]:
        """Sa forme JSON Schema (valeurs fermées : énumération des valeurs ArchiMate)."""
        out: dict[str, Any] = {"type": self.type, "title": self.label}
        if self.values:
            out["enum"] = list(self.values)
        return out


@dataclass(frozen=True)
class ArchimateType:
    name: str
    kind: str  # ELEMENT | RELATION
    label: str
    layer: str = ""
    attributes: tuple[Attribute, ...] = ()

    @property
    def is_relation(self) -> bool:
        return self.kind == RELATION


@dataclass(frozen=True)
class Catalog:
    version: int
    archimate: str
    types: dict[str, ArchimateType]

    def get(self, name: str) -> ArchimateType | None:
        return self.types.get(name)


def _attribute(name: str, data: dict[str, Any]) -> Attribute:
    values = data.get("values") or {}
    return Attribute(
        name=name,
        label=str(data.get("label") or name),
        type=str(data.get("type") or "string"),
        values={str(k): str(v) for k, v in values.items()},
    )


def parse_catalog(text: str) -> Catalog:
    data = yaml.safe_load(text) or {}
    types: dict[str, ArchimateType] = {}
    for name, raw in (data.get("types") or {}).items():
        attributes = tuple(
            _attribute(str(a), spec or {}) for a, spec in (raw.get("attributes") or {}).items()
        )
        types[str(name)] = ArchimateType(
            name=str(name),
            kind=str(raw["kind"]),
            label=str(raw.get("label") or name),
            layer=str(raw.get("layer") or ""),
            attributes=attributes,
        )
    return Catalog(
        version=int(data.get("version") or 1),
        archimate=str(data.get("archimate") or ""),
        types=types,
    )


@cache
def catalog() -> Catalog:
    """Le catalogue livré avec l'application (lu une fois)."""
    return parse_catalog(CATALOG_FILE.read_text(encoding="utf-8"))
