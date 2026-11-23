"""Fichiers d'un asset : `<clé>.schema.yaml` (standard) + `<clé>.extend.yaml`
(travail humain) — ticket docflow 7a284e55.

Gael, 23/11/2026 : « La priorité est bien de garder les standards non altérés.
[…] je préfère que les classifications soient dans le fichier
`<filename>.extend.yaml` à côté du fichier `<filename>.schema.yaml`. » Décidé avec
lui le même jour : **tout** le travail humain sort du schéma de l'asset (nom
métier, description, concept, tags, et demain certification et classification).

**Représentation en mémoire** : l'application manipule un asset comme un seul
dictionnaire où le travail humain porte ses clés historiques `x-businessName`,
`x-description`, `x-concept`, `x-tags` (au niveau de l'objet et de chaque
propriété). Ce module est la **seule** frontière avec le disque : il sépare ces
clés vers `.extend.yaml` à l'écriture et les y reprend à la lecture. Pourquoi ce
choix plutôt que de changer chaque lecteur : la curation, la revue, la page
Curation, les primitives MCP et les règles de gouvernance lisent déjà ces clés ;
une seule frontière garantit qu'aucun d'eux n'écrit plus jamais de travail
humain dans le schéma.

**Compatibilité** : un asset écrit avant ce ticket porte encore ces clés dans son
`.schema.yaml`. Elles sont lues (le `.extend.yaml` l'emporte s'il dit autre chose)
et **déplacées** à la prochaine écriture de l'asset. Pas de migration de masse.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ossie_studio.parsers.conventions import (
    SCHEMA_SUFFIX,
    SchemaReadError,
    dump_schema,
    is_schema_name,
    load_schema,
)

EXTEND_SUFFIX = ".extend.yaml"

# Clé en mémoire (historique, dans le schéma avant 7a284e55) → clé du `.extend.yaml`.
# Liste fermée : ajouter une clé ici, c'est décider qu'elle est du travail humain.
HUMAN_KEYS: dict[str, str] = {
    "x-businessName": "businessName",
    "x-description": "description",
    "x-concept": "concept",
    "x-tags": "tags",
    # Ticket 5e8680d7 : { id du schéma : nom du niveau, ou liste (multi-valeurs) }.
    "x-classification": "classification",
}
_FROM_EXTEND = {v: k for k, v in HUMAN_KEYS.items()}

_OBJECT = "object"
_FIELDS = "fields"


def extend_name(asset_name: str) -> str:
    """`commande.schema.yaml` → `commande.extend.yaml`."""
    stem = asset_name[: -len(SCHEMA_SUFFIX)] if asset_name.endswith(SCHEMA_SUFFIX) else asset_name
    return f"{stem}{EXTEND_SUFFIX}"


def is_extend_name(name: str) -> bool:
    return name.endswith(EXTEND_SUFFIX) and "/" not in name


def asset_name_of(extend: str) -> str:
    """`commande.extend.yaml` → `commande.schema.yaml`."""
    return f"{extend[: -len(EXTEND_SUFFIX)]}{SCHEMA_SUFFIX}"


def _human(node: dict[str, Any]) -> dict[str, Any]:
    return {
        HUMAN_KEYS[k]: v for k, v in node.items() if k in HUMAN_KEYS and v not in (None, "", [], {})
    }


def split_asset(asset: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(schéma standard, document `.extend.yaml`) à partir de l'asset en mémoire.
    Le document est vide (`{}`) s'il n'y a aucun travail humain."""
    schema = {k: v for k, v in asset.items() if k not in HUMAN_KEYS}
    extend: dict[str, Any] = {}
    obj = _human(asset)
    if obj:
        extend[_OBJECT] = obj
    props = asset.get("properties")
    if isinstance(props, dict):
        clean: dict[str, Any] = {}
        fields: dict[str, Any] = {}
        for name, prop in props.items():
            if isinstance(prop, dict):
                clean[name] = {k: v for k, v in prop.items() if k not in HUMAN_KEYS}
                human = _human(prop)
                if human:
                    fields[name] = human
            else:
                clean[name] = prop
        schema["properties"] = clean
        if fields:
            extend[_FIELDS] = fields
    return schema, extend


def join_asset(schema: dict[str, Any], extend: dict[str, Any] | None) -> dict[str, Any]:
    """Asset en mémoire : le schéma (avec ses éventuelles clés héritées) recouvert
    par le `.extend.yaml`. Une entrée de champ qui ne désigne aucune propriété est
    ignorée (elle disparaîtra à la prochaine écriture)."""
    asset = dict(schema)
    if not isinstance(extend, dict):
        return asset
    obj = extend.get(_OBJECT)
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in _FROM_EXTEND:
                asset[_FROM_EXTEND[key]] = value
    fields = extend.get(_FIELDS)
    props = asset.get("properties")
    if isinstance(fields, dict) and isinstance(props, dict):
        props = dict(props)
        for name, values in fields.items():
            if isinstance(values, dict) and isinstance(props.get(name), dict):
                merged = dict(props[name])
                for key, value in values.items():
                    if key in _FROM_EXTEND:
                        merged[_FROM_EXTEND[key]] = value
                props[name] = merged
        asset["properties"] = props
    return asset


def dump_asset(asset: dict[str, Any]) -> tuple[str, str | None]:
    """Textes des deux fichiers (le second None s'il n'y a rien à écrire).
    Déterministe : même asset → mêmes octets."""
    schema, extend = split_asset(asset)
    return dump_schema(schema), (dump_schema(extend) if extend else None)


def load_asset(file: Path) -> dict[str, Any]:
    """Lit l'asset `file` (`<clé>.schema.yaml`) et son `.extend.yaml` voisin.
    Lève SchemaReadError / OSError comme `load_schema`."""
    schema = load_schema(file.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise SchemaReadError("asset: pas un objet")
    ext_file = file.with_name(extend_name(file.name))
    extend: dict[str, Any] | None = None
    if ext_file.is_file():
        try:
            loaded = load_schema(ext_file.read_text(encoding="utf-8"))
        except SchemaReadError:
            loaded = None  # extension illisible : le schéma reste lisible
        extend = loaded if isinstance(loaded, dict) else None
    return join_asset(schema, extend)


def write_asset(file: Path, asset: dict[str, Any]) -> None:
    """Écrit l'asset en deux fichiers ; supprime le `.extend.yaml` s'il n'y a plus
    de travail humain."""
    schema_text, extend_text = dump_asset(asset)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(schema_text, encoding="utf-8")
    ext_file = file.with_name(extend_name(file.name))
    if extend_text is None:
        ext_file.unlink(missing_ok=True)
    else:
        ext_file.write_text(extend_text, encoding="utf-8")


def remove_asset(file: Path) -> None:
    """Retire l'asset et son `.extend.yaml`."""
    file.unlink(missing_ok=True)
    file.with_name(extend_name(file.name)).unlink(missing_ok=True)


def read_asset_dir(directory: Path) -> dict[str, dict[str, Any]]:
    """Tous les assets d'un dossier `assets/` (nom du schéma → asset en mémoire).
    Un fichier illisible est ignoré."""
    out: dict[str, dict[str, Any]] = {}
    if not directory.is_dir():
        return out
    for file in sorted(directory.iterdir()):
        if file.is_file() and is_schema_name(file.name):
            try:
                out[file.name] = load_asset(file)
            except (SchemaReadError, OSError):
                continue
    return out
