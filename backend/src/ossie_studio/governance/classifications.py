"""Dérivation d'id et validation des schémas de classification et de leurs
niveaux."""

from __future__ import annotations

import re
import unicodedata

_SHORT_NAME_MAX = 12


def derive_scheme_id(name: str) -> str:
    """Un id stable dérivé du nom (minuscules, accents repliés, tirets), figé à
    la création (« Sensibilité » → « sensibilite »)."""
    folded = unicodedata.normalize("NFKD", name.strip().lower())
    ascii_only = "".join(c for c in folded if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")
    return slug[:64]


def validate_scheme(name: str, multi_value: bool, is_primary: bool) -> list[str]:
    errors: list[str] = []
    if not name.strip():
        errors.append("scheme_name_required")
    if is_primary and multi_value:
        # Un schéma multi-valeurs n'est pas une échelle : il ne peut pas être
        # le schéma principal.
        errors.append("scheme_primary_multivalue")
    return errors


def validate_level(name: str, short_name: str) -> list[str]:
    errors: list[str] = []
    if not name.strip():
        errors.append("level_name_required")
    elif "," in name:
        # Le nom est la valeur stockée dans les assets : pas de virgule (elle
        # sépare les valeurs en multi-valeurs).
        errors.append("level_name_comma")
    if not short_name.strip():
        errors.append("short_name_required")
    elif len(short_name) > _SHORT_NAME_MAX:
        errors.append("short_name_too_long")
    return errors
