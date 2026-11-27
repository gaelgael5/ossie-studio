"""Les référentiels, première cible du module commun d'import (enabler 4f32c8c6).

Ce qui reste propre aux référentiels : le dossier des imports
(`referentiels/<nom>/imports/`), la clé d'une valeur (`x-key`) et son libellé, et le
type d'objet du journal. Tout le reste (lecture, JSLT, essai, compte rendu, tout
ou rien, journal) vit dans `ossie_studio/imports/`.

**Validation** (epic « Primitives MCP — écriture commune par genre », ticket
1612cce4) : la cible est le genre `referentiel_valeur` du registre. Chaque valeur
produite est validée par la méthode du validateur de ce genre — celle de
`write_documents` —, et ses refus portent le même code et le même message que par
MCP. Aucune validation n'est propre à l'import.
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from typing import Any

from ossie_studio.imports.target import ItemProblem
from ossie_studio.mcp_surface.kind_imports import check_import
from ossie_studio.referentiels.model import model_key
from ossie_studio.referentiels.values import key_label, key_of

JOURNAL_TYPE = "referentiel_import"
KIND = "referentiel_valeur"


@dataclass(frozen=True)
class ReferentielImportTarget:
    """Un référentiel vu par le module d'import : son nom et son modèle."""

    name: str
    model: dict[str, Any]

    def _identity(self, item: Any) -> dict[str, Any]:
        """L'identité d'une valeur produite, tirée de sa clé (`x-key`) ; clé absente :
        None (c'est alors la valeur qui dit ce qui manque)."""
        fields = model_key(self.model)
        parts = [item.get(f) for f in fields] if isinstance(item, Mapping) else []
        if not parts or any(p is None for p in parts):
            key: Any = None
        elif len(fields) == 1:
            key = parts[0]
        else:
            key = dict(zip(fields, parts, strict=True))
        return {"referentiel": self.name, "key": key}

    def check(self, produced: list[Any]) -> list[ItemProblem]:
        entries = [(self._identity(item), item) for item in produced]
        return [
            ItemProblem(r.index, r.code, r.message, r.path, self.label(produced[r.index], r.index))
            for r in check_import(KIND, self.model, entries)
        ]

    def key(self, item: Any) -> Hashable | None:
        found: Hashable | None = key_of(item, model_key(self.model))
        return found

    def label(self, item: Any, index: int) -> str:
        return str(key_label(item, model_key(self.model), index))
