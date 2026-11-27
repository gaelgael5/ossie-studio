"""Colonnes d'un fichier d'import face aux propriétés du modèle (ticket 221d832b).

Gael : « on regarde toutes les propriétés qui sont dans l'objet et on les compare
aux colonnes existantes de la feuille ; à droite de chaque colonne qui ne matche
pas avec une propriété, on propose un bouton ajouter dans le modèle. »

Correspondance par nom normalisé (sans casse ni accents, le reste ramené à `_`) :
« Délai (jours) » ↔ `delai_jours`. Le même nom normalisé sert de nom à la
propriété ajoutée : il est valide pour les deux cibles (référentiel, type de
cartographie : minuscules, chiffres, `_`, commence par une lettre).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_INT = re.compile(r"^[+-]?\d+$")
_NUMBER = re.compile(r"^[+-]?(\d+[.,]\d*|\d*[.,]\d+)$")


def property_name(column: str) -> str:
    decomposed = unicodedata.normalize("NFKD", column)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c)).lower()
    name = re.sub(r"[^a-z0-9]+", "_", ascii_only).strip("_")
    if not name:
        return "colonne"
    return name if name[0].isalpha() else f"c_{name}"


def infer_type(values: list[Any]) -> str:
    """Le type JSON Schema le plus étroit qui accepte toutes les valeurs non vides."""
    seen = [v for v in values if v is not None and str(v).strip() != ""]
    if not seen:
        return "string"
    if all(isinstance(v, bool) or str(v).strip().lower() in ("true", "false") for v in seen):
        return "boolean"
    if all(
        not isinstance(v, bool) and (isinstance(v, int) or _INT.match(str(v).strip())) for v in seen
    ):
        return "integer"
    if all(
        not isinstance(v, bool)
        and (
            isinstance(v, int | float)
            or _INT.match(str(v).strip())
            or _NUMBER.match(str(v).strip())
        )
        for v in seen
    ):
        return "number"
    return "string"


def compare(columns: list[str], properties: list[str]) -> list[dict[str, Any]]:
    """Chaque colonne, la propriété qui lui correspond (ou None) et le nom qu'aurait
    la propriété ajoutée."""
    by_name = {property_name(p): p for p in properties}
    return [
        {
            "column": c,
            "property": by_name.get(property_name(c)),
            "proposed": property_name(c),
        }
        for c in columns
    ]
