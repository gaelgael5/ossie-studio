"""Curation : retenir dans `sources/<slug>/assets/` les objets de `raw/` qui
méritent de l'attention, en entier ou champ par champ.

Décisions de cadrage (Gael) : **la recopie vaut décision** (pas de liste des
champs écartés — un champ absent a été écarté, et le `git diff` ne remontre
jamais deux fois le même changement) ; l'asset garde le **même nom de fichier et
le même `$id`** que le brut (la revue et le rafraîchissement retrouvent l'origine
sans table de correspondance) ; la partie **structurelle** vient du brut, le
**travail humain** vit sous des clés `x-` et est préservé à la re-rétention.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ossie_studio.parsers.conventions import dump_schema
from ossie_studio.sources.collect_core import RAW_DIR
from ossie_studio.sources.paths import safe_assets_path, safe_source_path

ORIGIN_KEY = "x-origin"


class AssetOriginMissingError(Exception):
    """L'objet brut à retenir n'existe pas (supprimé entre-temps)."""

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.path = path


@dataclass(frozen=True)
class CurationObject:
    path: str          # nom de fichier du schéma (racine de raw/)
    title: str
    kind: str          # x-kind, ou "object"
    fields: int        # nombre de champs du brut
    retained: str      # none | partial | full


@dataclass(frozen=True)
class CurationField:
    name: str
    type: str          # type, x-kind, ou "$ref"
    retained: bool     # déjà présent dans l'asset
    ref: bool          # porte une référence ($ref) — l'écran le signale


@dataclass(frozen=True)
class CurationDetail:
    path: str
    title: str
    fields: list[CurationField]


def is_schema_file(path: str) -> bool:
    return path.endswith(".json") and "/" not in path


def _read_json_dir(directory: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not directory.is_dir():
        return out
    for file in sorted(directory.iterdir()):
        if file.is_file() and is_schema_file(file.name):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                out[file.name] = data
    return out


def _retained_state(raw: dict[str, Any], asset: dict[str, Any] | None) -> str:
    if asset is None:
        return "none"
    raw_props = set((raw.get("properties") or {}).keys())
    asset_props = set((asset.get("properties") or {}).keys())
    if not raw_props or asset_props >= raw_props:
        return "full"
    return "partial"


def list_curation(workdir: Path, slug: str) -> list[CurationObject]:
    """Les objets de `raw/` d'une source, avec leur état de rétention. Lecture
    seule."""
    try:
        raw_dir = safe_source_path(workdir, slug) / RAW_DIR
        assets_dir = safe_assets_path(workdir, slug)
    except ValueError:
        return []
    raws = _read_json_dir(raw_dir)
    assets = _read_json_dir(assets_dir)
    objects: list[CurationObject] = []
    for name, raw in sorted(raws.items()):
        objects.append(
            CurationObject(
                path=name,
                title=str(raw.get("title") or raw.get("$id") or name),
                kind=str(raw.get("x-kind") or "object"),
                fields=len(raw.get("properties") or {}),
                retained=_retained_state(raw, assets.get(name)),
            )
        )
    return objects


def _field_type(prop: dict[str, Any]) -> tuple[str, bool]:
    has_ref = "$ref" in prop or (
        isinstance(prop.get("items"), dict) and "$ref" in prop["items"]
    )
    if "$ref" in prop:
        return "$ref", True
    label = prop.get("type") or prop.get("x-kind") or ("enum" if "enum" in prop else "")
    return str(label), has_ref


def get_object_detail(workdir: Path, slug: str, path: str) -> CurationDetail | None:
    """Champs d'un objet brut, avec l'état retenu (présent dans l'asset) et le
    signalement des références. None si l'objet brut n'existe pas."""
    try:
        raw_file = safe_source_path(workdir, slug) / RAW_DIR / path
        asset_file = safe_assets_path(workdir, slug) / path
    except ValueError:
        return None
    if not raw_file.is_file():
        return None
    raw = json.loads(raw_file.read_text(encoding="utf-8"))
    asset_props: set[str] = set()
    if asset_file.is_file():
        try:
            asset = json.loads(asset_file.read_text(encoding="utf-8"))
            asset_props = set((asset.get("properties") or {}).keys())
        except json.JSONDecodeError:
            asset_props = set()
    fields: list[CurationField] = []
    for name, prop in (raw.get("properties") or {}).items():
        if not isinstance(prop, dict):
            prop = {}
        label, ref = _field_type(prop)
        fields.append(
            CurationField(name=name, type=label, retained=name in asset_props, ref=ref)
        )
    return CurationDetail(
        path=path, title=str(raw.get("title") or raw.get("$id") or path), fields=fields
    )


def build_asset(
    raw: dict[str, Any], fields: list[str] | None, existing: dict[str, Any] | None, origin: str
) -> dict[str, Any]:
    """Construit l'asset depuis le brut. `fields` None = objet entier ; sinon seuls
    ces champs. Les clés `x-` déjà posées (travail humain) sont reprises de
    l'asset existant ; la structure vient du brut. Sortie déterministe en aval."""
    asset: dict[str, Any] = dict(raw)
    # Enrichissement d'objet déjà posé (clés x-, sauf x-origin qu'on régénère).
    if existing:
        for key, value in existing.items():
            if key.startswith("x-") and key != ORIGIN_KEY:
                asset[key] = value
    asset[ORIGIN_KEY] = origin

    raw_props = raw.get("properties")
    if isinstance(raw_props, dict):
        selected = list(raw_props) if fields is None else [f for f in fields if f in raw_props]
        existing_props = (existing or {}).get("properties") or {}
        kept: dict[str, Any] = {}
        for field in selected:
            prop = dict(raw_props[field])
            for key, value in (existing_props.get(field) or {}).items():
                if key.startswith("x-"):
                    prop[key] = value
            kept[field] = prop
        asset["properties"] = kept
        required = [r for r in (raw.get("required") or []) if r in kept]
        if required:
            asset["required"] = sorted(required)
        else:
            asset.pop("required", None)
    return asset


def retain_asset_fn(slug: str, path: str, fields: list[str] | None) -> Callable[[Path], None]:
    """Écrit (ou met à jour) l'asset `assets/<path>` à partir de `raw/<path>`, en
    un commit. Lève AssetOriginMissingError si le brut n'existe pas."""

    def write(workdir: Path) -> None:
        raw_file = safe_source_path(workdir, slug) / RAW_DIR / path
        if not raw_file.is_file():
            raise AssetOriginMissingError(path)
        raw = json.loads(raw_file.read_text(encoding="utf-8"))
        assets_dir = safe_assets_path(workdir, slug)
        asset_file = assets_dir / path
        existing = (
            json.loads(asset_file.read_text(encoding="utf-8")) if asset_file.is_file() else None
        )
        asset = build_asset(raw, fields, existing, f"{RAW_DIR}/{path}")
        assets_dir.mkdir(parents=True, exist_ok=True)
        asset_file.write_text(dump_schema(asset), encoding="utf-8")

    return write


def remove_asset_fn(slug: str, path: str) -> Callable[[Path], None]:
    """Retire l'asset `assets/<path>` (récupérable dans l'historique Git)."""

    def write(workdir: Path) -> None:
        asset_file = safe_assets_path(workdir, slug) / path
        asset_file.unlink(missing_ok=True)

    return write
