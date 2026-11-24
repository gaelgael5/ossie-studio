"""Catalogue des fonctions JSLT, pour l'auto-complétion de l'éditeur de template
(repris de Miles, `portail/backend/src/miles/transform/api_editor.py`).

Le composant `<jslt-editor>` va chercher ce catalogue à l'URL qu'on lui donne. Il vient
d'`urban-json` lui-même : les fonctions proposées à la saisie sont celles que la
transformation saura exécuter.
"""

from __future__ import annotations

from typing import Any

import urban_json_editor
from fastapi import APIRouter

from ossie_studio.api.common import CurrentUser

# Assets du composant. Sous /api : seul préfixe relayé par le nginx du front.
JSLT_EDITOR_PATH = "/api/jslt-editor"

router = APIRouter()


@router.get("/api/jslt-functions")
def jslt_functions(_: CurrentUser) -> dict[str, list[dict[str, Any]]]:
    return {"functions": urban_json_editor.catalog()}
