"""Effet d'un nouveau modèle sur les valeurs existantes d'un référentiel.

À l'enregistrement d'un modèle, les propriétés **retirées** du modèle sont
retirées des valeurs (à toute profondeur, objets et tableaux d'objets compris) ;
les valeurs obtenues doivent ensuite être valides pour le nouveau modèle (types,
propriétés requises, clé unique), sinon l'enregistrement est refusé.

L'aperçu (`preview`) dit tout cela **avant** d'enregistrer : combien de valeurs
restent valides, combien perdent chaque propriété retirée, les problèmes et les
clés en double.
"""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ossie_studio.referentiels.errors import Problem
from ossie_studio.referentiels.model import SEPARATOR, check_model, model_key
from ossie_studio.referentiels.values import check_values, key_label

PREVIEW_PROBLEMS_MAX = 50


def _strip(schema: Any, value: Any, path: str, removed: Counter[str]) -> Any:
    """La valeur sans les propriétés que le schéma ne déclare plus."""
    if not isinstance(schema, dict):
        return value
    kind = schema.get("type")
    if kind == "object" and isinstance(value, dict):
        raw = schema.get("properties")
        props: dict[str, Any] = raw if isinstance(raw, dict) else {}
        out: dict[str, Any] = {}
        for name, sub in value.items():
            where = f"{path}{SEPARATOR}{name}" if path else name
            if name not in props:
                removed[where] += 1
                continue
            out[name] = _strip(props[name], sub, where, removed)
        return out
    if kind == "array" and isinstance(value, list):
        return [_strip(schema.get("items"), item, f"{path}[]", removed) for item in value]
    return value


def adapt_values(model: dict[str, Any], values: list[Any]) -> tuple[list[Any], dict[str, int]]:
    """Les valeurs sans les propriétés retirées du modèle, et pour chaque
    propriété retirée le nombre de valeurs qui la portaient."""
    adapted: list[Any] = []
    totals: Counter[str] = Counter()
    for value in values:
        removed: Counter[str] = Counter()
        adapted.append(_strip(model, copy.deepcopy(value), "", removed))
        for where in removed:
            totals[where] += 1
    return adapted, dict(sorted(totals.items()))


@dataclass(frozen=True)
class ModelPreview:
    model_problems: list[Problem]
    values: int
    valid: int
    removed: dict[str, int]
    problems: list[Problem] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.model_problems and not self.problems

    def payload(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "model_problems": [p.payload() for p in self.model_problems],
            "values": self.values,
            "valid": self.valid,
            "removed": [{"property": k, "values": n} for k, n in self.removed.items()],
            "problems": [p.payload() for p in self.problems[:PREVIEW_PROBLEMS_MAX]],
            "problem_count": len(self.problems),
            "duplicates": self.duplicates,
        }


def preview(model: Any, values: list[Any]) -> ModelPreview:
    """Ce que deviendraient les valeurs avec ce modèle."""
    model_problems = check_model(model)
    if model_problems:
        return ModelPreview(model_problems, len(values), 0, {})
    assert isinstance(model, dict)
    adapted, removed = adapt_values(model, values)
    problems = check_values(model, adapted)
    # Une valeur est touchée si un problème la désigne (par sa clé ou son rang).
    touched = {p.path.split(SEPARATOR)[0] for p in problems}
    key = model_key(model)
    valid = sum(1 for i, v in enumerate(adapted) if key_label(v, key, i) not in touched)
    duplicates = sorted({p.detail for p in problems if p.code == "key_duplicate"})
    return ModelPreview([], len(values), valid, removed, problems, duplicates)
