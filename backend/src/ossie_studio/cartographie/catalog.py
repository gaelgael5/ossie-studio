"""Le catalogue des types ArchiMate 3.2, livré avec l'application (pas dans
l'espace Git) : `archimate-3.2.yaml`, à côté de ce module (enablers f8167ae1 et
0e088eaa). Tous les éléments et toutes les relations d'ArchiMate 3.2, avec leur
couche, leur libellé et leur définition en français, leur icône
(`icons/<type>.svg`) et les libellés des points de vue.

Un type de l'espace repose sur un type de ce catalogue (`x-archimate`) ; les
attributs propres à ce type ArchiMate (sens d'un accès, force d'une influence…)
font partie de la base de tout type qui repose dessus. Les valeurs stockées sont
celles d'ArchiMate (`Write`), l'écran affiche leur libellé français
(« écriture ») ; les noms des attributs sont ceux du contrat d'`urban-archimate`.

Les règles de relation et les éléments de chaque point de vue sont dans `rules`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import yaml

CATALOG_FILE = Path(__file__).with_name("archimate-3.2.yaml")
ICONS_DIR = Path(__file__).with_name("icons")
ELEMENT = "element"
RELATION = "relation"


@dataclass(frozen=True)
class Attribute:
    """Un attribut propre à un type ArchiMate."""

    name: str
    label: str
    type: str = "string"  # string | boolean
    values: dict[str, str] = field(default_factory=dict)  # valeur ArchiMate → libellé
    unset: str = ""  # libellé affiché quand l'attribut est absent

    def schema(self) -> dict[str, Any]:
        """Sa forme JSON Schema (valeurs fermées : énumération des valeurs ArchiMate).
        `x-labels` (libellé français de chaque valeur), `x-unset` (libellé de
        l'attribut absent) et `x-archimate` (attribut propre au type ArchiMate,
        toujours affiché sur la fiche) servent à l'écran ; la validation les ignore."""
        out: dict[str, Any] = {"type": self.type, "title": self.label, "x-archimate": True}
        if self.values:
            out["enum"] = list(self.values)
            out["x-labels"] = dict(self.values)
        if self.unset:
            out["x-unset"] = self.unset
        return out


@dataclass(frozen=True)
class ArchimateType:
    name: str
    kind: str  # ELEMENT | RELATION
    label: str
    layer: str = ""
    definition: str = ""
    attributes: tuple[Attribute, ...] = ()
    verbs: tuple[str, str] = ("", "")  # relation : lue depuis la source, depuis la cible

    @property
    def is_relation(self) -> bool:
        return self.kind == RELATION

    def icon(self) -> str:
        """Le pictogramme du type (SVG d'un trait, couleur `currentColor`)."""
        return (ICONS_DIR / f"{self.name}.svg").read_text(encoding="utf-8")


@dataclass(frozen=True)
class Catalog:
    version: int
    archimate: str
    types: dict[str, ArchimateType]
    layers: dict[str, str] = field(default_factory=dict)  # couche → libellé, dans l'ordre
    viewpoints: dict[str, str] = field(default_factory=dict)  # point de vue → libellé

    def get(self, name: str) -> ArchimateType | None:
        return self.types.get(name)

    def elements(self, layer: str | None = None) -> list[ArchimateType]:
        """Les éléments, dans l'ordre du catalogue (d'une couche si elle est donnée)."""
        return [
            t
            for t in self.types.values()
            if not t.is_relation and (layer is None or t.layer == layer)
        ]

    def relations(self) -> list[ArchimateType]:
        return [t for t in self.types.values() if t.is_relation]


def _attribute(name: str, data: dict[str, Any]) -> Attribute:
    values = data.get("values") or {}
    return Attribute(
        name=name,
        label=str(data.get("label") or name),
        type=str(data.get("type") or "string"),
        values={str(k): str(v) for k, v in values.items()},
        unset=str(data.get("unset") or ""),
    )


def parse_catalog(text: str) -> Catalog:
    data = yaml.safe_load(text) or {}
    types: dict[str, ArchimateType] = {}
    for name, raw in (data.get("types") or {}).items():
        attributes = tuple(
            _attribute(str(a), spec or {}) for a, spec in (raw.get("attributes") or {}).items()
        )
        verbs = raw.get("verbs") or {}
        types[str(name)] = ArchimateType(
            name=str(name),
            kind=str(raw["kind"]),
            label=str(raw.get("label") or name),
            layer=str(raw.get("layer") or ""),
            definition=str(raw.get("definition") or ""),
            attributes=attributes,
            verbs=(str(verbs.get("out") or ""), str(verbs.get("in") or "")),
        )
    return Catalog(
        version=int(data.get("version") or 1),
        archimate=str(data.get("archimate") or ""),
        types=types,
        layers={str(k): str(v) for k, v in (data.get("layers") or {}).items()},
        viewpoints={str(k): str(v) for k, v in (data.get("viewpoints") or {}).items()},
    )


@cache
def catalog() -> Catalog:
    """Le catalogue livré avec l'application (lu une fois)."""
    return parse_catalog(CATALOG_FILE.read_text(encoding="utf-8"))
