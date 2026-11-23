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

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ossie_studio.parsers.conventions import (
    SchemaReadError,
    is_schema_name,
    load_schema,
)
from ossie_studio.sources.asset_files import (
    HUMAN_KEYS,
    dump_asset,
    load_asset,
    read_asset_dir,
    remove_asset,
    write_asset,
)
from ossie_studio.sources.collect_core import RAW_DIR
from ossie_studio.sources.manifest import ManifestError, dump_manifest, parse_manifest
from ossie_studio.sources.paths import (
    MANIFEST_FILENAME,
    safe_assets_path,
    safe_source_path,
)
from ossie_studio.sources.tri import (
    TriRule,
    compile_rules,
    field_excluded,
    object_excluded,
    rule_label,
)

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
    name: str  # "" pour l'objet, sinon le nom du champ
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
    path: str  # nom de fichier du schéma (racine de raw/)
    title: str
    kind: str  # x-kind, ou "object"
    fields: int  # nombre de champs du brut
    retained: str  # none | partial | full
    orphan: bool  # retenu mais le brut a disparu (asset sans raw)
    excluded: bool  # écarté par une règle de tri (caché sauf filtre)
    excluded_by: str  # libellé de la règle qui l'écarte (ou "")


@dataclass(frozen=True)
class CurationField:
    name: str
    type: str  # type, x-kind, ou "$ref"
    retained: bool  # déjà présent dans l'asset
    ref: bool  # porte une référence ($ref) — l'écran le signale
    missing: bool  # retenu dans l'asset mais disparu du brut


@dataclass(frozen=True)
class CurationDetail:
    path: str
    title: str
    fields: list[CurationField]


def is_schema_file(path: str) -> bool:
    return is_schema_name(path)


def _read_json_dir(directory: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not directory.is_dir():
        return out
    for file in sorted(directory.iterdir()):
        if file.is_file() and is_schema_file(file.name):
            try:
                data = load_schema(file.read_text(encoding="utf-8"))
            except SchemaReadError:
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


def list_curation(
    workdir: Path,
    slug: str,
    rules: Sequence[TriRule] = (),
    show_excluded: bool = False,
) -> list[CurationObject]:
    """Les objets de `raw/` d'une source, avec leur état de rétention. Les règles
    de tri écartent les objets non retenus (cachés sauf `show_excluded`) ; un objet
    **retenu** n'est jamais caché, mais signale la règle en conflit. Lecture seule."""
    try:
        raw_dir = safe_source_path(workdir, slug) / RAW_DIR
        assets_dir = safe_assets_path(workdir, slug)
    except ValueError:
        return []
    rule_list = list(rules)
    raws = _read_json_dir(raw_dir)
    assets = read_asset_dir(assets_dir)
    objects: list[CurationObject] = []
    for name, raw in sorted(raws.items()):
        retained = _retained_state(raw, assets.get(name))
        rule = object_excluded(raw, rule_list)
        # Non retenu + écarté = caché (sauf filtre) ; retenu + écarté = montré, en conflit.
        hidden = rule is not None and retained == "none"
        if hidden and not show_excluded:
            continue
        objects.append(
            CurationObject(
                path=name,
                title=str(raw.get("title") or raw.get("$id") or name),
                kind=str(raw.get("x-kind") or "object"),
                fields=len(raw.get("properties") or {}),
                retained=retained,
                orphan=False,
                excluded=hidden,
                excluded_by=rule_label(rule) if rule is not None else "",
            )
        )
    # Assets dont le brut a disparu : signalés orphelins (jamais supprimés par le
    # rafraîchissement ; leur sort se décide à la revue).
    for name, asset in sorted(assets.items()):
        if name in raws:
            continue
        rule = object_excluded(asset, rule_list)
        objects.append(
            CurationObject(
                path=name,
                title=str(asset.get("title") or asset.get("$id") or name),
                kind=str(asset.get("x-kind") or "object"),
                fields=len(asset.get("properties") or {}),
                retained="full",
                orphan=True,
                excluded=False,  # un asset existe : jamais caché
                excluded_by=rule_label(rule) if rule is not None else "",
            )
        )
    return objects


def _field_type(prop: dict[str, Any]) -> tuple[str, bool]:
    has_ref = "$ref" in prop or (isinstance(prop.get("items"), dict) and "$ref" in prop["items"])
    if "$ref" in prop:
        return "$ref", True
    label = prop.get("type") or prop.get("x-kind") or ("enum" if "enum" in prop else "")
    return str(label), has_ref


def get_object_detail(
    workdir: Path, slug: str, path: str, rules: Sequence[TriRule] = ()
) -> CurationDetail | None:
    """Champs d'un objet brut, avec l'état retenu (présent dans l'asset) et le
    signalement des références. Les champs écartés par une règle de tri sont
    retirés (bruit). None si l'objet brut n'existe pas."""
    try:
        raw_file = safe_source_path(workdir, slug) / RAW_DIR / path
        asset_file = safe_assets_path(workdir, slug) / path
    except ValueError:
        return None
    if not raw_file.is_file():
        return None
    rule_list = list(rules)
    raw = load_schema(raw_file.read_text(encoding="utf-8"))
    asset_data: dict[str, Any] = {}
    if asset_file.is_file():
        try:
            asset = load_asset(asset_file)
            asset_data = asset.get("properties") or {}
        except SchemaReadError:
            asset_data = {}
    asset_props = set(asset_data.keys())
    raw_props = raw.get("properties") or {}
    fields: list[CurationField] = []
    for name, prop in raw_props.items():
        if field_excluded(name, rule_list) is not None:
            continue  # champ technique écarté du tri
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
        if field_excluded(name, rule_list) is not None:
            continue
        prop = asset_data[name] if isinstance(asset_data[name], dict) else {}
        label, ref = _field_type(prop)
        fields.append(CurationField(name=name, type=label, retained=True, ref=ref, missing=True))
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
        raw = load_schema(raw_file.read_text(encoding="utf-8"))
        assets_dir = safe_assets_path(workdir, slug)
        asset_file = assets_dir / path
        existing = load_asset(asset_file) if asset_file.is_file() else None
        asset = build_asset(raw, fields, existing, f"{RAW_DIR}/{path}")
        write_asset(asset_file, asset)

    return write


def remove_asset_fn(slug: str, path: str) -> Callable[[Path], None]:
    """Retire l'asset `assets/<path>` (récupérable dans l'historique Git)."""

    def write(workdir: Path) -> None:
        remove_asset(safe_assets_path(workdir, slug) / path)

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
        if k != ORIGIN_KEY and (k == "description" or (k.startswith("x-") and k not in HUMAN_KEYS))
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
    asset = load_asset(asset_file)
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
        asset = load_asset(asset_file)
        if object_values is not None:
            apply_enrichment(asset, object_values)
        props = asset.get("properties") or {}
        for field, values in field_values.items():
            if field in props and isinstance(props[field], dict):
                apply_enrichment(props[field], values)
        write_asset(asset_file, asset)

    return write


# --- Classification (ticket 5e8680d7) ----------------------------------------

CLASSIFICATION_X = "x-classification"


def _apply_classification(node: dict[str, Any], values: dict[str, list[str]]) -> None:
    """Pose ou retire, schéma par schéma, la classification déclarée d'un nœud.
    Une liste vide retire la déclaration (retour à « dérivée »). Un schéma à
    valeur unique est stocké comme un nom, un schéma multi-valeurs comme une
    liste : `values` porte déjà la forme voulue (voir `classify_asset_fn`)."""
    current = node.get(CLASSIFICATION_X)
    declared: dict[str, Any] = dict(current) if isinstance(current, dict) else {}
    for scheme_id, names in values.items():
        if names:
            declared[scheme_id] = names
        else:
            declared.pop(scheme_id, None)
    if declared:
        node[CLASSIFICATION_X] = declared
    else:
        node.pop(CLASSIFICATION_X, None)


def classify_asset_fn(
    slug: str,
    path: str,
    object_values: dict[str, Any] | None,
    field_values: dict[str, dict[str, Any]],
) -> Callable[[Path], None]:
    """Écrit la classification déclarée de l'asset et de ses champs (dans son
    `.extend.yaml`), en un commit. Les valeurs sont celles à stocker (nom, ou
    liste pour un schéma multi-valeurs ; vide = retrait) et ont été validées
    contre les schémas. Lève AssetNotRetainedError si l'asset n'existe pas."""

    def write(workdir: Path) -> None:
        asset_file = safe_assets_path(workdir, slug) / path
        if not asset_file.is_file():
            raise AssetNotRetainedError(path)
        asset = load_asset(asset_file)
        if object_values is not None:
            _apply_classification(asset, object_values)
        props = asset.get("properties") or {}
        for name, values in field_values.items():
            if isinstance(props.get(name), dict):
                _apply_classification(props[name], values)
        write_asset(asset_file, asset)

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
    assets = read_asset_dir(assets_dir)
    raws = _read_json_dir(raw_dir)
    wanted = set(assets) if paths is None else {p for p in paths if p in assets}
    return [(name, raws[name], assets[name]) for name in sorted(wanted) if name in raws]


def refresh_targets(workdir: Path, slug: str, paths: list[str] | None) -> list[str]:
    """Liste triée des assets qui **changeraient** à un rafraîchissement (octets
    différents). Sert à ne commiter que s'il y a un changement et à lister les
    assets modifiés dans le message. Lecture seule."""
    changed: list[str] = []
    for name, raw, asset in _refresh_pairs(workdir, slug, paths):
        if dump_asset(refresh_asset(raw, asset)) != dump_asset(asset):
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
            write_asset(assets_dir / name, refresh_asset(raw, asset))

    return write


# --- Revue des changements du brut, limitée aux assets retenus ----------------
#
# La revue compare trois états par objet : le brut **publié** (tête de la branche
# de lecture), le brut de ma vue (publié + mon brouillon) et l'asset retenu.
# Ticket docflow aad1d3c6 : plus de point de reprise (`review.yaml`) ; Gael,
# 23/11/2026 : « ce qui fait disparaître le badge nouveau, c'est de publier et de
# ne plus avoir de modifications à pousser ». Le calcul est pur et testable ;
# les décisions s'écrivent dans le brouillon.


@dataclass(frozen=True)
class ReviewField:
    name: str
    change: str  # changed | appeared | disappeared
    type: str
    lost_enrichment: list[str]  # clés x- d'enrichissement perdues (disappeared)


@dataclass(frozen=True)
class ReviewObject:
    path: str
    title: str
    status: str  # retained-touched | object-disappeared | new
    fields: list[ReviewField]


@dataclass(frozen=True)
class ReviewResult:
    objects: list[ReviewObject]


@dataclass(frozen=True)
class ReviewDecisions:
    apply_fields: dict[str, list[str]]  # path → champs retenus à rafraîchir (changed)
    take_fields: dict[str, list[str]]  # path → champs apparus à reprendre (appeared)
    remove_fields: dict[str, list[str]]  # path → champs disparus à retirer (disappeared)
    remove_objects: list[str]  # assets à retirer (objet disparu du brut)
    retain_objects: list[str]  # objets bruts nouveaux à retenir en entier


def published_raw(
    raw_view: dict[str, dict[str, Any]], published_texts: dict[str, str | None]
) -> dict[str, dict[str, Any]]:
    """Brut publié d'une source, reconstruit à partir du brut de ma vue et du
    texte publié des seuls fichiers que mon brouillon touche (None : absent côté
    publié, donc créé par mon brouillon)."""
    base = dict(raw_view)
    for name, text in published_texts.items():
        parsed = parse_schema_texts({name: text}) if text is not None else {}
        if name in parsed:
            base[name] = parsed[name]
        else:
            base.pop(name, None)
    return base


def read_source_trees(
    workdir: Path, slug: str
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """(brut, assets) d'une source tels qu'ils sont dans l'arbre de travail, à
    HEAD. Lecture seule."""
    try:
        raw_dir = safe_source_path(workdir, slug) / RAW_DIR
        assets_dir = safe_assets_path(workdir, slug)
    except ValueError:
        return {}, {}
    return _read_json_dir(raw_dir), read_asset_dir(assets_dir)


def parse_schema_texts(text_map: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Parse une table {fichier: texte YAML} (le brut publié) en
    {fichier: schéma}, en ignorant un fichier illisible."""
    out: dict[str, dict[str, Any]] = {}
    for name, text in text_map.items():
        if not is_schema_file(name):
            continue
        try:
            data = load_schema(text)
        except SchemaReadError:
            continue
        if isinstance(data, dict):
            out[name] = data
    return out


def _lost_enrichment(node: dict[str, Any]) -> list[str]:
    # Tout travail humain (classification comprise) est perdu avec le champ.
    return sorted(k for k in node if k in HUMAN_KEYS)


def _review_fields(
    rb: dict[str, Any] | None,
    rh: dict[str, Any] | None,
    asset: dict[str, Any],
    rules: list[TriRule],
) -> list[ReviewField]:
    base = (rb or {}).get("properties") or {}
    head = (rh or {}).get("properties") or {}
    aprops = asset.get("properties") or {}
    out: list[ReviewField] = []
    for name in sorted(set(base) | set(head) | set(aprops)):
        in_b, in_h, in_a = name in base, name in head, name in aprops
        hp = head[name] if isinstance(head.get(name), dict) else {}
        ap = aprops[name] if isinstance(aprops.get(name), dict) else {}
        if in_a and in_h:
            # Champ retenu encore présent : « changé » si le rafraîchissement
            # modifierait sa structure (on ignore les clés x-).
            if _structural(ap) != _structural(hp):
                label, _ = _field_type(hp)
                out.append(ReviewField(name=name, change="changed", type=label, lost_enrichment=[]))
        elif in_a and not in_h:
            # Champ retenu absent du brut : on montre ce qui serait perdu. Signalé
            # tant que l'asset n'est pas corrigé, même une fois la disparition
            # publiée (sinon l'asset garderait un champ fantôme sans que personne
            # ne le voie).
            label, _ = _field_type(ap)
            out.append(
                ReviewField(
                    name=name,
                    change="disappeared",
                    type=label,
                    lost_enrichment=_lost_enrichment(ap),
                )
            )
        elif in_h and not in_a and not in_b and field_excluded(name, rules) is None:
            # Champ apparu par rapport au publié sur un objet retenu, pas encore repris
            # (sauf champ technique écarté par une règle de tri).
            label, _ = _field_type(hp)
            out.append(ReviewField(name=name, change="appeared", type=label, lost_enrichment=[]))
    return out


def _structural(node: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in node.items() if not k.startswith("x-")}


def build_review(
    raw_base: dict[str, dict[str, Any]],
    raw_head: dict[str, dict[str, Any]],
    assets: dict[str, dict[str, Any]],
    rules: Sequence[TriRule] = (),
) -> ReviewResult:
    """Décisions à présenter, par objet, en comparant le brut publié (`raw_base`),
    le brut de ma vue (`raw_head`) et les assets. Ne remonte que le bruit utile ;
    les règles de tri écartent les nouveaux objets et les champs apparus
    techniques. Déterministe (tri)."""
    rule_list = list(rules)
    objects: list[ReviewObject] = []
    for name in sorted(set(raw_base) | set(raw_head) | set(assets)):
        rb = raw_base.get(name)
        rh = raw_head.get(name)
        asset = assets.get(name)
        if asset is not None:
            if rh is None:
                # Retenu mais absent du brut (orphelin), publié ou non.
                objects.append(
                    ReviewObject(
                        path=name,
                        title=str(asset.get("title") or asset.get("$id") or name),
                        status="object-disappeared",
                        fields=[],
                    )
                )
                continue
            fields = _review_fields(rb, rh, asset, rule_list)
            if fields:
                objects.append(
                    ReviewObject(
                        path=name,
                        title=str(rh.get("title") or rh.get("$id") or name),
                        status="retained-touched",
                        fields=fields,
                    )
                )
        elif rh is not None and rb is None and object_excluded(rh, rule_list) is None:
            # Nouvel objet par rapport au publié, pas écarté par une règle de tri.
            objects.append(
                ReviewObject(
                    path=name,
                    title=str(rh.get("title") or rh.get("$id") or name),
                    status="new",
                    fields=[],
                )
            )
    return ReviewResult(objects=objects)


def apply_review_fn(slug: str, decisions: ReviewDecisions) -> Callable[[Path], None]:
    """Applique les décisions de revue dans ma vue (donc dans mon brouillon). Un
    changement non décidé est laissé tel quel. Déterministe (`dump_schema`)."""

    def write(workdir: Path) -> None:
        raw_dir = safe_source_path(workdir, slug) / RAW_DIR
        assets_dir = safe_assets_path(workdir, slug)
        raws = _read_json_dir(raw_dir)
        edited = (
            set(decisions.apply_fields) | set(decisions.take_fields) | set(decisions.remove_fields)
        )
        for path in sorted(edited):
            asset_file = assets_dir / path
            if not asset_file.is_file():
                continue
            asset = load_asset(asset_file)
            raw = raws.get(path) or {}
            rprops = raw.get("properties") or {}
            props: dict[str, Any] = asset.setdefault("properties", {})
            for field_name in decisions.apply_fields.get(path, []):
                if isinstance(props.get(field_name), dict) and isinstance(
                    rprops.get(field_name), dict
                ):
                    props[field_name] = _merge_structural(rprops[field_name], props[field_name])
            for field_name in decisions.take_fields.get(path, []):
                if isinstance(rprops.get(field_name), dict):
                    props[field_name] = dict(rprops[field_name])
            for field_name in decisions.remove_fields.get(path, []):
                props.pop(field_name, None)
            required = sorted(r for r in (raw.get("required") or []) if r in props)
            if required:
                asset["required"] = required
            else:
                asset.pop("required", None)
            write_asset(asset_file, asset)
        for path in decisions.remove_objects:
            remove_asset(assets_dir / path)
        for path in decisions.retain_objects:
            new_raw = raws.get(path)
            if new_raw is None:
                continue
            assets_dir.mkdir(parents=True, exist_ok=True)
            asset = build_asset(new_raw, None, None, f"{RAW_DIR}/{path}")
            write_asset(assets_dir / path, asset)

    return write


# --- Règles de tri (écarter le bruit), stockées dans le manifeste -------------


def read_tri_rules_raw(workdir: Path, slug: str) -> list[dict[str, Any]]:
    """Section `triage` brute du manifeste d'une source (pour l'éditeur). [] si le
    manifeste est absent ou illisible. Lecture seule."""
    try:
        manifest_file = safe_source_path(workdir, slug) / MANIFEST_FILENAME
        if not manifest_file.is_file():
            return []
        manifest = parse_manifest(manifest_file.read_text(encoding="utf-8"))
    except (ManifestError, OSError, ValueError):  # défense en profondeur côté lecture
        return []
    return list(manifest.triage)


def read_tri_rules(workdir: Path, slug: str) -> list[TriRule]:
    """Règles de tri compilées d'une source (pour filtrer liste et revue)."""
    return compile_rules(read_tri_rules_raw(workdir, slug))


def set_tri_rules_fn(slug: str, rules: list[dict[str, Any]]) -> Callable[[Path], None]:
    """Écrit la section `triage` du manifeste (remplace la liste), en un commit. Le
    reste du manifeste est préservé. Lève SourceManifestError si le manifeste est
    absent ou invalide."""

    def write(workdir: Path) -> None:
        manifest_file = safe_source_path(workdir, slug) / MANIFEST_FILENAME
        if not manifest_file.is_file():
            raise FileNotFoundError(slug)
        manifest = parse_manifest(manifest_file.read_text(encoding="utf-8"))
        updated = replace(manifest, triage=rules)
        manifest_file.write_text(dump_manifest(updated), encoding="utf-8")

    return write
