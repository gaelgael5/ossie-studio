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


def effective_mapping(
    columns: list[str], properties: list[str], chosen: dict[str, str] | None = None
) -> dict[str, str | None]:
    """La propriété de chaque colonne (ticket 47716786) : le choix enregistré s'il
    vise une propriété du modèle ("" : colonne ignorée), sinon la correspondance par
    nom normalisé, sauf si cette propriété est déjà prise par un choix. Un choix vers
    une propriété disparue du modèle est ignoré (retour à la correspondance par nom)."""
    chosen = {c: p for c, p in (chosen or {}).items() if p == "" or p in properties}
    taken = {p for p in chosen.values() if p}
    by_name = {property_name(p): p for p in properties}
    out: dict[str, str | None] = {}
    for column in columns:
        if column in chosen:
            out[column] = chosen[column] or None
        else:
            auto = by_name.get(property_name(column))
            out[column] = auto if auto not in taken else None
    return out


def minimal_mapping(
    columns: list[str], properties: list[str], chosen: dict[str, str]
) -> dict[str, str]:
    """Les seuls choix utiles : la déclaration ne garde que les écarts à la
    correspondance par nom ; un choix dont l'absence ne change pas le résultat
    n'est pas gardé."""
    target = effective_mapping(columns, properties, chosen)
    for column in list(chosen):
        trial = {c: p for c, p in chosen.items() if c != column}
        if effective_mapping(columns, properties, trial) == target:
            chosen = trial
    return chosen


def follow_renames(
    columns: list[str],
    before: list[str],
    after: list[str],
    chosen: dict[str, str],
    renames: dict[str, str],
) -> dict[str, str]:
    """Les choix d'un import après le renommage de propriétés (ticket 859579bb) :
    chaque colonne qui allait à un ancien nom va au nouveau, les autres gardent
    leur propriété, qu'elle vienne d'un choix ou de la correspondance par nom."""
    target = effective_mapping(columns, before, chosen)
    wanted = {c: renames.get(p, p) if p else "" for c, p in target.items()}
    # Les choix sur des colonnes absentes de l'exemple sont gardés, renommés.
    extra = {c: renames.get(p, p) for c, p in chosen.items() if c not in target}
    return minimal_mapping(columns, after, {**extra, **wanted})


def rename_template_keys(template: str, renames: dict[str, str]) -> str:
    """Les clés `"ancien":` d'un template JSLT deviennent `"nouveau":` (ticket
    859579bb). Seules les clés d'objet sont visées : une chaîne `"ancien"` qui
    n'est pas suivie de `:` (valeur, argument) reste telle quelle."""
    for old, new in renames.items():
        pattern = re.compile(r'"' + re.escape(old) + r'"(\s*):')
        template = pattern.sub(r'"' + new.replace("\\", "\\\\") + r'"\1:', template)
    return template


def auto_property(column: str, properties: list[str]) -> str | None:
    """La correspondance par nom normalisé seule (sans les choix)."""
    return {property_name(p): p for p in properties}.get(property_name(column))


def compare(
    columns: list[str], properties: list[str], chosen: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Chaque colonne, la propriété qui lui correspond (ou None), si elle a été
    choisie à la main, et le nom qu'aurait la propriété ajoutée."""
    mapping = effective_mapping(columns, properties, chosen)
    return [
        {
            "column": c,
            "property": mapping[c],
            "chosen": c in (chosen or {}) and mapping[c] != auto_property(c, properties),
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


def _expression(
    prop: str, kind: str | None, columns: dict[str, list[Any]], assigned: dict[str, str]
) -> str:
    column = assigned.get(prop)
    if column is None:
        return "null"
    expr = _path(column)
    seen = runtime_kind(columns.get(column, []))
    # Conversion écrite si le type voulu n'est pas celui des valeurs lues ; un entier
    # lu pour une propriété « number » convient tel quel.
    if kind in CASTS and seen != kind and not (kind == "number" and seen == "integer"):
        expr += f"#{CASTS[kind]}"
    return expr


def resolve_template(
    fields: list[tuple[str, str | None]],
    group: tuple[str, list[tuple[str, str | None]]] | None,
    columns: dict[str, list[Any]],
    chosen: dict[str, str] | None = None,
) -> str:
    """Un template JSLT écrit d'après le modèle et les colonnes du fichier (ticket
    0598c41b, « Résoudre le template ») : toutes les propriétés, chacune reliée à la colonne
    qui lui correspond — choisie à la main (47716786), sinon même nom normalisé
    (221d832b) — ou à `null`, avec la conversion quand le type diffère. `fields` :
    (nom, type JSON Schema) au premier niveau ; `group` : un objet imbriqué (la
    cartographie : `proprietes`)."""
    names = [n for n, _ in fields] + ([n for n, _ in group[1]] if group else [])
    assigned: dict[str, str] = {}
    for column, prop in effective_mapping(list(columns), names, chosen).items():
        if prop is not None:
            assigned.setdefault(prop, column)
    lines = ["{"]
    entries: list[str] = [
        f'  "{name}": {_expression(name, kind, columns, assigned)}' for name, kind in fields
    ]
    if group is not None:
        name, inner = group
        body = ",\n".join(f'    "{n}": {_expression(n, k, columns, assigned)}' for n, k in inner)
        entries.append(f'  "{name}": {{\n{body}\n  }}' if inner else f'  "{name}": {{}}')
    lines.append(",\n".join(entries))
    lines.append("}")
    return "\n".join(lines) + "\n"
