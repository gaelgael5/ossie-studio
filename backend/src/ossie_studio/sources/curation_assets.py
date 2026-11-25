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

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ossie_studio.governance.classification_resolve import (
    AssetClassification,
    RuleValue,
    classify,
    source_levels,
)
from ossie_studio.governance.repo import Scheme, read_schemes
from ossie_studio.sources.asset_files import read_asset_dir
from ossie_studio.sources.explorer import BADGE_MODIFIED, SchemaRef, list_source_slugs
from ossie_studio.sources.explorer_graph import ReferenceGraph
from ossie_studio.sources.paths import ASSETS_DIR, SOURCES_DIR, safe_assets_path
from ossie_studio.sources.referentiel_links import LinkTarget, issues, link_views, read_targets

AssetKey = tuple[str, str]  # (source, fichier du schéma)

# Les liens d'une propriété vers un autre objet (ni l'héritage, ni les clés
# étrangères SQL, qui ne portent pas de champ objet).
_PROPERTY_REF_KINDS = frozenset({"field", "list", "map"})


def read_assets(workdir: Path) -> dict[AssetKey, dict[str, Any]]:
    """Tous les assets retenus de la branche, par (source, fichier)."""
    out: dict[AssetKey, dict[str, Any]] = {}
    # Aussi les sources supprimées qui gardent leurs assets (ticket ce21a9e8) :
    # ils apparaissent orphelins, leur raw a disparu.
    base = workdir / SOURCES_DIR
    kept = (
        [p.name for p in base.iterdir() if p.is_dir() and (p / ASSETS_DIR).is_dir()]
        if base.is_dir()
        else []
    )
    for slug in sorted(set(list_source_slugs(workdir)) | set(kept)):
        try:
            directory = safe_assets_path(workdir, slug)
        except ValueError:
            continue
        for name, data in read_asset_dir(directory).items():
            out[(slug, name)] = data
    return out


def read_assets_and_schemes(
    workdir: Path,
) -> tuple[dict[AssetKey, dict[str, Any]], list[Scheme]]:
    """Assets et schémas de classification de la même vue (une seule lecture)."""
    return read_assets(workdir), read_schemes(workdir)


def read_curation_view(
    workdir: Path,
) -> tuple[dict[AssetKey, dict[str, Any]], list[Scheme], dict[str, LinkTarget]]:
    """Assets, schémas de classification et référentiels (liens des propriétés,
    ticket 8269c642) de la même vue."""
    return read_assets(workdir), read_schemes(workdir), read_targets(workdir)


def _described(node: dict[str, Any]) -> bool:
    return bool(str(node.get("x-description") or "").strip())


def _properties(asset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    props = asset.get("properties")
    if not isinstance(props, dict):
        return {}
    return {k: v for k, v in props.items() if isinstance(v, dict)}


@dataclass(frozen=True)
class ClassBadge:
    """Badge de classification effectif de l'asset pour un schéma (en-tête,
    liste). Ticket 5e8680d7."""

    scheme: str
    values: tuple[str, ...]
    declared: bool
    extra: int


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
    # Classification effective de l'asset (un badge par schéma ayant une valeur,
    # dans l'ordre des schémas) et tous les niveaux effectifs, champs compris,
    # sous la forme `schéma/niveau` : le filtre de la page s'appuie dessus
    # (critère 8 : un asset apparaît si l'un de ses niveaux correspond).
    classification: tuple[ClassBadge, ...] = ()
    class_levels: tuple[str, ...] = ()
    # Liens de référentiel à reprendre (partiels ou cassés), ticket 8269c642.
    referentiel_issues: int = 0


# Valeurs posées par règle sur les champs raw d'un asset : champ → schéma → valeur.
RuleValues = dict[str, dict[str, RuleValue]]


def rule_values(graph: ReferenceGraph, key: AssetKey) -> RuleValues:
    """Classifications posées par règle sur les champs de l'objet raw d'un asset
    (ticket 568a0547) : lues dans l'index de l'explorateur, où elles sont déjà
    calculées. Vide si l'objet raw a disparu."""
    located = graph.located(key)
    if located is None:
        return {}
    out: RuleValues = {}
    for c in located[1].field_classes:
        out.setdefault(c.field, {})[c.scheme] = RuleValue(c.values, c.rule, c.rule_name)
    return out


def _class_fields(
    asset: dict[str, Any], schemes: Sequence[Scheme], rules: RuleValues | None = None
) -> dict[str, Any]:
    if not schemes:
        return {}
    c = classify(asset, schemes, rules)
    return {
        "classification": tuple(
            ClassBadge(scheme=v.scheme, values=v.values, declared=v.declared, extra=v.extra)
            for v in c.object
            if v.values
        ),
        "class_levels": tuple(sorted(f"{sc}/{lv}" for sc, lv in c.levels)),
    }


def summarize(
    assets: dict[AssetKey, dict[str, Any]],
    graph: ReferenceGraph,
    schemes: Sequence[Scheme] = (),
    targets: Mapping[str, LinkTarget] | None = None,
) -> list[AssetSummary]:
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
                **_class_fields(asset, schemes, rule_values(graph, (slug, name))),
                referentiel_issues=issues(asset, targets) if targets is not None else 0,
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
    # Lien vers une propriété de référentiel (ticket 8269c642), None sinon.
    referentiel: dict[str, Any] | None = None


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
    targets: Mapping[str, LinkTarget] | None = None,
) -> AssetView:
    summary = summarize({key: asset}, graph, (), targets)[0]
    links = link_views(asset, targets or {})
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
                referentiel=asdict(links[name]) if name in links else None,
            )
        )
    return AssetView(
        summary=summary,
        description=str(asset.get("x-description") or ""),
        business_name=str(asset.get("x-businessName") or ""),
        properties=props,
    )


@dataclass(frozen=True)
class InvalidClassification:
    """Une valeur de classification portée par un asset et qui ne correspond à
    aucun niveau (ou schéma) : sans badge, listée dans Gouvernance (ticket
    5e8680d7, critère 6)."""

    source: str
    path: str
    title: str
    field: str  # "" pour l'objet
    scheme: str
    value: str
    reason: str  # unknown_scheme | unknown_level | not_single


def invalid_classifications(workdir: Path) -> list[InvalidClassification]:
    """Toutes les valeurs invalides de la vue, triées (source, asset, champ)."""
    assets, schemes = read_assets_and_schemes(workdir)
    out: list[InvalidClassification] = []
    for (slug, name), asset in sorted(assets.items()):
        title = str(asset.get("title") or asset.get("$id") or name)
        for bad in classify(asset, schemes).invalid:
            out.append(
                InvalidClassification(
                    source=slug,
                    path=name,
                    title=title,
                    field=bad.field,
                    scheme=bad.scheme,
                    value=bad.value,
                    reason=bad.reason,
                )
            )
    return out


def source_classifications(
    workdir: Path, graph: ReferenceGraph | None = None
) -> dict[str, list[dict[str, object]]]:
    """Niveau de chaque source par schéma : le plus sensible de ses assets
    (l'union pour un multi-valeurs) — ticket 5e8680d7, critère 9. Schémas sans
    valeur absents ; liste dans l'ordre des schémas. `graph` (index de
    l'explorateur) : valeurs posées par règle sur les champs (ticket 568a0547)."""
    assets, schemes = read_assets_and_schemes(workdir)
    by_source: dict[str, list[AssetClassification]] = {}
    for key, asset in assets.items():
        rules = rule_values(graph, key) if graph is not None else None
        by_source.setdefault(key[0], []).append(classify(asset, schemes, rules))
    out: dict[str, list[dict[str, object]]] = {}
    for slug, found in by_source.items():
        levels = source_levels(found, schemes)
        out[slug] = [
            {"scheme": sc.id, "values": list(levels[sc.id])} for sc in schemes if sc.id in levels
        ]
    return out
