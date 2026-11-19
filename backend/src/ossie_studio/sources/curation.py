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

# Liste **fermée** des clés `x-` d'enrichissement humain (libellé → clé). Toute
# autre clé (standard ou venue du parseur) est en lecture seule (voir le document
# de conventions). S'arrêter et demander avant d'en ajouter une.
ENRICHMENT_KEYS: dict[str, str] = {
    "business_name": "x-businessName",
    "description": "x-description",
    "concept": "x-concept",
    "tags": "x-tags",
}
_ENRICHMENT_X = frozenset(ENRICHMENT_KEYS.values())


class AssetOriginMissingError(Exception):
    """L'objet brut à retenir n'existe pas (supprimé entre-temps)."""

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.path = path


class AssetNotRetainedError(Exception):
    """On enrichit un asset qui n'a pas été retenu."""

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.path = path


@dataclass(frozen=True)
class EnrichScope:
    name: str          # "" pour l'objet, sinon le nom du champ
    business_name: str
    description: str
    concept: str
    tags: list[str]
    readonly: dict[str, Any]  # description standard + clés x- du parseur


@dataclass(frozen=True)
class EnrichView:
    path: str
    title: str
    object: EnrichScope
    fields: list[EnrichScope]


@dataclass(frozen=True)
class CurationObject:
    path: str          # nom de fichier du schéma (racine de raw/)
    title: str
    kind: str          # x-kind, ou "object"
    fields: int        # nombre de champs du brut
    retained: str      # none | partial | full
    orphan: bool       # retenu mais le brut a disparu (asset sans raw)


@dataclass(frozen=True)
class CurationField:
    name: str
    type: str          # type, x-kind, ou "$ref"
    retained: bool     # déjà présent dans l'asset
    ref: bool          # porte une référence ($ref) — l'écran le signale
    missing: bool      # retenu dans l'asset mais disparu du brut


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
                orphan=False,
            )
        )
    # Assets dont le brut a disparu : signalés orphelins (jamais supprimés par le
    # rafraîchissement ; leur sort se décide à la revue).
    for name, asset in sorted(assets.items()):
        if name in raws:
            continue
        objects.append(
            CurationObject(
                path=name,
                title=str(asset.get("title") or asset.get("$id") or name),
                kind=str(asset.get("x-kind") or "object"),
                fields=len(asset.get("properties") or {}),
                retained="full",
                orphan=True,
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
    asset_data: dict[str, Any] = {}
    if asset_file.is_file():
        try:
            asset = json.loads(asset_file.read_text(encoding="utf-8"))
            asset_data = asset.get("properties") or {}
        except json.JSONDecodeError:
            asset_data = {}
    asset_props = set(asset_data.keys())
    raw_props = raw.get("properties") or {}
    fields: list[CurationField] = []
    for name, prop in raw_props.items():
        if not isinstance(prop, dict):
            prop = {}
        label, ref = _field_type(prop)
        fields.append(
            CurationField(
                name=name, type=label, retained=name in asset_props, ref=ref, missing=False
            )
        )
    # Champs retenus disparus du brut : gardés, signalés « disparu à la source ».
    for name in asset_props - set(raw_props):
        prop = asset_data[name] if isinstance(asset_data[name], dict) else {}
        label, ref = _field_type(prop)
        fields.append(
            CurationField(name=name, type=label, retained=True, ref=ref, missing=True)
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


# --- Enrichissement (clés x- humaines) ---------------------------------------


def apply_enrichment(node: dict[str, Any], values: dict[str, Any]) -> None:
    """Pose (ou retire) **uniquement** les clés d'enrichissement sur un nœud.
    Une valeur vide retire la clé (pas de valeur vide stockée). Les clés standard
    et les clés `x-` du parseur ne sont jamais touchées."""
    for label, xkey in ENRICHMENT_KEYS.items():
        if label not in values:
            continue
        value = values[label]
        empty = value is None or value == [] or (isinstance(value, str) and not value.strip())
        if empty:
            node.pop(xkey, None)
        else:
            node[xkey] = value


def _scope_view(name: str, node: dict[str, Any]) -> EnrichScope:
    readonly = {
        k: v
        for k, v in sorted(node.items())
        if k != ORIGIN_KEY
        and (k == "description" or (k.startswith("x-") and k not in _ENRICHMENT_X))
    }
    return EnrichScope(
        name=name,
        business_name=str(node.get("x-businessName") or ""),
        description=str(node.get("x-description") or ""),
        concept=str(node.get("x-concept") or ""),
        tags=[str(t) for t in (node.get("x-tags") or [])],
        readonly=readonly,
    )


def read_enrichment(workdir: Path, slug: str, path: str) -> EnrichView | None:
    """Enrichissement courant d'un asset (objet et champs) + contexte en lecture
    seule. None si l'asset n'a pas été retenu."""
    try:
        asset_file = safe_assets_path(workdir, slug) / path
    except ValueError:
        return None
    if not asset_file.is_file():
        return None
    asset = json.loads(asset_file.read_text(encoding="utf-8"))
    fields = [
        _scope_view(name, prop)
        for name, prop in (asset.get("properties") or {}).items()
        if isinstance(prop, dict)
    ]
    return EnrichView(
        path=path,
        title=str(asset.get("title") or asset.get("$id") or path),
        object=_scope_view("", asset),
        fields=fields,
    )


def enrich_asset_fn(
    slug: str,
    path: str,
    object_values: dict[str, Any] | None,
    field_values: dict[str, dict[str, Any]],
) -> Callable[[Path], None]:
    """Écrit l'enrichissement sur l'asset `assets/<path>`, en un commit. Seules les
    clés d'enrichissement sont touchées. Lève AssetNotRetainedError si l'asset
    n'existe pas. Sortie déterministe : réenregistrer la même saisie ne commite
    rien."""

    def write(workdir: Path) -> None:
        asset_file = safe_assets_path(workdir, slug) / path
        if not asset_file.is_file():
            raise AssetNotRetainedError(path)
        asset = json.loads(asset_file.read_text(encoding="utf-8"))
        if object_values is not None:
            apply_enrichment(asset, object_values)
        props = asset.get("properties") or {}
        for field, values in field_values.items():
            if field in props and isinstance(props[field], dict):
                apply_enrichment(props[field], values)
        asset_file.write_text(dump_schema(asset), encoding="utf-8")

    return write


# --- Rafraîchir depuis le brut (structure du brut, x- de l'humain) ------------


def _merge_structural(raw_node: dict[str, Any], asset_node: dict[str, Any]) -> dict[str, Any]:
    """Reconstruit un nœud à neuf : clés **standard** (et `$...`) reprises du brut,
    **toutes** les clés `x-` reprises de l'asset (jamais touchées). Une clé standard
    disparue du brut disparaît donc de l'asset ; une clé `x-` reste telle quelle."""
    merged = {k: v for k, v in raw_node.items() if not k.startswith("x-")}
    for key, value in asset_node.items():
        if key.startswith("x-"):
            merged[key] = value
    return merged


def refresh_asset(raw: dict[str, Any], asset: dict[str, Any]) -> dict[str, Any]:
    """Recopie la structure du brut dans l'asset, **champ retenu par champ retenu**,
    en gardant toutes les clés `x-` (enrichissement humain comme métadonnées du
    parseur). N'ajoute aucun champ (un champ apparu dans le brut est ignoré),
    n'en retire aucun (un champ retenu disparu du brut est gardé tel quel).
    Déterministe en aval (`dump_schema`)."""
    refreshed = {
        k: v
        for k, v in raw.items()
        if not k.startswith("x-") and k not in ("properties", "required")
    }
    for key, value in asset.items():  # x- de l'asset (dont x-origin) conservés
        if key.startswith("x-"):
            refreshed[key] = value

    asset_props = asset.get("properties")
    if isinstance(asset_props, dict):
        raw_props = raw.get("properties") or {}
        kept: dict[str, Any] = {}
        for name, aprop in asset_props.items():
            rprop = raw_props.get(name)
            if isinstance(aprop, dict) and isinstance(rprop, dict):
                kept[name] = _merge_structural(rprop, aprop)  # champ présent : structure rafraîchie
            else:
                kept[name] = aprop  # champ disparu : gardé tel quel
        refreshed["properties"] = kept
        required = sorted(r for r in (raw.get("required") or []) if r in kept)
        if required:
            refreshed["required"] = required
    return refreshed


def _refresh_pairs(
    workdir: Path, slug: str, paths: list[str] | None
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """(nom, brut, asset) pour chaque asset visé qui a encore un brut. `paths` None
    = tous les assets retenus. Les orphelins (brut absent) sont écartés."""
    try:
        raw_dir = safe_source_path(workdir, slug) / RAW_DIR
        assets_dir = safe_assets_path(workdir, slug)
    except ValueError:
        return []
    assets = _read_json_dir(assets_dir)
    raws = _read_json_dir(raw_dir)
    wanted = set(assets) if paths is None else {p for p in paths if p in assets}
    return [(name, raws[name], assets[name]) for name in sorted(wanted) if name in raws]


def refresh_targets(workdir: Path, slug: str, paths: list[str] | None) -> list[str]:
    """Liste triée des assets qui **changeraient** à un rafraîchissement (octets
    différents). Sert à ne commiter que s'il y a un changement et à lister les
    assets modifiés dans le message. Lecture seule."""
    changed: list[str] = []
    for name, raw, asset in _refresh_pairs(workdir, slug, paths):
        if dump_schema(refresh_asset(raw, asset)) != dump_schema(asset):
            changed.append(name)
    return changed


def refresh_assets_fn(slug: str, paths: list[str] | None) -> Callable[[Path], None]:
    """Réécrit chaque asset visé à sa version rafraîchie, en un commit. `paths`
    None = tous les assets retenus. Un asset inchangé est réécrit à l'identique
    (git ne voit rien) ; un orphelin n'est pas touché. Sans changement net,
    `commit_to_branch` ne commite pas."""

    def write(workdir: Path) -> None:
        assets_dir = safe_assets_path(workdir, slug)
        for name, raw, asset in _refresh_pairs(workdir, slug, paths):
            (assets_dir / name).write_text(dump_schema(refresh_asset(raw, asset)), encoding="utf-8")

    return write
