"""Dataset manuel d'un domaine : créé et modifié à la main, sans lien avec raw
(ticket docflow 1c68a20a, epic « Modèle sémantique Ossie »).

Gael, 19/11/2026 : « Deux types de dataset. Les datasets qui dérivent d'un raw
[...] et ceux que l'on construit à la main. » Un dataset manuel est un `Dataset`
ordinaire, `derived: false` et sans asset : aucun format nouveau.

- **consultable** (décision du 24/11 : on garde oui / non) : `source` saisi (table,
  vue ou requête SQL), le dataset va dans `<nom>.ossie.yaml` ; sinon virtuel, dans
  `<nom>.virtuel.ossie.yaml`, avec `source: manuel:<domaine>/<dataset>` écrit ici.
- un champ d'un dataset virtuel a pour expression son nom (rien à saisir) ;
- le type d'un champ est facultatif et pris dans `DATATYPES` (Ossie 0.3), dans le
  `.extend.yaml` ;
- renommer un champ ou le dataset fait suivre relations, références et clés ;
  supprimer un champ retire les liens qui l'utilisent. Ces effets sont d'abord
  listés (`impacts`) : l'écran les montre avant de confirmer.

Fonctions pures, appelées par l'API dans le brouillon de la personne.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ossie_studio.domains.documents import (
    DATATYPES,
    Dataset,
    Domain,
    DomainError,
    DomainField,
    check_domain,
)

MANUAL_SOURCE_PREFIX = "manuel:"


def manual_source(domain: str, dataset: str) -> str:
    """`source` d'un dataset manuel virtuel (Gael, 24/11), sur le modèle de
    `raw:<source>/<fichier>` des datasets virtuels tirés de raw."""
    return f"{MANUAL_SOURCE_PREFIX}{domain}/{dataset}"


@dataclass
class FieldInput:
    name: str
    expression: str = ""
    datatype: str = ""
    description: str = ""
    label: str = ""
    # Nom du champ avant modification : c'est ce qui distingue un renommage
    # (les liens suivent) d'une suppression suivie d'un ajout (les liens tombent).
    previous: str = ""


@dataclass
class DatasetInput:
    name: str
    consultable: bool = True
    source: str = ""
    description: str = ""
    fields: list[FieldInput] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    unique_keys: list[list[str]] = field(default_factory=list)


@dataclass(frozen=True)
class Impact:
    """Un lien du domaine touché par la modification : `follows` (renommage
    suivi) ou `removed` (le champ ou le dataset qu'il utilise disparaît)."""

    kind: str  # relationship | reference
    name: str
    effect: str  # follows | removed


def _build(domain: Domain, data: DatasetInput) -> Dataset:
    name = data.name.strip()
    if not name or "/" in name:
        # « / » sépare domaine et dataset dans `manuel:<domaine>/<dataset>`.
        raise DomainError("dataset_name_invalid", name)
    source = data.source.strip()
    if data.consultable and not source:
        raise DomainError("dataset_source_required", name)
    fields: list[DomainField] = []
    seen: set[str] = set()
    for f in data.fields:
        fname = f.name.strip()
        if not fname:
            raise DomainError("field_name_required", name)
        if fname in seen:
            raise DomainError("field_name_taken", fname)
        seen.add(fname)
        # Virtuel : l'expression est le nom du champ (ticket) ; consultable : nom
        # de colonne ou expression SQL, obligatoire dans la spécification Ossie.
        expression = f.expression.strip() if data.consultable else fname
        if not expression:
            raise DomainError("field_expression_required", fname)
        if f.datatype and f.datatype not in DATATYPES:
            raise DomainError("field_datatype_invalid", f"{fname} : {f.datatype}")
        fields.append(
            DomainField(
                name=fname,
                expression=expression,
                description=f.description.strip(),
                label=f.label.strip(),
                datatype=f.datatype,
            )
        )
    for key in [data.primary_key, *data.unique_keys]:
        missing = next((k for k in key if k not in seen), None)
        if missing is not None:
            raise DomainError("key_field_missing", missing)
    return Dataset(
        name=name,
        source=source if data.consultable else manual_source(domain.name, name),
        consultable=data.consultable,
        derived=False,
        description=data.description.strip(),
        fields=fields,
        primary_key=list(data.primary_key),
        unique_keys=[list(k) for k in data.unique_keys if k],
    )


def _renames(old: Dataset | None, data: DatasetInput) -> tuple[dict[str, str], set[str]]:
    """(ancien nom → nouveau nom) des champs renommés, et champs supprimés."""
    if old is None:
        return {}, set()
    before = {f.name for f in old.fields}
    renamed = {
        f.previous: f.name.strip()
        for f in data.fields
        if f.previous and f.previous in before and f.previous != f.name.strip()
    }
    # Un `previous` inconnu (écran resté sur un état ancien) retombe sur le nom :
    # un champ déjà renommé n'est pas pris pour une suppression.
    kept = {f.previous if f.previous in before else f.name.strip() for f in data.fields}
    return renamed, before - kept


def _apply(
    domain: Domain, old_name: str | None, new: Dataset, renamed: dict[str, str], removed: set[str]
) -> list[Impact]:
    """Fait suivre les liens du domaine ; rend ce qui a été touché."""
    impacts: list[Impact] = []
    ds_renamed = old_name is not None and old_name != new.name

    def column(ds: str, col: str) -> str:
        return renamed.get(col, col) if ds == old_name else col

    kept_relations = []
    for r in domain.relationships:
        from_mine, to_mine = r.from_dataset == old_name, r.to_dataset == old_name
        lost = (from_mine and set(r.from_columns) & removed) or (
            to_mine and set(r.to_columns) & removed
        )
        if lost:
            impacts.append(Impact("relationship", r.name, "removed"))
            continue
        touched = (from_mine or to_mine) and (
            ds_renamed
            or (from_mine and any(c in renamed for c in r.from_columns))
            or (to_mine and any(c in renamed for c in r.to_columns))
        )
        r.from_columns = [column(r.from_dataset, c) for c in r.from_columns]
        r.to_columns = [column(r.to_dataset, c) for c in r.to_columns]
        if from_mine:
            r.from_dataset = new.name
        if to_mine:
            r.to_dataset = new.name
        if touched:
            impacts.append(Impact("relationship", r.name, "follows"))
        kept_relations.append(r)
    domain.relationships = kept_relations

    kept_refs = []
    for ref in domain.references:
        if ref.from_dataset == old_name and ref.field in removed:
            impacts.append(Impact("reference", ref.name, "removed"))
            continue
        touched = (old_name in (ref.from_dataset, ref.to_dataset) and ds_renamed) or (
            ref.from_dataset == old_name and ref.field in renamed
        )
        if ref.from_dataset == old_name:
            ref.field = renamed.get(ref.field, ref.field)
            ref.from_dataset = new.name
        if ref.to_dataset == old_name:
            ref.to_dataset = new.name
        if touched:
            impacts.append(Impact("reference", ref.name, "follows"))
        kept_refs.append(ref)
    domain.references = kept_refs
    return impacts


def save_manual_dataset(
    domain: Domain, data: DatasetInput, current: str | None = None
) -> tuple[Dataset, list[Impact]]:
    """Crée (`current` None) ou remplace le dataset manuel `current` dans
    `domain`, liens suivis ; rend le dataset et les liens touchés. Le domaine est
    vérifié en entier (`check_domain`) : un passage consultable ↔ virtuel qui
    laisserait une relation entre deux documents est refusé en la nommant."""
    old: Dataset | None = None
    if current is not None:
        old = domain.dataset(current)
        if old is None:
            raise DomainError("dataset_not_found", current)
        if old.derived:
            # Interdit par le ticket : un dataset dérivé reste en lecture seule.
            raise DomainError("dataset_derived_readonly", current)
    new = _build(domain, data)
    if old is not None:
        # Le lien de référentiel d'un champ le suit quand il est renommé ; un champ
        # supprimé l'emporte, et son groupe de clé composée devient partiel, signalé
        # à l'écran (ticket 80bb56af, critère 4).
        before = {f.name: f.referentiel for f in old.fields}
        for f_in, f_new in zip(data.fields, new.fields, strict=True):
            f_new.referentiel = before.get(f_in.previous if f_in.previous in before else f_new.name)
    # Les clés suivent les renommages de champs saisis dans la même modification :
    # l'écran les envoie avec les nouveaux noms, rien à traduire ici.
    renamed, removed = _renames(old, data)
    impacts = _apply(domain, current, new, renamed, removed)
    if old is None:
        domain.datasets.append(new)
    else:
        domain.datasets[domain.datasets.index(old)] = new
    check_domain(domain)
    return new, impacts
