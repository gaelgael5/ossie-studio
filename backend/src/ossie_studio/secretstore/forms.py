from __future__ import annotations

from ossie_studio.secretstore.categories import is_known_category
from ossie_studio.secretstore.service import SLUG_PATTERN


def validate_new_secret(slug: str, label: str, category: str, value: str) -> list[str]:
    """Valide les champs AVANT tout usage ; messages en clair, en français."""
    errors: list[str] = []
    if not slug:
        errors.append("Le slug est obligatoire.")
    elif not SLUG_PATTERN.fullmatch(slug):
        errors.append(
            "Le slug doit contenir uniquement minuscules, chiffres, tirets et "
            "tirets bas (64 caractères maximum)."
        )
    if not label.strip():
        errors.append("Le libellé est obligatoire.")
    errors.extend(validate_category(category))
    errors.extend(validate_value(value))
    return errors


def validate_category(category: str) -> list[str]:
    if not category:
        return ["La catégorie est obligatoire."]
    if not is_known_category(category):
        return [f"La catégorie « {category} » est inconnue."]
    return []


def validate_value(value: str) -> list[str]:
    if not value:
        return ["La valeur est obligatoire."]
    return []
