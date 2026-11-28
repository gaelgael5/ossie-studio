"""Ce qu'un import a créé (ticket 6d36073b) : pour la stratégie « On supprime les
éléments absents que cet import a créés », chaque import tient la liste des clés
des éléments qu'il a créés, dans `<slug>.provenance.yaml`, à côté de sa
déclaration, dans le dépôt de l'espace. Les documents des éléments ne sont pas
touchés (objets fermés, standards non altérés).

Mise à jour à chaque import réussi : les clés créées par cet import s'ajoutent ;
une clé dont l'élément n'existe plus (supprimé par l'import ou à la main) sort. Un
élément existant avant l'import et seulement corrigé par lui n'y entre pas.

Une clé est une chaîne (objet de cartographie) ou un tuple (clé, simple ou
composée, d'une valeur de référentiel), écrit comme une liste.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from pathlib import Path
from typing import Any

import yaml

from ossie_studio.imports.errors import ImportsError

SUFFIX = ".provenance.yaml"
CREATED_KEY = "created"


def provenance_path(folder: str, slug: str) -> str:
    return f"{folder}/{slug}{SUFFIX}"


def _key(value: Any) -> Hashable:
    return tuple(_key(v) for v in value) if isinstance(value, list) else value


def _plain(key: Hashable) -> Any:
    return [_plain(k) for k in key] if isinstance(key, tuple) else key


def read_created(root: Path, folder: str, slug: str) -> set[Hashable]:
    """Les clés des éléments que l'import a créés (vide s'il n'a rien créé)."""
    file = root / provenance_path(folder, slug)
    if not file.is_file():
        return set()
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ImportsError("import_unreadable", slug=slug, detail=str(exc)) from None
    keys = data.get(CREATED_KEY) if isinstance(data, dict) else None
    return {_key(k) for k in keys} if isinstance(keys, list) else set()


def write_created(root: Path, folder: str, slug: str, keys: Iterable[Hashable]) -> None:
    """Réécrit la liste (triée : même contenu, mêmes octets) ; vide : le fichier part."""
    file = root / provenance_path(folder, slug)
    ordered = sorted((_plain(k) for k in keys), key=repr)
    if not ordered:
        if file.is_file():
            file.unlink()
        return
    file.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        {CREATED_KEY: ordered}, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    file.write_text(text, encoding="utf-8")
