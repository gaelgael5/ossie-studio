"""Le modèle sémantique Ossie dans l'écriture commune (epic « Primitives MCP — écriture
commune par type de document », ticket b4aeae83) : types de document `domaine`, `dataset`,
`relation_dataset`, `reference_dataset`.

Un domaine `<nom>`, ce sont trois fichiers dans `domaines/` (`<nom>.ossie.yaml`,
`<nom>.virtuel.ossie.yaml`, `<nom>.extend.yaml`). L'agent ne choisit jamais le
fichier : chaque validateur lit le domaine, change la partie visée, et le réécrit par
**la seule porte d'écriture des domaines** (`domains/documents.write_domain`), qui
répartit les datasets entre les fichiers, vérifie le domaine entier (`check_domain`)
et valide les documents Ossie contre le schéma officiel embarqué.

« La priorité est bien de garder les standards non altérés » (Gael) : le contenu d'un
dataset sépare la part **Ossie 0.1.1** (`ossie` : le dataset tel que le document
Ossie l'écrit, validé contre le schéma officiel) de la part **extension** (`extension`
: ce qui va dans le `.extend.yaml`). Ce que le service ne conserve pas
(`ai_context`, `custom_extensions`, `dimension`, plusieurs dialectes) est refusé
plutôt que perdu en silence.

Plusieurs datasets (ou relations) partagent un fichier : leur version est celle de
leur contenu, et leur taille est mesurée seule (comme une valeur de référentiel).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ossie_studio.domains.documents import (
    DIALECT,
    DOMAINS_DIR,
    EXTEND_SCHEMA,
    EXTEND_SUFFIX,
    OSSIE_SUFFIX,
    VIRTUAL_SUFFIX,
    AssetRef,
    Dataset,
    Domain,
    DomainError,
    DomainField,
    Reference,
    Relationship,
    delete_domain,
    is_valid_domain_name,
    list_domains,
    read_domain,
    write_domain,
)
from ossie_studio.mcp_surface.document_type_base import (
    Document,
    DocumentError,
    Item,
    ListQuery,
    Location,
)

_OSSIE_SCHEMA_FILE = Path(__file__).parents[1] / "domains" / "schemas" / "osi-schema-0.1.1.json"

# Ce que le service des domaines écrit d'un dataset et d'un champ Ossie : le reste du
# schéma officiel serait perdu à l'écriture, il est donc refusé.
_DATASET_KEYS = ("name", "source", "description", "primary_key", "unique_keys", "fields")
_FIELD_KEYS = ("name", "expression", "label", "description")
_RELATION_KEYS = ("name", "from", "to", "from_columns", "to_columns")


# --- Chemins, calculés par les constantes du service ------------------------------------------


def extend_path(domain: str) -> str:
    return f"{DOMAINS_DIR}/{domain}{EXTEND_SUFFIX}"


def ossie_path(domain: str, consultable: bool) -> str:
    return f"{DOMAINS_DIR}/{domain}{OSSIE_SUFFIX if consultable else VIRTUAL_SUFFIX}"


# --- Schémas (read_schema) --------------------------------------------------------------------


@cache
def _ossie_defs() -> dict[str, Any]:
    defs: dict[str, Any] = json.loads(_OSSIE_SCHEMA_FILE.read_text(encoding="utf-8"))["$defs"]
    return defs


def _extend_part(name: str) -> dict[str, Any]:
    properties: dict[str, Any] = EXTEND_SCHEMA["properties"]
    if name == "dataset":
        part: dict[str, Any] = properties["datasets"]["additionalProperties"]
        return part
    reference: dict[str, Any] = properties["references"]["items"]
    return reference


def dataset_schema() -> dict[str, Any]:
    """Le contenu d'un dataset : la part Ossie 0.1.1 (schéma officiel) et la part
    extension (schéma de l'extension d'Ossie-studio)."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Dataset d'un domaine (Ossie 0.1.1 + extension)",
        "type": "object",
        "required": ["ossie", "extension"],
        "additionalProperties": False,
        "properties": {
            "ossie": {"$ref": "#/$defs/Dataset"},
            "extension": _extend_part("dataset"),
        },
        "$defs": _ossie_defs(),
    }


def relation_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Relation Ossie 0.1.1 (clé étrangère)",
        "$ref": "#/$defs/Relationship",
        "$defs": _ossie_defs(),
    }


def reference_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Référence entre datasets (extension)",
        **_extend_part("reference"),
    }


def domain_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Domaine (modèle sémantique Ossie)",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string"},
            "label": {"type": "string"},
            "description": {"type": "string"},
        },
    }


@cache
def _validator(name: str) -> Draft202012Validator:
    schemas = {
        "dataset_ossie": {"$ref": "#/$defs/Dataset", "$defs": _ossie_defs()},
        "dataset_extension": _extend_part("dataset"),
        "relation": relation_schema(),
        "reference": reference_schema(),
        "domain": domain_schema(),
    }
    return Draft202012Validator(schemas[name])


def _check_schema(name: str, data: Any, prefix: str) -> None:
    """Le contenu passe son schéma ; sinon refus au chemin de la première erreur."""
    error = next(iter(sorted(_validator(name).iter_errors(data), key=lambda e: list(e.path))), None)
    if error is None:
        return
    where = prefix + "".join(
        f"[{p}]" if isinstance(p, int) else f".{p}" for p in error.absolute_path
    )
    raise DocumentError(
        "write_content_invalid",
        where,
        f"{where} : {error.message}.",
        expected=error.schema if isinstance(error.schema, dict) else None,
    )


def _not_kept(where: str, names: list[str]) -> DocumentError:
    return DocumentError(
        "write_content_invalid",
        where,
        f"{', '.join(names)} : Ossie-studio ne conserve pas ces propriétés d'Ossie 0.1.1 ; "
        "les retirer.",
        received=names,
    )


def _mapping(content: Any, label: str) -> dict[str, Any]:
    if not isinstance(content, Mapping):
        raise DocumentError(
            "write_content_invalid",
            "$",
            f"Le contenu d'un document « {label} » est un objet ; reçu {type(content).__name__}.",
            received=content,
        )
    return dict(content)


def _same(field: str, given: Any, expected: Any, label: str) -> None:
    if given is not None and given != expected:
        raise DocumentError(
            "write_identity_mismatch",
            field,
            f"Le contenu dit « {given} » mais l'identité désigne « {expected} » : un document "
            f"« {label} » ne change jamais d'identité. Pour en changer : créer le nouveau "
            "document et supprimer l'ancien dans le même lot (write_documents).",
            expected=expected,
            received=given,
        )


# --- Contenu ↔ modèle du service --------------------------------------------------------------


def dataset_document(ds: Dataset) -> dict[str, Any]:
    ossie: dict[str, Any] = {"name": ds.name, "source": ds.source}
    if ds.description:
        ossie["description"] = ds.description
    if ds.primary_key:
        ossie["primary_key"] = list(ds.primary_key)
    if ds.unique_keys:
        ossie["unique_keys"] = [list(k) for k in ds.unique_keys]
    if ds.fields:
        ossie["fields"] = []
        for f in ds.fields:
            entry: dict[str, Any] = {
                "name": f.name,
                "expression": {"dialects": [{"dialect": DIALECT, "expression": f.expression}]},
            }
            if f.label:
                entry["label"] = f.label
            if f.description:
                entry["description"] = f.description
            ossie["fields"].append(entry)
    extension: dict[str, Any] = {"consultable": ds.consultable, "derived": ds.derived}
    if ds.asset is not None:
        extension["asset"] = {"source": ds.asset.source, "path": ds.asset.path}
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
        extension["fields"] = fields
    return {"ossie": ossie, "extension": extension}


def _dataset(name: str, content: Any) -> Dataset:
    data = _mapping(content, "dataset")
    unknown = sorted(set(data) - {"ossie", "extension"})
    if unknown:
        raise DocumentError(
            "write_content_invalid",
            f"$.{unknown[0]}",
            "Un dataset a deux parties : « ossie » (Ossie 0.1.1) et « extension » "
            f"(hors norme) ; reçu aussi : {', '.join(unknown)}.",
            received=unknown,
        )
    ossie = data.get("ossie")
    extension = data.get("extension")
    _check_schema("dataset_ossie", ossie, "$.ossie")
    _check_schema("dataset_extension", extension, "$.extension")
    assert isinstance(ossie, dict) and isinstance(extension, dict)
    _same("$.ossie.name", ossie.get("name"), name, "dataset")
    dropped = sorted(set(ossie) - set(_DATASET_KEYS))
    if dropped:
        raise _not_kept("$.ossie", dropped)
    fields: list[DomainField] = []
    ext_fields: dict[str, Any] = extension.get("fields") or {}
    for i, raw in enumerate(ossie.get("fields") or []):
        where = f"$.ossie.fields[{i}]"
        dropped = sorted(set(raw) - set(_FIELD_KEYS))
        if dropped:
            raise _not_kept(where, dropped)
        dialects = raw["expression"]["dialects"]
        if len(dialects) != 1 or dialects[0]["dialect"] != DIALECT:
            raise DocumentError(
                "write_content_invalid",
                f"{where}.expression.dialects",
                f"Une seule expression, en dialecte {DIALECT} (le nom de la colonne, ou de la "
                "propriété pour un dataset non consultable).",
                received=dialects,
            )
        ext = ext_fields.get(raw["name"]) or {}
        fields.append(
            DomainField(
                name=raw["name"],
                expression=str(dialects[0]["expression"]),
                description=str(raw.get("description") or ""),
                label=str(raw.get("label") or ""),
                datatype=str(ext.get("datatype") or ""),
                property=str(ext.get("property") or ""),
                referentiel=ext.get("referentiel"),
            )
        )
    asset = extension.get("asset")
    return Dataset(
        name=name,
        source=str(ossie["source"]),
        consultable=bool(extension["consultable"]),
        derived=bool(extension["derived"]),
        asset=AssetRef(asset["source"], asset["path"]) if asset else None,
        description=str(ossie.get("description") or ""),
        fields=fields,
        primary_key=list(ossie.get("primary_key") or []),
        unique_keys=[list(k) for k in ossie.get("unique_keys") or []],
    )


def relation_document(rel: Relationship) -> dict[str, Any]:
    return {
        "name": rel.name,
        "from": rel.from_dataset,
        "to": rel.to_dataset,
        "from_columns": list(rel.from_columns),
        "to_columns": list(rel.to_columns),
    }


def _relation(name: str, content: Any) -> Relationship:
    data = {**_mapping(content, "relation de datasets"), "name": name}
    _same("$.name", _mapping(content, "relation").get("name"), name, "relation de datasets")
    dropped = sorted(set(data) - set(_RELATION_KEYS))
    if dropped:
        raise _not_kept("$", dropped)
    _check_schema("relation", data, "$")
    return Relationship(
        name=name,
        from_dataset=data["from"],
        to_dataset=data["to"],
        from_columns=list(data["from_columns"]),
        to_columns=list(data["to_columns"]),
    )


def reference_document(ref: Reference) -> dict[str, Any]:
    doc = {
        "name": ref.name,
        "from": ref.from_dataset,
        "field": ref.field,
        "to": ref.to_dataset,
        "kind": ref.kind,
    }
    if ref.cardinality:
        doc["cardinality"] = ref.cardinality
    return doc


def _reference(name: str, content: Any) -> Reference:
    given = _mapping(content, "référence de datasets")
    _same("$.name", given.get("name"), name, "référence de datasets")
    data = {**given, "name": name}
    _check_schema("reference", data, "$")
    return Reference(
        name=name,
        from_dataset=data["from"],
        field=data["field"],
        to_dataset=data["to"],
        kind=data["kind"],
        cardinality=str(data.get("cardinality") or ""),
    )


def domain_document(domain: Domain) -> dict[str, Any]:
    doc: dict[str, Any] = {"name": domain.name}
    if domain.label:
        doc["label"] = domain.label
    if domain.description:
        doc["description"] = domain.description
    return doc


def _version(content: Any) -> str:
    from ossie_studio.mcp_surface.document_type_stores import value_version

    return value_version(content)


# --- Refus du service, au bon chemin ----------------------------------------------------------


def _refused(exc: DomainError, content: Any = None) -> DocumentError:
    """Un refus du service des domaines, rattaché au champ du document quand on le
    sait (une colonne inconnue d'une relation : le rang de la colonne)."""
    where = "$"
    if exc.code == "relationship_column_unknown" and isinstance(content, Mapping):
        dataset, _, column = exc.detail.partition(".")
        for side in ("from", "to"):
            columns = content.get(f"{side}_columns") or []
            if content.get(side) == dataset and column in columns:
                where = f"$.{side}_columns[{list(columns).index(column)}]"
                break
    elif exc.code.startswith(("relationship_dataset", "reference_dataset")):
        where = "$.from"
    return DocumentError(
        "write_content_invalid",
        where,
        f"Refusé par le service des domaines : {exc.code}"
        + (f" ({exc.detail})" if exc.detail else "")
        + ".",
        problem=exc.code,
    )


def _domain_or_refuse(root: Path, name: str) -> Domain:
    domain = read_domain(root, name)
    if domain is None:
        raise DocumentError(
            "write_identity_invalid",
            "domain",
            f"Le domaine « {name} » n'existe pas (list_documents, type de document domaine) : le "
            f"créer, "
            "dans le même lot au besoin.",
            received=name,
        )
    return domain


def _save(root: Path, domain: Domain, content: Any = None) -> None:
    try:
        write_domain(root, domain)
    except DomainError as exc:
        raise _refused(exc, content) from None


# --- Validateurs ------------------------------------------------------------------------------


class DomainStore:
    """Un domaine : son nom, son libellé, sa description. Il existe dès que son
    `.extend.yaml` existe. Le supprimer demande qu'il n'ait plus de dataset (les
    supprimer dans le même lot)."""

    def read(self, root: Path, location: Location) -> Document | None:
        domain = read_domain(root, location.identity["name"])
        if domain is None:
            return None
        doc = domain_document(domain)
        return Document(doc, _version(doc), extend_path(domain.name))

    def write(self, root: Path, location: Location, content: Any, *, create: bool) -> list[str]:
        name = location.identity["name"]
        data = _mapping(content, "domaine")
        _check_schema("domain", data, "$")
        _same("$.name", data.get("name"), name, "domaine")
        domain = Domain(name=name) if create else _domain_or_refuse(root, name)
        domain.label = str(data.get("label") or "")
        domain.description = str(data.get("description") or "")
        _save(root, domain)
        return []

    def delete(self, root: Path, location: Location) -> None:
        domain = _domain_or_refuse(root, location.identity["name"])
        if domain.datasets:
            raise DocumentError(
                "write_content_invalid",
                "delete",
                f"Le domaine « {domain.name} » a encore des datasets "
                f"({', '.join(d.name for d in domain.datasets)}) : les supprimer dans le "
                "même lot.",
            )
        delete_domain(root, domain.name)


class DatasetStore:
    """Un dataset : la part Ossie et la part extension. Le service le range dans le
    document Ossie ou virtuel selon `extension.consultable` ; le changer le déplace."""

    def read(self, root: Path, location: Location) -> Document | None:
        domain = read_domain(root, location.identity["domain"])
        ds = domain.dataset(location.identity["name"]) if domain else None
        if domain is None or ds is None:
            return None
        doc = dataset_document(ds)
        return Document(doc, _version(doc), ossie_path(domain.name, ds.consultable))

    def write(self, root: Path, location: Location, content: Any, *, create: bool) -> list[str]:
        domain = _domain_or_refuse(root, location.identity["domain"])
        ds = _dataset(location.identity["name"], content)
        domain.datasets = [d for d in domain.datasets if d.name != ds.name] + [ds]
        _save(root, domain, content)
        return []

    def delete(self, root: Path, location: Location) -> None:
        """Retire le dataset seul : une relation ou une référence qui le cite encore
        fait refuser le domaine (les supprimer dans le même lot)."""
        domain = _domain_or_refuse(root, location.identity["domain"])
        domain.datasets = [d for d in domain.datasets if d.name != location.identity["name"]]
        _save(root, domain)


class RelationStore:
    """Une relation Ossie (clé étrangère) entre deux datasets du même document."""

    def read(self, root: Path, location: Location) -> Document | None:
        domain = read_domain(root, location.identity["domain"])
        if domain is None:
            return None
        rel = next((r for r in domain.relationships if r.name == location.identity["name"]), None)
        if rel is None:
            return None
        source = domain.dataset(rel.from_dataset)
        doc = relation_document(rel)
        return Document(
            doc, _version(doc), ossie_path(domain.name, bool(source and source.consultable))
        )

    def write(self, root: Path, location: Location, content: Any, *, create: bool) -> list[str]:
        domain = _domain_or_refuse(root, location.identity["domain"])
        rel = _relation(location.identity["name"], content)
        domain.relationships = [r for r in domain.relationships if r.name != rel.name] + [rel]
        _save(root, domain, content)
        return []

    def delete(self, root: Path, location: Location) -> None:
        domain = _domain_or_refuse(root, location.identity["domain"])
        name = location.identity["name"]
        domain.relationships = [r for r in domain.relationships if r.name != name]
        _save(root, domain)


class ReferenceStore:
    """Un lien d'objet entre deux datasets, sans colonne de clé : il vit dans
    l'extension (Ossie 0.1.1 ne sait pas l'exprimer)."""

    def read(self, root: Path, location: Location) -> Document | None:
        domain = read_domain(root, location.identity["domain"])
        if domain is None:
            return None
        ref = next((r for r in domain.references if r.name == location.identity["name"]), None)
        if ref is None:
            return None
        doc = reference_document(ref)
        return Document(doc, _version(doc), extend_path(domain.name))

    def write(self, root: Path, location: Location, content: Any, *, create: bool) -> list[str]:
        domain = _domain_or_refuse(root, location.identity["domain"])
        ref = _reference(location.identity["name"], content)
        domain.references = [r for r in domain.references if r.name != ref.name] + [ref]
        _save(root, domain, content)
        return []

    def delete(self, root: Path, location: Location) -> None:
        domain = _domain_or_refuse(root, location.identity["domain"])
        name = location.identity["name"]
        domain.references = [r for r in domain.references if r.name != name]
        _save(root, domain)


# --- Emplacements -----------------------------------------------------------------------------


def _domain_name(doc_type: str, identity: Mapping[str, Any], field: str) -> str:
    name = identity[field]
    if not isinstance(name, str) or not is_valid_domain_name(name):
        raise DocumentError(
            "write_identity_invalid",
            field,
            f"« {field} » invalide pour le type de document {doc_type} : minuscules, chiffres et "
            f"tirets "
            f"(ex. « ventes ») ; reçu « {name} ».",
            received=name,
        )
    return name


def locate_domain(root: Path | None, identity: Mapping[str, Any]) -> Location:
    name = _domain_name("domaine", identity, "name")
    return Location("domaine", {"name": name}, extend_path(name))


def _member_name(doc_type: str, identity: Mapping[str, Any]) -> str:
    name = identity["name"]
    if not isinstance(name, str) or not name.strip():
        raise DocumentError(
            "write_identity_invalid",
            "name",
            f"« name » est obligatoire pour le type de document {doc_type}.",
            received=name,
        )
    return name


def locate_dataset(root: Path | None, identity: Mapping[str, Any]) -> Location:
    domain = _domain_name("dataset", identity, "domain")
    name = _member_name("dataset", identity)
    path = None
    if root is not None:
        found = read_domain(root, domain)
        ds = found.dataset(name) if found else None
        path = ossie_path(domain, ds.consultable) if ds else None
    return Location("dataset", {"domain": domain, "name": name}, path, (name,))


def locate_relation(root: Path | None, identity: Mapping[str, Any]) -> Location:
    domain = _domain_name("relation_dataset", identity, "domain")
    name = _member_name("relation_dataset", identity)
    return Location("relation_dataset", {"domain": domain, "name": name}, None, (name,))


def locate_reference(root: Path | None, identity: Mapping[str, Any]) -> Location:
    domain = _domain_name("reference_dataset", identity, "domain")
    name = _member_name("reference_dataset", identity)
    return Location(
        "reference_dataset", {"domain": domain, "name": name}, extend_path(domain), (name,)
    )


# --- Listes -----------------------------------------------------------------------------------


def _domains(root: Path, query: ListQuery, field: str) -> list[Domain]:
    wanted = query.identity.get(field)
    found = list_domains(root)
    return [d for d in found if wanted is None or d.name == wanted]


def domain_items(root: Path, query: ListQuery) -> list[Item]:
    out = []
    for domain in _domains(root, query, "name"):
        doc = domain_document(domain)
        summary = {
            "datasets": len(domain.datasets),
            "relationships": len(domain.relationships),
            "references": len(domain.references),
            "warnings": list(domain.warnings),
            **{k: v for k, v in doc.items() if k != "name"},
        }
        out.append(
            Item(
                {"name": domain.name},
                domain.label or domain.name,
                extend_path(domain.name),
                summary,
                version=_version(doc),
            )
        )
    return out


def dataset_items(root: Path, query: ListQuery) -> list[Item]:
    out = []
    for domain in _domains(root, query, "domain"):
        for ds in domain.datasets:
            doc = dataset_document(ds)
            summary = {
                "source": ds.source,
                "consultable": ds.consultable,
                "derived": ds.derived,
                "fields": len(ds.fields),
                "description": ds.description,
            }
            if ds.asset is not None:
                summary["asset"] = {"source": ds.asset.source, "path": ds.asset.path}
            out.append(
                Item(
                    {"domain": domain.name, "name": ds.name},
                    ds.name,
                    ossie_path(domain.name, ds.consultable),
                    summary,
                    version=_version(doc),
                    related=(extend_path(domain.name),),
                )
            )
    return out


def relation_items(root: Path, query: ListQuery) -> list[Item]:
    out = []
    for domain in _domains(root, query, "domain"):
        for rel in domain.relationships:
            doc = relation_document(rel)
            source = domain.dataset(rel.from_dataset)
            path = ossie_path(domain.name, bool(source and source.consultable))
            summary = {k: v for k, v in doc.items() if k != "name"}
            out.append(
                Item(
                    {"domain": domain.name, "name": rel.name},
                    rel.name,
                    path,
                    summary,
                    version=_version(doc),
                )
            )
    return out


def reference_items(root: Path, query: ListQuery) -> list[Item]:
    out = []
    for domain in _domains(root, query, "domain"):
        for ref in domain.references:
            doc = reference_document(ref)
            summary = {k: v for k, v in doc.items() if k != "name"}
            out.append(
                Item(
                    {"domain": domain.name, "name": ref.name},
                    ref.name,
                    extend_path(domain.name),
                    summary,
                    version=_version(doc),
                )
            )
    return out
