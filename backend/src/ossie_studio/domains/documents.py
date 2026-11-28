"""Documents d'un domaine (ticket docflow bfae2d97).

Gael, 23/11/2026 : un **domaine** = un **modèle sémantique Ossie**. Pour un
domaine `<nom>`, dans `domaines/` à la racine du dépôt de l'espace :

| Fichier | Contenu |
|---|---|
| `<nom>.ossie.yaml` | datasets consultables (table, vue), relations — Ossie 0.1.1 strict |
| `<nom>.virtuel.ossie.yaml` | datasets non consultables (classe de code, contrat…) — même forme |
| `<nom>.extend.yaml` | ce qui sort de la norme (asset, consultable, dérivé, types, références) |

« La priorité est bien de garder les standards non altérés » (Gael) : aucune
donnée propre à Ossie-studio dans un document Ossie, pas de `custom_extensions`.
Chaque document Ossie est validé contre le JSON Schema officiel embarqué
(`schemas/osi-schema-0.1.1.json`) avant d'être écrit, et à la lecture.

Le domaine existe dès que son `.extend.yaml` existe : Ossie exige au moins un
dataset par document, un domaine vide n'a donc aucun document Ossie.

Ce module est la **seule** porte d'écriture des fichiers d'un domaine.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ossie_studio.parsers.conventions import SchemaReadError, dump_schema, load_schema
from ossie_studio.roundtrip import write_document

DOMAINS_DIR = "domaines"
OSSIE_VERSION = "0.1.1"
OSSIE_SUFFIX = ".ossie.yaml"
VIRTUAL_SUFFIX = ".virtuel.ossie.yaml"
EXTEND_SUFFIX = ".extend.yaml"
EXTEND_VERSION = 1
# Dialecte des expressions : le nom de la colonne (consultable) ou de la
# propriété (virtuel) ; Ossie exige au moins une expression par champ.
DIALECT = "ANSI_SQL"

_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SCHEMA_FILE = Path(__file__).parent / "schemas" / "osi-schema-0.1.1.json"


class DomainError(ValueError):
    """Domaine refusé (nom, document invalide, incohérence). `code` est stable."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def is_valid_domain_name(name: str) -> bool:
    return bool(name) and len(name) <= 64 and bool(_NAME.match(name))


# --- Modèle en mémoire ----------------------------------------------------------


@dataclass(frozen=True)
class AssetRef:
    """L'asset d'origine d'un dataset dérivé : `sources/<source>/assets/<path>`."""

    source: str
    path: str


@dataclass
class DomainField:
    name: str
    expression: str  # nom de colonne (consultable) ou de propriété (virtuel)
    description: str = ""
    label: str = ""
    datatype: str = ""  # extension : Ossie 0.1.1 n'a pas de type de champ
    property: str = ""  # extension : propriété de l'asset d'origine
    # Extension (tickets 65235f64, 80bb56af) : lien vers une propriété de
    # référentiel, `{schema, property, group?}` comme sur un asset. Ossie n'a pas
    # cette notion : rangé dans le `.extend.yaml`, jamais dans le document Ossie.
    referentiel: dict[str, str] | None = None


@dataclass
class Dataset:
    name: str
    source: str
    consultable: bool
    derived: bool
    asset: AssetRef | None = None
    description: str = ""
    fields: list[DomainField] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    unique_keys: list[list[str]] = field(default_factory=list)


@dataclass
class Relationship:
    """Relation Ossie : clé étrangère `from_columns` (côté plusieurs) → `to_columns`."""

    name: str
    from_dataset: str
    to_dataset: str
    from_columns: list[str]
    to_columns: list[str]


@dataclass
class Reference:
    """Lien d'objet entre deux datasets (une propriété qui pointe vers un autre
    objet), sans colonne de clé : inexprimable en relation Ossie 0.1.1, il vit
    dans le `.extend.yaml`."""

    name: str
    from_dataset: str
    field: str
    to_dataset: str
    kind: str  # field | list | map
    cardinality: str = ""  # « n:1 », « 1:n »… (affichage)


@dataclass
class Domain:
    name: str  # slug : nom des fichiers et `name` du modèle Ossie
    description: str = ""
    # Libellé libre saisi à l'écran (règle libellé + slug de l'application) ;
    # hors norme Ossie, donc dans le `.extend.yaml`. Vide : on affiche le slug.
    label: str = ""
    datasets: list[Dataset] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # lecture : incohérences signalées

    def dataset(self, name: str) -> Dataset | None:
        return next((d for d in self.datasets if d.name == name), None)


# --- Schémas --------------------------------------------------------------------


@cache
def _ossie_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(_SCHEMA_FILE.read_text(encoding="utf-8")))


_STR = {"type": "string"}

# Types de champ d'Ossie-studio (ticket 9bfa21d7, Gael, 24/11/2026) : EXACTEMENT
# `$defs/DataType` d'Ossie 0.3 (`core-spec/ossie-schema.json`, apache/ossie),
# mêmes noms, même ordre. Ossie 0.1.1, celle qu'on écrit, n'a pas de type de
# champ : il vit dans `<nom>.extend.yaml`, et passera dans le document Ossie sans
# conversion le jour où l'on adopte la 0.3. Seule liste du code : ne pas en
# écrire une seconde, ni y ajouter un type absent d'Ossie 0.3.
DATATYPES: tuple[str, ...] = (
    "String",
    "Integer",
    "Decimal",
    "Float",
    "Boolean",
    "Date",
    "Time",
    "DateTime",
    "DateTimeTz",
    "Opaque",
)
_STR_LIST = {"type": "array", "items": _STR}

EXTEND_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Ossie-studio — extension d'un domaine",
    "type": "object",
    "required": ["version", "name"],
    "additionalProperties": False,
    "properties": {
        "version": {"const": EXTEND_VERSION},
        "name": _STR,
        "label": _STR,
        "description": _STR,
        "datasets": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "additionalProperties": False,
                "required": ["consultable", "derived"],
                "properties": {
                    "consultable": {"type": "boolean"},
                    "derived": {"type": "boolean"},
                    "asset": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["source", "path"],
                        "properties": {"source": _STR, "path": _STR},
                    },
                    "fields": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "property": _STR,
                                "datatype": {"enum": list(DATATYPES)},
                                "referentiel": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["schema", "property"],
                                    "properties": {"schema": _STR, "property": _STR, "group": _STR},
                                },
                            },
                        },
                    },
                },
            },
        },
        "references": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "from", "field", "to", "kind"],
                "properties": {
                    "name": _STR,
                    "from": _STR,
                    "field": _STR,
                    "to": _STR,
                    "kind": {"enum": ["field", "list", "map"]},
                    "cardinality": _STR,
                },
            },
        },
    },
}


@cache
def _extend_validator() -> Draft202012Validator:
    return Draft202012Validator(EXTEND_SCHEMA)


def _check(validator: Draft202012Validator, doc: Any, code: str) -> None:
    error = next(iter(sorted(validator.iter_errors(doc), key=lambda e: list(e.path))), None)
    if error is not None:
        where = "/".join(str(p) for p in error.absolute_path) or "(racine)"
        raise DomainError(code, f"{where} : {error.message}")


# --- Écriture -------------------------------------------------------------------


def _ossie_dataset(ds: Dataset) -> dict[str, Any]:
    out: dict[str, Any] = {"name": ds.name, "source": ds.source}
    if ds.description:
        out["description"] = ds.description
    if ds.primary_key:
        out["primary_key"] = list(ds.primary_key)
    if ds.unique_keys:
        out["unique_keys"] = [list(k) for k in ds.unique_keys]
    if ds.fields:
        out["fields"] = [_ossie_field(f) for f in ds.fields]
    return out


def _ossie_field(f: DomainField) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": f.name,
        "expression": {"dialects": [{"dialect": DIALECT, "expression": f.expression}]},
    }
    if f.label:
        out["label"] = f.label
    if f.description:
        out["description"] = f.description
    return out


def _ossie_document(domain: Domain, consultable: bool) -> dict[str, Any] | None:
    datasets = [d for d in domain.datasets if d.consultable == consultable]
    if not datasets:
        return None
    names = {d.name for d in datasets}
    relations = [
        r for r in domain.relationships if r.from_dataset in names and r.to_dataset in names
    ]
    model: dict[str, Any] = {"name": domain.name, "datasets": [_ossie_dataset(d) for d in datasets]}
    if domain.description:
        model["description"] = domain.description
    if relations:
        model["relationships"] = [
            {
                "name": r.name,
                "from": r.from_dataset,
                "to": r.to_dataset,
                "from_columns": list(r.from_columns),
                "to_columns": list(r.to_columns),
            }
            for r in relations
        ]
    return {"version": OSSIE_VERSION, "semantic_model": [model]}


def _extend_document(domain: Domain) -> dict[str, Any]:
    datasets: dict[str, Any] = {}
    for ds in domain.datasets:
        entry: dict[str, Any] = {"consultable": ds.consultable, "derived": ds.derived}
        if ds.asset is not None:
            entry["asset"] = {"source": ds.asset.source, "path": ds.asset.path}
        fields = {
            f.name: {
                k: v
                for k, v in (
                    ("property", f.property),
                    ("datatype", f.datatype),
                    ("referentiel", f.referentiel),
                )
                if v
            }
            for f in ds.fields
            if f.property or f.datatype or f.referentiel
        }
        if fields:
            entry["fields"] = fields
        datasets[ds.name] = entry
    doc: dict[str, Any] = {"version": EXTEND_VERSION, "name": domain.name}
    if domain.label:
        doc["label"] = domain.label
    if domain.description:
        doc["description"] = domain.description
    if datasets:
        doc["datasets"] = datasets
    if domain.references:
        doc["references"] = [
            {
                k: v
                for k, v in (
                    ("name", r.name),
                    ("from", r.from_dataset),
                    ("field", r.field),
                    ("to", r.to_dataset),
                    ("kind", r.kind),
                    ("cardinality", r.cardinality),
                )
                if v
            }
            for r in domain.references
        ]
    return doc


def check_domain(domain: Domain) -> None:
    """Cohérence interne avant écriture : noms uniques, liens vers des datasets
    et des champs existants. Lève DomainError."""
    if not is_valid_domain_name(domain.name):
        raise DomainError("domain_name_invalid", domain.name)
    names = [d.name for d in domain.datasets]
    duplicate = next((n for n in names if names.count(n) > 1), None)
    if duplicate:
        raise DomainError("dataset_name_taken", duplicate)
    by_name = {d.name: d for d in domain.datasets}
    for r in domain.relationships:
        src, dst = by_name.get(r.from_dataset), by_name.get(r.to_dataset)
        if src is None or dst is None:
            raise DomainError("relationship_dataset_missing", r.name)
        if src.consultable != dst.consultable:
            # Question ouverte 4 de l'epic : un consultable et un virtuel ne sont
            # pas dans le même document Ossie ; pas de relation entre eux.
            raise DomainError("relationship_cross_document", r.name)
        if len(r.from_columns) != len(r.to_columns) or not r.from_columns:
            raise DomainError("relationship_columns_mismatch", r.name)
        # Les colonnes désignent des champs déclarés de leur dataset (ticket
        # 4e856094) : une relation vers un champ absent rendrait le modèle faux.
        for ds, columns in ((src, r.from_columns), (dst, r.to_columns)):
            declared = {f.name for f in ds.fields}
            missing = next((c for c in columns if c not in declared), None)
            if missing is not None:
                raise DomainError("relationship_column_unknown", f"{ds.name}.{missing}")
    for kind, links in (("relationship", domain.relationships), ("reference", domain.references)):
        seen = [link.name for link in links]
        duplicate = next((n for n in seen if seen.count(n) > 1), None)
        if duplicate:
            raise DomainError(f"{kind}_name_taken", duplicate)
    for ref in domain.references:
        if ref.from_dataset not in by_name or ref.to_dataset not in by_name:
            raise DomainError("reference_dataset_missing", ref.name)


def domain_documents(domain: Domain) -> dict[str, dict[str, Any] | None]:
    """Document de chaque fichier du domaine (None : fichier à supprimer), validé.
    Clés : noms de fichiers relatifs à `domaines/`. Le document Ossie et le
    `.extend.yaml` restent deux fichiers distincts : jamais fusionnés sur disque."""
    check_domain(domain)
    out: dict[str, dict[str, Any] | None] = {}
    for suffix, consultable in ((OSSIE_SUFFIX, True), (VIRTUAL_SUFFIX, False)):
        doc = _ossie_document(domain, consultable)
        if doc is not None:
            _check(_ossie_validator(), doc, "ossie_invalid")
        out[f"{domain.name}{suffix}"] = doc
    extend = _extend_document(domain)
    _check(_extend_validator(), extend, "extend_invalid")
    out[f"{domain.name}{EXTEND_SUFFIX}"] = extend
    return out


def render_domain(domain: Domain) -> dict[str, str | None]:
    """Texte neuf de chaque fichier du domaine (None : fichier à supprimer)."""
    return {
        name: dump_schema(doc) if doc is not None else None
        for name, doc in domain_documents(domain).items()
    }


def write_domain(root: Path, domain: Domain) -> None:
    """Écrit les fichiers du domaine dans la copie `root` (le brouillon)."""
    documents = domain_documents(domain)
    base = root / DOMAINS_DIR
    base.mkdir(parents=True, exist_ok=True)
    for name, doc in documents.items():
        target = base / name
        if doc is None:
            target.unlink(missing_ok=True)
        else:
            # Aller-retour (14bb8eb8) : seules les lignes changées bougent.
            write_document(target, doc, dump_schema)


def delete_domain(root: Path, name: str) -> bool:
    if not is_valid_domain_name(name):
        return False
    base = root / DOMAINS_DIR
    found = False
    for suffix in (OSSIE_SUFFIX, VIRTUAL_SUFFIX, EXTEND_SUFFIX):
        target = base / f"{name}{suffix}"
        if target.is_file():
            target.unlink()
            found = True
    return found


# --- Lecture --------------------------------------------------------------------


def _load(file: Path) -> Any:
    try:
        return load_schema(file.read_text(encoding="utf-8"))
    except (OSError, SchemaReadError) as exc:
        raise DomainError("document_unreadable", f"{file.name} : {exc}") from None


def _expression(raw: dict[str, Any]) -> str:
    dialects = (raw.get("expression") or {}).get("dialects") or []
    first = next((d for d in dialects if isinstance(d, dict)), {})
    return str(first.get("expression", ""))


def list_domain_names(root: Path) -> list[str]:
    base = root / DOMAINS_DIR
    if not base.is_dir():
        return []
    return sorted(
        f.name[: -len(EXTEND_SUFFIX)]
        for f in base.iterdir()
        if f.is_file()
        and f.name.endswith(EXTEND_SUFFIX)
        and is_valid_domain_name(f.name[: -len(EXTEND_SUFFIX)])
    )


def read_domain(root: Path, name: str) -> Domain | None:
    """Domaine lu dans la copie `root`, None s'il n'existe pas. Un document Ossie
    invalide lève DomainError (avec la règle en cause) ; une entrée d'extension
    orpheline est signalée dans `warnings`, pas refusée."""
    if not is_valid_domain_name(name):
        return None
    base = root / DOMAINS_DIR

    def load(suffix: str) -> Any:
        file = base / f"{name}{suffix}"
        return _load(file) if file.is_file() else MISSING

    return domain_from(name, load)


# Un fichier du domaine absent (distinct d'un document vide).
MISSING: Any = object()


def domain_from(name: str, load: Callable[[str], Any]) -> Domain | None:
    """Le domaine `name`, depuis ses fichiers lus par `load(suffixe)` (le document, ou
    `MISSING` si le fichier manque ; lève DomainError « document_unreadable » s'il est
    illisible). Les fichiers ou l'index des documents (ticket fc5bb3b4) : même règle."""
    extend = load(EXTEND_SUFFIX)
    if extend is MISSING:
        return None
    _check(_extend_validator(), extend, "extend_invalid")
    domain = Domain(
        name=name,
        description=str(extend.get("description") or ""),
        label=str(extend.get("label") or ""),
    )
    ext_datasets: dict[str, Any] = extend.get("datasets") or {}
    seen: set[str] = set()
    for suffix, consultable in ((OSSIE_SUFFIX, True), (VIRTUAL_SUFFIX, False)):
        doc = load(suffix)
        if doc is MISSING:
            continue
        _check(_ossie_validator(), doc, "ossie_invalid")
        for model in doc.get("semantic_model") or []:
            if not domain.description and model.get("description"):
                domain.description = str(model["description"])
            for raw in model.get("datasets") or []:
                ext = ext_datasets.get(raw["name"]) or {}
                ext_fields: dict[str, Any] = ext.get("fields") or {}
                asset = ext.get("asset")
                domain.datasets.append(
                    Dataset(
                        name=raw["name"],
                        source=raw["source"],
                        consultable=bool(ext.get("consultable", consultable)),
                        derived=bool(ext.get("derived", False)),
                        asset=AssetRef(asset["source"], asset["path"]) if asset else None,
                        description=str(raw.get("description") or ""),
                        fields=[
                            DomainField(
                                name=f["name"],
                                expression=_expression(f),
                                description=str(f.get("description") or ""),
                                label=str(f.get("label") or ""),
                                datatype=str((ext_fields.get(f["name"]) or {}).get("datatype", "")),
                                property=str((ext_fields.get(f["name"]) or {}).get("property", "")),
                                referentiel=(ext_fields.get(f["name"]) or {}).get("referentiel"),
                            )
                            for f in raw.get("fields") or []
                        ],
                        primary_key=list(raw.get("primary_key") or []),
                        unique_keys=[list(k) for k in raw.get("unique_keys") or []],
                    )
                )
                seen.add(raw["name"])
            for rel in model.get("relationships") or []:
                domain.relationships.append(
                    Relationship(
                        name=rel["name"],
                        from_dataset=rel["from"],
                        to_dataset=rel["to"],
                        from_columns=list(rel["from_columns"]),
                        to_columns=list(rel["to_columns"]),
                    )
                )
    for orphan in sorted(set(ext_datasets) - seen):
        domain.warnings.append(f"extend_orphan_dataset:{orphan}")
    for ref in extend.get("references") or []:
        if ref["from"] not in seen or ref["to"] not in seen:
            domain.warnings.append(f"extend_orphan_reference:{ref['name']}")
            continue
        domain.references.append(
            Reference(
                name=ref["name"],
                from_dataset=ref["from"],
                field=ref["field"],
                to_dataset=ref["to"],
                kind=ref["kind"],
                cardinality=str(ref.get("cardinality") or ""),
            )
        )
    return domain


def list_domains(root: Path) -> list[Domain]:
    """Tous les domaines lisibles de la copie ; un domaine illisible est rendu
    vide avec son motif dans `warnings` (l'écran le signale, rien ne casse)."""
    return domains_of(list_domain_names(root), lambda name: read_domain(root, name))


def domains_of(names: list[str], read: Callable[[str], Domain | None]) -> list[Domain]:
    """`list_domains`, pour ces domaines lus par `read` (les fichiers ou l'index)."""
    out: list[Domain] = []
    for name in names:
        try:
            domain = read(name)
        except DomainError as exc:
            domain = Domain(name=name, warnings=[f"{exc.code}:{exc.detail}"])
        if domain is not None:
            out.append(domain)
    return out
