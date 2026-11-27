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


# --- « Résoudre le template » ---------------------------------------------------------

# Le type JSON Schema d'une propriété → le suffixe de conversion du moteur JSLT.
CASTS = {"integer": "integer", "number": "decimal", "boolean": "boolean", "string": "string"}


def runtime_kind(values: list[Any]) -> str | None:
    """Le type des valeurs telles que le template les reçoit : un CSV ne donne que du
    texte ; un xlsx ou un JSON donne aussi des nombres et des booléens. None : rien
    de lu, ou des genres mêlés (on ne devine pas)."""
    seen = [v for v in values if v is not None and v != ""]
    if not seen:
        return None
    if all(isinstance(v, bool) for v in seen):
        return "boolean"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in seen):
        return "integer"
    if all(isinstance(v, int | float) and not isinstance(v, bool) for v in seen):
        return "number"
    if all(isinstance(v, str) for v in seen):
        return "string"
    return None


def _path(column: str) -> str:
    plain = bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column))
    escaped = column.replace("\\", "\\\\").replace('"', '\\"')
    return f"$.{column}" if plain else f'$["{escaped}"]'


def _expression(prop: str, kind: str | None, columns: dict[str, list[Any]]) -> str:
    by_name = {property_name(c): c for c in columns}
    column = by_name.get(property_name(prop))
    if column is None:
        return "null"
    expr = _path(column)
    seen = runtime_kind(columns[column])
    # Conversion écrite si le type voulu n'est pas celui des valeurs lues ; un entier
    # lu pour une propriété « number » convient tel quel.
    if kind in CASTS and seen != kind and not (kind == "number" and seen == "integer"):
        expr += f"#{CASTS[kind]}"
    return expr


def resolve_template(
    fields: list[tuple[str, str | None]],
    group: tuple[str, list[tuple[str, str | None]]] | None,
    columns: dict[str, list[Any]],
) -> str:
    """Un template JSLT écrit d'après le modèle et les colonnes du fichier (ticket
    0598c41b, « Résoudre le template ») : toutes les propriétés, chacune reliée à la colonne
    qui lui correspond (nom normalisé, comme 221d832b) ou à `null`, avec la
    conversion quand le type diffère. `fields` : (nom, type JSON Schema) au premier
    niveau ; `group` : un objet imbriqué (la cartographie : `proprietes`)."""
    lines = ["{"]
    entries: list[str] = [
        f'  "{name}": {_expression(name, kind, columns)}' for name, kind in fields
    ]
    if group is not None:
        name, inner = group
        body = ",\n".join(f'    "{n}": {_expression(n, k, columns)}' for n, k in inner)
        entries.append(f'  "{name}": {{\n{body}\n  }}' if inner else f'  "{name}": {{}}')
    lines.append(",\n".join(entries))
    lines.append("}")
    return "\n".join(lines) + "\n"
