"""La cible d'un import (enabler 4f32c8c6) : ce que chaque domaine fournit au
module commun. Le module ne connaît ni référentiel ni cartographie ; il demande
à la cible de valider ce que le template produit, et de dire la clé et le
libellé d'un élément pour comparer, fusionner et rendre compte."""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ItemProblem:
    """Un élément produit refusé par la cible : son rang dans la liste produite,
    le code, le détail, le champ en cause, et sa clé lisible si on la connaît."""

    index: int
    code: str
    detail: str = ""
    field: str = ""
    key: str = ""


class ImportTarget(Protocol):
    def check(self, produced: list[Any]) -> list[ItemProblem]:
        """Valide les éléments produits (schéma, clé unique parmi eux, règles du
        domaine) ; liste vide : tous valides."""
        ...

    def key(self, item: Any) -> Hashable | None:
        """La clé qui identifie un élément (comparer, fusionner) ; None sans clé."""
        ...

    def label(self, item: Any, index: int) -> str:
        """La clé lisible d'un élément, pour le compte rendu."""
        ...
