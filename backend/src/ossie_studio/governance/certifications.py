"""Validation et presets des niveaux de certification.

Un niveau est adossé à un **tag** (ex. « certification/or ») qui le relie aux
assets ; le tag suit les mêmes règles qu'un id de tag gouverné (minuscules,
hiérarchie « / »). Le rang fixe l'ordre, le plus bas en premier.
"""

from __future__ import annotations

from ossie_studio.governance.tags import is_valid_tag_id

# Les trois niveaux par défaut, créés par le bouton « presets » et modifiables.
PRESETS: list[dict[str, object]] = [
    {
        "name": "Or",
        "rank": 1,
        "tag": "certification/or",
        "description": "Qualité la plus élevée, pleinement fiable.",
        "icon": "🥇",
    },
    {
        "name": "Argent",
        "rank": 2,
        "tag": "certification/argent",
        "description": "Bonne qualité, fiable pour la plupart des usages.",
        "icon": "🥈",
    },
    {
        "name": "Bronze",
        "rank": 3,
        "tag": "certification/bronze",
        "description": "Qualité de base, à utiliser avec prudence.",
        "icon": "🥉",
    },
]


def validate_certification(name: str, rank: object, tag: str) -> list[str]:
    errors: list[str] = []
    if not name.strip():
        errors.append("certification_name_required")
    if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
        errors.append("certification_rank_invalid")
    if not tag:
        errors.append("tag_id_required")
    elif not is_valid_tag_id(tag):
        errors.append("tag_id_invalid")
    return errors
