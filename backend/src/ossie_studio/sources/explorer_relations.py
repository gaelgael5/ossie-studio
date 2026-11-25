"""Relations virtuelles des sources SQL, calculées à la construction de l'index
(ticket b5f333b4, règles dans `governance/relation_profiles.py`).

Une relation virtuelle devient une référence `foreign_key` de l'objet, marquée
`virtual` (nom du profil) : flèches et « référencé par » des vignettes, onglet
« Référencé par », graphe et `get_reference_graph` la montrent sans rien
changer à leur code — c'est le même chemin que les clés étrangères déclarées.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from ossie_studio.governance.relation_profiles import RelationProfile, fill
from ossie_studio.sources.explorer import ExplorerObject, SchemaRef, SourceEntries

REF_FOREIGN_KEY = "foreign_key"


@dataclass(frozen=True)
class VirtualHit:
    file: str  # objet qui porte la colonne
    title: str
    field: str
    target_file: str
    target: str  # SCHEMA.TABLE
    column: str
    profile: str


@dataclass(frozen=True)
class Unresolved:
    file: str
    title: str
    field: str
    wanted: str  # table recherchée
    reason: str  # not_found | ambiguous | column_missing


def _schema_table(obj: ExplorerObject) -> tuple[str, str]:
    """`SCHEMA.TABLE` lu dans l'origine de l'objet (clé de la table de travail
    SQL) ; à défaut, le titre seul."""
    if "." in obj.origin:
        schema, table = obj.origin.split(".", 1)
        return schema, table
    return "", obj.title


class _Tables:
    def __init__(self, objects: Iterable[ExplorerObject]) -> None:
        self.by_key: dict[tuple[str, str], ExplorerObject] = {}
        self.by_name: dict[str, list[ExplorerObject]] = {}
        for obj in objects:
            schema, table = _schema_table(obj)
            self.by_key[(schema.casefold(), table.casefold())] = obj
            self.by_name.setdefault(table.casefold(), []).append(obj)

    def find(self, schema: str, table: str) -> tuple[ExplorerObject | None, str]:
        # Même schéma d'abord, puis toute la source ; plusieurs candidates ailleurs
        # → rien (une flèche fausse est pire qu'une flèche absente).
        same = self.by_key.get((schema.casefold(), table.casefold()))
        if same is not None:
            return same, ""
        others = self.by_name.get(table.casefold(), [])
        if len(others) == 1:
            return others[0], ""
        return None, "ambiguous" if others else "not_found"


def resolve_source(
    entries: SourceEntries, profiles: list[RelationProfile]
) -> tuple[list[VirtualHit], list[Unresolved]]:
    tables = _Tables(entries.objects)
    hits: list[VirtualHit] = []
    unresolved: list[Unresolved] = []
    compiled = [(p, r, r.regex()) for p in profiles for r in p.rules]
    for obj in entries.objects:
        # Seules les clés DÉCLARÉES l'emportent : les virtuelles déjà posées
        # dans l'index (aperçu d'un autre profil) ne masquent rien.
        declared = {r.field for r in obj.refs if r.kind == REF_FOREIGN_KEY and not r.virtual}
        schema, _table = _schema_table(obj)
        for field in obj.facts.field_names:
            if field in declared:
                continue  # une clé étrangère déclarée l'emporte
            missed: Unresolved | None = None
            for profile, rule, rx in compiled:
                if rx is None or (rule.kinds and obj.kind not in rule.kinds):
                    continue
                m = rx.fullmatch(field)
                if m is None:
                    continue
                groups = {k: v or "" for k, v in m.groupdict().items()}
                wanted, column = fill(rule.table, groups), fill(rule.target_column, groups)
                target, reason = tables.find(schema, wanted)
                if target is None:
                    missed = missed or Unresolved(obj.file, obj.title, field, wanted, reason)
                    continue
                if target.file == obj.file:
                    # La colonne cible elle-même (ACCOUNT_HUB.ACCOUNT_HK) n'est
                    # pas une référence vers sa propre table.
                    missed = None
                    break
                if column not in target.facts.field_names:
                    missed = missed or Unresolved(
                        obj.file, obj.title, field, f"{wanted}.{column}", "column_missing"
                    )
                    continue
                t_schema, t_table = _schema_table(target)
                hits.append(
                    VirtualHit(
                        obj.file,
                        obj.title,
                        field,
                        target.file,
                        f"{t_schema}.{t_table}" if t_schema else t_table,
                        column,
                        profile.name,
                    )
                )
                missed = None
                break
            if missed is not None:
                unresolved.append(missed)
    return hits, unresolved


def apply_relation_profiles(
    sources: Iterable[SourceEntries], chosen: Mapping[str, list[RelationProfile]]
) -> list[SourceEntries]:
    """Les sources avec, sur chaque objet, ses références virtuelles en plus de
    celles que son schéma déclare. Toujours recalculé en entier : une référence
    virtuelle d'un index relu ne survit pas à un profil retiré."""
    out: list[SourceEntries] = []
    for entries in sources:
        objects = [
            replace(o, refs=tuple(r for r in o.refs if not r.virtual)) for o in entries.objects
        ]
        entries = replace(entries, objects=objects)
        profiles = chosen.get(entries.slug) or []
        if not profiles:
            out.append(entries)
            continue
        hits, _ = resolve_source(entries, profiles)
        by_file: dict[str, list[SchemaRef]] = {}
        for h in hits:
            by_file.setdefault(h.file, []).append(
                SchemaRef(h.field, REF_FOREIGN_KEY, h.target, "0..1", False, h.profile, h.column)
            )
        out.append(
            replace(
                entries,
                objects=[
                    replace(o, refs=o.refs + tuple(by_file[o.file])) if o.file in by_file else o
                    for o in objects
                ],
            )
        )
    return out
