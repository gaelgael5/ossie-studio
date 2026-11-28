"""Ce que la liste des objets de cartographie montre d'un objet : un extrait de sa
documentation et ses premières propriétés renseignées.

Calculé une fois, pour l'écran et pour l'index des documents (ticket 06171fd3) : la
liste des objets se lit dans l'index sans relire les fichiers, et montre exactement la
même chose."""

from __future__ import annotations

import re
from typing import Any

from ossie_studio.cartographie.model import is_empty

EXCERPT_LENGTH = 180
SHOWN_PROPERTIES = 3


def excerpt(markdown: str) -> str:
    """Le début de la documentation, sans la syntaxe Markdown la plus courante."""
    text = re.sub(r"[`*_>#\[\]()-]+", " ", markdown)
    text = " ".join(text.split())
    return text if len(text) <= EXCERPT_LENGTH else text[: EXCERPT_LENGTH - 1].rstrip() + "…"


def shown(value: Any) -> str:
    """Une valeur de propriété telle que la liste l'affiche."""
    if isinstance(value, bool):
        return "oui" if value else "non"
    if isinstance(value, (list, dict)):
        return f"{len(value)} élément(s)"
    return str(value)


def shown_properties(properties: dict[str, Any]) -> list[dict[str, str]]:
    """Les premières propriétés renseignées, nom et valeur affichée."""
    return [
        {"name": name, "value": shown(value)}
        for name, value in properties.items()
        if not is_empty(value)
    ][:SHOWN_PROPERTIES]
