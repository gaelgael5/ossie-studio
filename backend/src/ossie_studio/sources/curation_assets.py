"""Page « Curation » : les assets retenus de toutes les sources et leurs descriptions.

Ne pas confondre avec l'ancien onglet « Curation » d'une source (liste de tous
les objets raw, supprimé : on retient désormais depuis l'Explorateur raw). Ici on
ne voit QUE ce qui est retenu — Gael, 22/11/2026 : « dans curation on a que les
objets que l'on a retenus » — pour le décrire (ticket docflow aeb2e429).

Deux lectures :
- les fichiers `sources/<slug>/assets/*.schema.yaml` du brouillon, pour ce que l'humain
  a écrit (propriétés retenues, noms métier, descriptions) ;
- le graphe de l'explorateur raw (index déjà en cache), pour ce qui vient du
  raw : fichier d'origine, nombre de champs, « modifié depuis la revue », et la
  résolution des références vers les autres objets. Pas de seconde résolution.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ossie_studio.sources.curation import _read_json_dir
from ossie_studio.sources.explorer import BADGE_MODIFIED, SchemaRef, list_source_slugs
from ossie_studio.sources.explorer_graph import ReferenceGraph
from ossie_studio.sources.paths import safe_assets_path

AssetKey = tuple[str, str]  # (source, fichier du schéma)

# Les liens d'une propriété vers un autre objet (ni l'héritage, ni les clés
# étrangères SQL, qui ne portent pas de champ objet).
_PROPERTY_REF_KINDS = frozenset({"field", "list", "map"})


def read_assets(workdir: Path) -> dict[AssetKey, dict[str, Any]]:
    """Tous les assets retenus de la branche, par (source, fichier)."""
    out: dict[AssetKey, dict[str, Any]] = {}
    for slug in list_source_slugs(workdir):
        try:
            directory = safe_assets_path(workdir, slug)
        except ValueError:
            continue
        for name, data in _read_json_dir(directory).items():
            out[(slug, name)] = data
    return out


def _described(node: dict[str, Any]) -> bool:
    return bool(str(node.get("x-description") or "").strip())


def _properties(asset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    props = asset.get("properties")
    if not isinstance(props, dict):
        return {}
    return {k: v for k, v in props.items() if isinstance(v, dict)}


@dataclass(frozen=True)
class AssetSummary:
    source: str
    path: str
    title: str
    kind: str
    parser: str
    origin: str  # fichier source d'origine (Ventes/Commande.cs), "" si inconnu
    folder: str  # dossier de l'objet dans l'Explorateur raw (lien « Voir dans l'Explorateur »)
    property_count: int  # propriétés retenues
    raw_fields: int  # champs de l'objet raw (0 si le raw a disparu)
    undescribed: int  # propriétés sans description
    described: bool  # l'objet lui-même a une description
    modified: bool  # l'objet raw a changé depuis le point de revue de la source
    orphan: bool  # l'objet raw a disparu
    # Noms raw et métier des propriétés : la recherche de la page porte aussi
    # sur eux (maquette : « Rechercher un asset, une propriété »).
    property_names: tuple[str, ...] = ()


def summarize(assets: dict[AssetKey, dict[str, Any]], graph: ReferenceGraph) -> list[AssetSummary]:
    out: list[AssetSummary] = []
    for (slug, name), asset in sorted(assets.items()):
        props = _properties(asset)
        located = graph.located((slug, name))
        obj = located[1] if located else None
        out.append(
            AssetSummary(
                source=slug,
                path=name,
                title=str(asset.get("title") or asset.get("$id") or name),
                kind=obj.kind if obj else str(asset.get("x-kind") or "object"),
                parser=obj.parser if obj else "",
                origin=obj.origin if obj else "",
                folder=(graph.target((slug, name)) or {}).get("folder", ""),
                property_count=len(props),
                raw_fields=obj.fields if obj else 0,
                undescribed=sum(1 for p in props.values() if not _described(p)),
                described=_described(asset),
                modified=bool(obj and BADGE_MODIFIED in obj.badges),
                orphan=obj is None,
                property_names=tuple(
                    sorted({n for k, p in props.items() for n in (k, p.get("x-businessName")) if n})
                ),
            )
        )
    return out


@dataclass(frozen=True)
class PropertyRef:
    source: str
    path: str
    title: str
    kind: str  # field | list | map
    retained: bool  # la cible est un asset retenu (lien cliquable dans la Curation)


@dataclass(frozen=True)
class AssetProperty:
    name: str  # nom raw (clé de la propriété)
    business_name: str  # x-businessName, "" sinon
    type: str  # libellé du type pour un champ simple
    format: str
    max_length: int | None
    ref: PropertyRef | None
    external: str  # type externe non résolu (référence sortante hors de l'espace)
    description: str
    missing: bool  # retenu mais disparu du raw


@dataclass(frozen=True)
class AssetView:
    summary: AssetSummary
    description: str
    business_name: str
    properties: list[AssetProperty]


def _type_label(prop: dict[str, Any]) -> str:
    if "enum" in prop and not prop.get("type"):
        return "enum"
    kind = prop.get("type")
    if isinstance(kind, list):  # ["string", "null"]
        kind = next((k for k in kind if k != "null"), kind[0] if kind else "")
    if kind == "array" and isinstance(prop.get("items"), dict):
        inner = _type_label(prop["items"])
        return f"{inner}[]" if inner else "array"
    return str(kind or prop.get("x-kind") or "")


def asset_view(
    key: AssetKey,
    asset: dict[str, Any],
    assets: dict[AssetKey, dict[str, Any]],
    graph: ReferenceGraph,
) -> AssetView:
    summary = summarize({key: asset}, graph)[0]
    located = graph.located(key)
    refs: dict[str, SchemaRef] = {}
    raw_fields: set[str] | None = None
    if located is not None:
        obj = located[1]
        refs = {r.field: r for r in obj.refs if r.kind in _PROPERTY_REF_KINDS}
        raw_fields = set(obj.facts.field_names) if obj.facts.field_names else None
    props: list[AssetProperty] = []
    for name, prop in _properties(asset).items():
        ref: PropertyRef | None = None
        external = ""
        schema_ref = refs.get(name)
        if schema_ref is not None:
            target = graph.resolve(schema_ref)
            found = graph.located(target) if target else None
            if target and found:
                ref = PropertyRef(
                    source=target[0],
                    path=target[1],
                    title=found[1].title,
                    kind=schema_ref.kind,
                    retained=target in assets,
                )
            elif schema_ref.external:
                external = schema_ref.target
        max_length = prop.get("maxLength")
        props.append(
            AssetProperty(
                name=name,
                business_name=str(prop.get("x-businessName") or ""),
                type=_type_label(prop),
                format=str(prop.get("format") or ""),
                max_length=max_length if isinstance(max_length, int) else None,
                ref=ref,
                external=external,
                description=str(prop.get("x-description") or ""),
                missing=raw_fields is not None and name not in raw_fields,
            )
        )
    return AssetView(
        summary=summary,
        description=str(asset.get("x-description") or ""),
        business_name=str(asset.get("x-businessName") or ""),
        properties=props,
    )
