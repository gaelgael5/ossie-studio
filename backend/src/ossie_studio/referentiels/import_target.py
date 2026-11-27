"""Les référentiels, première cible du module commun d'import (enabler 4f32c8c6).

Ce qui reste propre aux référentiels : le dossier des imports
(`referentiels/<nom>/imports/`), la validation d'une valeur par le modèle (clé
unique comprise, `values.check_values`), sa clé (`x-key`) et son libellé, et le
type d'objet du journal. Tout le reste (lecture, JSLT, essai, compte rendu, tout
ou rien, journal) vit dans `ossie_studio/imports/`.
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass
from typing import Any

from ossie_studio.imports.target import ItemProblem
from ossie_studio.referentiels.model import SEPARATOR, model_key
from ossie_studio.referentiels.values import check_values, key_label, key_of

JOURNAL_TYPE = "referentiel_import"


@dataclass(frozen=True)
class ReferentielImportTarget:
    """Un référentiel vu par le module d'import : son modèle suffit."""

    model: dict[str, Any]

    def check(self, produced: list[Any]) -> list[ItemProblem]:
        return [
            ItemProblem(p.index, p.code, p.detail, p.field, p.path.split(SEPARATOR)[0])
            for p in check_values(self.model, produced)
        ]

    def key(self, item: Any) -> Hashable | None:
        found: Hashable | None = key_of(item, model_key(self.model))
        return found

    def label(self, item: Any, index: int) -> str:
        return str(key_label(item, model_key(self.model), index))
