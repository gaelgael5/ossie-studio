"""D'un asset retenu à un dataset de domaine, et des références aux liens
(ticket docflow 36d35a88).

Gael, 23/11/2026 : « un asset donne un dataset ». Rien n'est saisi :
- **dataset dérivé** (lecture seule) : les propriétés retenues de l'asset sont
  ses champs ; consultable s'il vient d'une table ou d'une vue (`source` = nom
  qualifié, expression = colonne), virtuel sinon (`source: raw:<source>/<fichier>`,
  décision du 23/11 ; expression = nom de propriété) ;
- **liens** entre datasets du domaine, tirés du graphe de références de
  l'Explorateur raw : une **clé étrangère** SQL entre deux datasets du même
  document Ossie devient une **relation** Ossie ; une **référence d'objet** (une
  propriété de classe qui pointe vers une autre classe, sans colonne de clé)
  devient une **référence** du `.extend.yaml` — Ossie 0.1.1 ne sait l'exprimer
  qu'avec des colonnes de clé.

Fonctions pures : l'asset est la vue en mémoire (`sources/asset_files.py`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ossie_studio.domains.documents import (
    AssetRef,
    Dataset,
    Domain,
    DomainField,
    Reference,
    Relationship,
)
from ossie_studio.parsers.conventions import schema_key
from ossie_studio.sources.explorer_graph import ReferenceGraph
from ossie_studio.sources.referentiel_links import link_of

AssetKey = tuple[str, str]  # (source, fichier de l'asset)

CONSULTABLE_KINDS = frozenset({"table", "view"})
_OBJECT_LINKS = frozenset({"field", "list", "map"})


# Horodatages AVEC fuseau ou décalage (ticket 9bfa21d7) : `date-time` de JSON
# Schema ne le dit pas, le type natif si. Types SQL lus dans `x-db-type` (posé par
# les extracteurs), en minuscules et sans précision ; types de code dans
# `x-code-type` (posé par les parseurs C# et Kotlin/Java pour ces seuls types).
# `timestamp`, `TIMESTAMP_NTZ`, `datetime2`, `DateTime`, `LocalDateTime` restent
# `DateTime`. `Instant` (Java) aussi : le ticket ne le range pas, à trancher.
_TZ_DB_TYPES = frozenset(
    {"timestamptz", "timestamp with time zone", "timestamp_tz", "timestamp_ltz", "datetimeoffset"}
)
_TZ_CODE_TYPES = frozenset({"DateTimeOffset", "OffsetDateTime", "ZonedDateTime"})


def _with_zone(prop: dict[str, Any]) -> bool:
    # « timestamp(3) with time zone » (PostgreSQL), « TIMESTAMP_TZ(9) » : précision ôtée.
    db_type = " ".join(re.sub(r"\([^)]*\)", "", str(prop.get("x-db-type") or "")).lower().split())
    return db_type in _TZ_DB_TYPES or str(prop.get("x-code-type") or "") in _TZ_CODE_TYPES


def datatype_of(prop: dict[str, Any]) -> str:
    """Type Ossie-studio d'un champ (dans le `.extend.yaml` : Ossie 0.1.1 n'a pas
    de type de champ), déduit du type raw, parmi `DATATYPES`."""
    kind = prop.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "")
    fmt = str(prop.get("format") or "")
    if kind == "string":
        if fmt == "date-time":
            return "DateTimeTz" if _with_zone(prop) else "DateTime"
        return {"date": "Date", "time": "Time"}.get(fmt, "String")
    if kind == "integer":
        return "Integer"
    if kind == "number":
        return "Float" if fmt in ("float", "double") else "Decimal"
    if kind == "boolean":
        return "Boolean"
    return "Opaque"


def is_consultable(asset: dict[str, Any]) -> bool:
    return str(asset.get("x-kind") or "") in CONSULTABLE_KINDS


def _properties(asset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    props = asset.get("properties")
    if not isinstance(props, dict):
        return {}
    return {k: v for k, v in props.items() if isinstance(v, dict)}


def dataset_name(asset: dict[str, Any], key: AssetKey, taken: set[str]) -> str:
    """Nom du dataset : le titre de l'asset ; suffixé par la source s'il est déjà
    pris dans le domaine (deux `Commande` de deux sources)."""
    base = str(asset.get("title") or schema_key(key[1]) or key[1])
    if base not in taken:
        return base
    candidate, n = f"{base}_{key[0]}", 2
    while candidate in taken:
        candidate, n = f"{base}_{key[0]}_{n}", n + 1
    return candidate


def dataset_from_asset(key: AssetKey, asset: dict[str, Any], name: str) -> Dataset:
    consultable = is_consultable(asset)
    props = _properties(asset)
    fields = [
        DomainField(
            name=prop_name,
            expression=prop_name,
            description=str(prop.get("x-description") or prop.get("description") or ""),
            label=str(prop.get("x-businessName") or ""),
            datatype=datatype_of(prop),
            property=prop_name,
            # Hérité de l'asset (ticket 65235f64), tenu à jour par `domains.links`.
            referentiel=link_of(prop),
        )
        for prop_name, prop in props.items()
    ]
    names = set(props)

    def keep(columns: Iterable[Any]) -> list[str]:
        cols = [str(c) for c in columns]
        return cols if cols and all(c in names for c in cols) else []

    # Clé primaire / unicité reprises de raw seulement quand toutes leurs
    # colonnes sont des champs du dataset.
    primary = keep(asset.get("x-primary-key") or [])
    uniques = [k for k in (keep(u) for u in asset.get("x-unique-keys") or []) if k]
    return Dataset(
        name=name,
        source=schema_key(key[1]) if consultable else f"raw:{key[0]}/{key[1]}",
        consultable=consultable,
        derived=True,
        asset=AssetRef(source=key[0], path=key[1]),
        description=str(asset.get("x-description") or asset.get("description") or ""),
        fields=fields,
        primary_key=primary,
        unique_keys=uniques,
    )


@dataclass(frozen=True)
class Link:
    """Lien proposé entre deux datasets, avant écriture (fenêtre de confirmation)."""

    id: str
    kind: str  # relationship | reference
    from_dataset: str
    to_dataset: str
    field: str
    ref_kind: str  # field | list | map | foreign_key
    cardinality: str
    to_column: str = ""


def _cardinality(ref_kind: str) -> str:
    # Côté de l'objet qui porte le champ : un champ (ou une clé étrangère) vise
    # un seul objet ; une liste ou un dictionnaire en vise plusieurs.
    return "1:n" if ref_kind in ("list", "map") else "n:1"


def _fk_column(prop: dict[str, Any], target_key: AssetKey) -> str:
    refs = prop.get("x-references")
    entries = refs if isinstance(refs, list) else [refs] if isinstance(refs, dict) else []
    wanted = schema_key(target_key[1]).casefold()
    for entry in entries:
        table = str(entry.get("table") or "").casefold()
        if table in (wanted, wanted.replace("-", "_")) or table.replace("_", "-") == wanted:
            return str(entry.get("column") or "")
    return str(entries[0].get("column") or "") if len(entries) == 1 else ""


def deduce_links(
    datasets: dict[AssetKey, Dataset],
    assets: dict[AssetKey, dict[str, Any]],
    graph: ReferenceGraph,
) -> list[Link]:
    """Liens entre les datasets donnés (clés = assets d'origine), déduits du
    graphe de références. Seuls les champs **retenus** comptent : une propriété
    non retenue n'est pas un champ du dataset."""
    out: list[Link] = []
    for key, ds in sorted(datasets.items()):
        located = graph.located(key)
        if located is None:
            continue
        field_names = {f.name for f in ds.fields}
        props = _properties(assets.get(key) or {})
        for ref in located[1].refs:
            if ref.field not in field_names:
                continue
            target_key = graph.resolve(ref)
            target = datasets.get(target_key) if target_key else None
            if target is None or target_key is None:
                continue
            if ref.kind == "foreign_key":
                column = _fk_column(props.get(ref.field) or {}, target_key)
                if (
                    not column
                    or column not in {f.name for f in target.fields}
                    or ds.consultable != target.consultable
                ):
                    continue
                out.append(
                    Link(
                        id=f"rel:{ds.name}.{ref.field}->{target.name}.{column}",
                        kind="relationship",
                        from_dataset=ds.name,
                        to_dataset=target.name,
                        field=ref.field,
                        ref_kind=ref.kind,
                        cardinality="n:1",
                        to_column=column,
                    )
                )
            elif ref.kind in _OBJECT_LINKS:
                out.append(
                    Link(
                        id=f"ref:{ds.name}.{ref.field}->{target.name}",
                        kind="reference",
                        from_dataset=ds.name,
                        to_dataset=target.name,
                        field=ref.field,
                        ref_kind=ref.kind,
                        cardinality=_cardinality(ref.kind),
                    )
                )
    return out


def apply_link(domain: Domain, link: Link) -> None:
    """Écrit un lien dans le domaine (sans doublon de nom)."""
    name = f"{link.from_dataset}_{link.field}"
    if link.kind == "relationship":
        if any(r.name == name for r in domain.relationships):
            return
        domain.relationships.append(
            Relationship(
                name=name,
                from_dataset=link.from_dataset,
                to_dataset=link.to_dataset,
                from_columns=[link.field],
                to_columns=[link.to_column],
            )
        )
    else:
        if any(r.name == name for r in domain.references):
            return
        domain.references.append(
            Reference(
                name=name,
                from_dataset=link.from_dataset,
                field=link.field,
                to_dataset=link.to_dataset,
                kind=link.ref_kind,
                cardinality=link.cardinality,
            )
        )


@dataclass
class AddPlan:
    """Ce qu'un ajout d'assets va faire (fenêtre de confirmation)."""

    added: list[Dataset]  # nouveaux datasets
    already: list[str]  # assets déjà dans le domaine (non dupliqués)
    links: list[Link]  # liens déduits touchant au moins un nouveau dataset
    ignored: list[dict[str, str]]  # références vers des objets non retenus


def expand_with_references(
    keys: list[AssetKey], assets: dict[AssetKey, dict[str, Any]], graph: ReferenceGraph
) -> tuple[list[AssetKey], list[dict[str, str]]]:
    """Les assets donnés et tous les assets **retenus** qu'ils référencent,
    récursivement ; les objets référencés non retenus sont rendus à part."""
    out: list[AssetKey] = list(dict.fromkeys(keys))
    ignored: dict[AssetKey, dict[str, str]] = {}
    for key in keys:
        for child in graph.descendants(key):
            child_key = (child["slug"], child["file"])
            if child_key in assets:
                if child_key not in out:
                    out.append(child_key)
            else:
                ignored[child_key] = {
                    "source": child["slug"],
                    "path": child["file"],
                    "title": child["title"],
                }
    return out, [ignored[k] for k in sorted(ignored)]


def plan_add(
    domain: Domain,
    keys: list[AssetKey],
    assets: dict[AssetKey, dict[str, Any]],
    graph: ReferenceGraph,
    with_references: bool,
) -> AddPlan:
    ignored: list[dict[str, str]] = []
    if with_references:
        keys, ignored = expand_with_references(keys, assets, graph)
    present = {(d.asset.source, d.asset.path): d for d in domain.datasets if d.asset}
    taken = {d.name for d in domain.datasets}
    added: list[Dataset] = []
    already: list[str] = []
    for key in keys:
        if key in present:
            already.append(present[key].name)
            continue
        asset = assets.get(key)
        if asset is None:
            continue  # non retenu : le service le refuse avant
        ds = dataset_from_asset(key, asset, dataset_name(asset, key, taken))
        taken.add(ds.name)
        added.append(ds)
    all_datasets = {**present, **{(d.asset.source, d.asset.path): d for d in added if d.asset}}
    new_names = {d.name for d in added}
    links = [
        link
        for link in deduce_links(all_datasets, assets, graph)
        if link.from_dataset in new_names or link.to_dataset in new_names
    ]
    return AddPlan(added=added, already=already, links=links, ignored=ignored)


def apply_plan(domain: Domain, plan: AddPlan, accepted: set[str] | None) -> None:
    """Ajoute les datasets du plan et les liens acceptés (`None` : tous, comme
    proposés cochés). Rien n'est écrit sans confirmation : c'est l'appel même."""
    domain.datasets.extend(plan.added)
    for link in plan.links:
        if accepted is None or link.id in accepted:
            apply_link(domain, link)


def remove_dataset(domain: Domain, name: str) -> bool:
    before = len(domain.datasets)
    domain.datasets = [d for d in domain.datasets if d.name != name]
    if len(domain.datasets) == before:
        return False
    domain.relationships = [
        r for r in domain.relationships if name not in (r.from_dataset, r.to_dataset)
    ]
    domain.references = [r for r in domain.references if name not in (r.from_dataset, r.to_dataset)]
    return True
