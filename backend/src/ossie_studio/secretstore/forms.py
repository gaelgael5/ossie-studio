from __future__ import annotations

from ossie_studio.secretstore.categories import is_known_category
from ossie_studio.secretstore.service import SLUG_PATTERN


def validate_new_secret(slug: str, label: str, category: str, value: str) -> list[str]:
    """Valide les champs AVANT tout usage ; renvoie des codes de champ."""
    errors: list[str] = []
    if not slug:
        errors.append("slug_required")
    elif not SLUG_PATTERN.fullmatch(slug):
        errors.append("slug_invalid")
    if not label.strip():
        errors.append("label_required")
    errors.extend(validate_category(category))
    errors.extend(validate_value(value))
    return errors


def validate_category(category: str) -> list[str]:
    if not category:
        return ["category_required"]
    if not is_known_category(category):
        return ["secret_category_unknown"]
    return []


def validate_value(value: str) -> list[str]:
    if not value:
        return ["secret_value_required"]
    return []
