"""Normalisation et validation du vocabulaire de tags gouvernés.

Un id de tag est **insensible à la casse** et normalisé en minuscules. La
hiérarchie est portée par « / » : chaque segment commence par une lettre ou un
chiffre, puis lettres, chiffres, tirets et tirets bas. Les niveaux parents n'ont
pas besoin d'exister.
"""

from __future__ import annotations

import re

_SEGMENT = r"[a-z0-9][a-z0-9_-]*"
_TAG_ID = re.compile(rf"^{_SEGMENT}(/{_SEGMENT})*$")

# Valeurs scalaires admises pour une propriété de tag (jamais de structure).
_SCALARS = (str, int, float, bool)


def normalize_tag_id(raw: str) -> str:
    return raw.strip().lower()


def tag_prefixes(tag_id: str) -> list[str]:
    """Les préfixes hiérarchiques d'un id, du plus large au plus précis
    (« gouvernance/pii/x » → [gouvernance, gouvernance/pii, gouvernance/pii/x])."""
    parts = tag_id.split("/")
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


def validate_tag_fields(
    tag_id: str, links: object, properties: object
) -> list[str]:
    errors: list[str] = []
    if not tag_id:
        errors.append("tag_id_required")
    elif not _TAG_ID.fullmatch(tag_id):
        errors.append("tag_id_invalid")
    if not _valid_links(links):
        errors.append("tag_links_invalid")
    if not _valid_properties(properties):
        errors.append("tag_properties_invalid")
    return errors


def _valid_links(links: object) -> bool:
    if not isinstance(links, dict):
        return False
    return all(
        isinstance(k, str) and k.strip() != "" and isinstance(v, str) and v.strip() != ""
        for k, v in links.items()
    )


def _valid_properties(properties: object) -> bool:
    if not isinstance(properties, dict):
        return False
    return all(
        isinstance(k, str) and k.strip() != "" and isinstance(v, _SCALARS)
        for k, v in properties.items()
    )
