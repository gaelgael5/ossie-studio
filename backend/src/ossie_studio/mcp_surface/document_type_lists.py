"""Ce que rend la liste de chaque type de document (`list_documents`, epic « Primitives MCP —
écriture commune par type de document », ticket 2113e7bc).

Un lister par type de document lit l'espace **par le service de l'écran** (cartographie,
modèles, processus, référentiels, gouvernance) et rend ses documents triés, avec
leur identité, leur nom, leur fichier et les champs de synthèse que rendaient les
lectures par domaine qu'il remplace (`list_carto_types`, `list_carto_objects`,
`list_carto_models`, `list_referentiels`, `search_referentiel_values`,
`list_tags`, `list_classifications`, `list_certifications`). Il applique les
filtres propres à son type de document (déclarés dans le registre) ; l'identité partielle, la
recherche texte, la pagination et les versions sont communes (`document_reads`).
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Any

from ossie_studio.cartographie.catalog import ArchimateType, catalog
from ossie_studio.cartographie.diagram import (
    diagram_path,
    graphic_path,
    list_diagrams,
    list_rules,
    read_diagram,
    rules_path,
)
from ossie_studio.cartographie.errors import CartographieError
from ossie_studio.cartographie.indexed import IndexedCartographie
from ossie_studio.cartographie.model import object_path, split_ref, type_path
from ossie_studio.cartographie.process import list_processes, parse_process, process_path
from ossie_studio.cartographie.space import Cartographie, read_cartographie
from ossie_studio.doc_index.search import Snapshot
from ossie_studio.governance import classification_rules as classification_rule_service
from ossie_studio.governance import tag_rules as tag_rule_service
from ossie_studio.governance.builtin_classification_rules import builtin_classification_rules
from ossie_studio.governance.builtin_tag_rules import builtin_rules
from ossie_studio.governance.classification_rules import read_classification_rules
from ossie_studio.governance.relation_profiles import builtin_profiles, read_profile_files
from ossie_studio.governance.relation_profiles import profile_document as relation_profile_document
from ossie_studio.governance.relation_profiles import profile_path as relation_profile_path
from ossie_studio.governance.repo import (
    cert_path,
    read_certs,
    read_schemes,
    read_tags,
    scheme_path,
    tag_path,
)
from ossie_studio.governance.tag_rules import read_tag_rules
from ossie_studio.mcp_surface.document_type_base import (
    Document,
    DocumentError,
    Item,
    ListFilter,
    ListQuery,
    Location,
)
from ossie_studio.mcp_surface.document_type_stores import (
    ClassificationRuleStore,
    ReadOnlyStore,
    TagRuleStore,
    value_version,
)
from ossie_studio.parsers.application import MODULE_KIND, extend_file
from ossie_studio.parsers.application import key_of as raw_key_of
from ossie_studio.parsers.conventions import SchemaReadError, load_schema
from ossie_studio.referentiels import published
from ossie_studio.referentiels.errors import ReferentielError
from ossie_studio.referentiels.model import model_key
from ossie_studio.referentiels.repo import list_referentiels, model_path, values_path
from ossie_studio.referentiels.values import key_of
from ossie_studio.sources.asset_files import extend_name
from ossie_studio.sources.collect_core import RAW_DIR
from ossie_studio.sources.contracts import read_contracts_view
from ossie_studio.sources.curation import (
    CurationObject,
    list_curation,
    read_tri_rules,
    read_tri_rules_raw,
)
from ossie_studio.sources.explorer import SourceEntries, collect_source_entries
from ossie_studio.sources.explorer_application import (
    application_view,
    members_by_module,
    module_detail,
)
from ossie_studio.sources.paths import (
    ASSETS_DIR,
    CONTRATS_DIR,
    MANIFEST_FILENAME,
    SOURCES_DIR,
    is_valid_source_slug,
    source_rel_dir,
)
from ossie_studio.sources.raw_facts import IndexedSource, IndexedSources
from ossie_studio.sources.service import read_sources_tree
from ossie_studio.sources.tri import TriRule

# --- Les filtres déclarés -----------------------------------------------------------------

NATURE = ListFilter(
    "nature",
    "'object' or 'relation' (a relationship is an object of a relationship type).",
    choices=("object", "relation"),
)
ARCHIMATE = ListFilter("archimate", "ArchiMate type, e.g. 'ApplicationComponent'.")
SOURCE = ListFilter(
    "source", "An object, as 'type/KEY' (or its key alone): the relationships that start from it."
)
CIBLE = ListFilter(
    "cible", "An object, as 'type/KEY' (or its key alone): the relationships that end on it."
)
CONTAINS = ListFilter(
    "contains",
    "An object or relationship, as 'type/KEY' (or its key alone): the models that keep it.",
)
DETAIL_OF = ListFilter(
    "detail_of", "Key of a business process of the map: the BPMN processes that detail it."
)
SOURCE_TYPE = ListFilter("type", "Type of the source, e.g. 'git', 'snowflake', 'openapi'.")
RAW_KIND = ListFilter(
    "kind", "Nature of the raw object (its x-kind), e.g. 'module', 'class', 'table', 'view'."
)
RAW_MODULE = ListFilter(
    "module",
    "A module of the source (its name or its title, from filters {kind: 'module'}): the raw "
    "objects that belong to it (nearest project file up the tree, as the Explorer does).",
)
ENTRY_MODULE = ListFilter(
    "module",
    "A module of the source (its name or its title, from "
    "list_documents(document_type='schema_raw', "
    "filters={kind: 'module'})): its entry points.",
)
ENTRY_CONTRACT = ListFilter(
    "contract_status",
    "'absent' = entry points missing from the OpenAPI contract of the source; 'linked' = "
    "matched to an operation; 'not_compared' = never compared to a contract.",
    choices=("linked", "absent", "not_compared"),
)
ENTRY_KIND = ListFilter(
    "kind", "'http' (verb and route), 'task' or 'message'.", choices=("http", "task", "message")
)
EQUALS = ListFilter(
    "equals",
    "Equality on simple top-level properties of the values, e.g. {pays: 'FR'}; all must "
    "match. Needs identity.referentiel.",
    type="object",
)


def _nature(base: ArchimateType | None) -> str:
    return "relation" if base is not None and base.is_relation else "object"


def _given(query: ListQuery, name: str) -> Any:
    return query.filters.get(name)


# --- Cartographie -------------------------------------------------------------------------


def carto_types(root: Path, query: ListQuery) -> list[Item]:
    space = read_cartographie(root)
    cat = catalog()
    counts: dict[str, int] = {}
    for _path, obj in space.objects.values():
        counts[obj.type] = counts.get(obj.type, 0) + 1
    out = []
    for ctype in sorted(space.types.values(), key=lambda t: (t.title.casefold(), t.slug)):
        base = cat.get(ctype.archimate)
        nature = _nature(base)
        if _given(query, "nature") not in (None, nature):
            continue
        if _given(query, "archimate") not in (None, ctype.archimate):
            continue
        out.append(
            Item(
                {"slug": ctype.slug},
                ctype.title,
                type_path(ctype.slug),
                {
                    "archimate": ctype.archimate,
                    "layer": base.layer if base and not base.is_relation else None,
                    "nature": nature,
                    "objects": counts.get(ctype.slug, 0),
                },
            )
        )
    return out


def _end(space: Cartographie, value: str | None) -> dict[str, Any] | None:
    """Une extrémité de relation : `ref` (`type/CLÉ`), clé, nom, type."""
    if value is None:
        return None
    found = space.find(value)
    if found is None:
        key = split_ref(value)[1]
        return {"ref": value, "key": key, "name": None, "type": None, "exists": False}
    return {
        "ref": found[1].ref,
        "key": found[1].key,
        "name": found[1].name,
        "type": found[1].type,
        "exists": True,
        "path": found[0],
    }


def carto_objects(root: Path, query: ListQuery) -> list[Item]:
    space = read_cartographie(root)
    cat = catalog()
    out = []
    for path, obj in space.objects.values():
        ctype = space.types.get(obj.type)
        archimate = ctype.archimate if ctype else None
        nature = _nature(cat.get(archimate or ""))
        wanted = {"nature": nature, "archimate": archimate}
        if any(_given(query, f) not in (None, value) for f, value in wanted.items()):
            continue
        ends = space.ends(obj)
        if any(
            _given(query, f) is not None and (end is None or space.resolve(_given(query, f)) != end)
            for f, end in (("source", ends[0]), ("cible", ends[1]))
        ):
            continue
        summary: dict[str, Any] = {"archimate": archimate, "nature": nature}
        if obj.is_relation:
            summary["source"] = _end(space, obj.source)
            summary["target"] = _end(space, obj.target)
        out.append(Item({"type": obj.type, "key": obj.key}, obj.name, path, summary))
    out.sort(key=lambda i: (i.name.casefold(), i.identity["key"]))
    return out


# --- Cartographie, tirée de l'index (ticket 06171fd3) ------------------------------------
#
# Mêmes éléments que `carto_types` et `carto_objects`, sans lire les fichiers
# (`cartographie/indexed.py`).


def carto_types_indexed(snap: Snapshot, query: ListQuery) -> list[Item]:
    space = IndexedCartographie(snap)
    cat = catalog()
    counts: dict[str, int] = {}
    for obj in space.objects.values():
        counts[obj.type] = counts.get(obj.type, 0) + 1
    out = []
    ordered = sorted(
        space.types.items(), key=lambda t: (str(t[1].get("title") or "").casefold(), t[0])
    )
    for slug, fields in ordered:
        archimate = str(fields.get("archimate") or "")
        base = cat.get(archimate)
        nature = _nature(base)
        if _given(query, "nature") not in (None, nature):
            continue
        if _given(query, "archimate") not in (None, archimate):
            continue
        out.append(
            Item(
                {"slug": slug},
                str(fields.get("title") or ""),
                type_path(slug),
                {
                    "archimate": archimate,
                    "layer": base.layer if base and not base.is_relation else None,
                    "nature": nature,
                    "objects": counts.get(slug, 0),
                },
                version=space.type_versions.get(slug),
            )
        )
    return out


def carto_objects_indexed(snap: Snapshot, query: ListQuery) -> list[Item]:
    space = IndexedCartographie(snap)
    cat = catalog()
    out = []
    for obj in space.objects.values():
        fields = space.types.get(obj.type)
        archimate = str(fields.get("archimate") or "") if fields is not None else None
        nature = _nature(cat.get(archimate or ""))
        wanted = {"nature": nature, "archimate": archimate}
        if any(_given(query, f) not in (None, value) for f, value in wanted.items()):
            continue
        ends = (space.resolve(obj.source), space.resolve(obj.target))
        if any(
            _given(query, f) is not None and (end is None or space.resolve(_given(query, f)) != end)
            for f, end in (("source", ends[0]), ("cible", ends[1]))
        ):
            continue
        summary: dict[str, Any] = {"archimate": archimate, "nature": nature}
        if obj.is_relation:
            summary["source"] = space.end(obj.source)
            summary["target"] = space.end(obj.target)
        out.append(
            Item({"type": obj.type, "key": obj.key}, obj.name, obj.path, summary, version=obj.blob)
        )
    out.sort(key=lambda i: (i.name.casefold(), i.identity["key"]))
    return out


def carto_models(root: Path, query: ListQuery) -> list[Item]:
    space = read_cartographie(root)
    wanted = _given(query, "contains")
    out = []
    for slug in list_diagrams(root):
        read = read_diagram(root, slug, space)
        diagram = read.diagram
        problems = [{"code": p.code, "detail": p.detail, "field": p.field} for p in read.errors]
        related = (graphic_path(slug),)
        if diagram is None:  # illisible : nommé, sans contenu
            if not wanted:
                summary = {"readable": False, "problems": problems}
                out.append(Item({"slug": slug}, slug, read.path, summary, related=related))
            continue
        retained = diagram.elements + diagram.relationships
        if wanted and not _keeps(retained, diagram.documents, wanted):
            continue
        summary = {
            "readable": True,
            "id": diagram.id,
            "description": diagram.description,
            "objects": len(diagram.elements),
            "relations": len(diagram.relationships),
            "views": [
                {"id": v.id, "name": v.name, "viewpoint": v.viewpoint or None}
                for v in diagram.views
            ],
            "rules": diagram.rules,
            "problems": problems,
        }
        name = diagram.name or diagram.id
        out.append(Item({"slug": slug}, name, read.path, summary, related=related))
    return out


def _keeps(retained: list[str], documents: list[str], wanted: str) -> bool:
    """Le modèle retient-il cet objet (`type/CLÉ`, ou une clé seule) ?"""
    type_slug, key = split_ref(wanted)
    if key not in retained:
        return False
    return type_slug is None or object_path(type_slug, key) in documents


def carto_graphics(root: Path, query: ListQuery) -> list[Item]:
    return [
        Item({"slug": slug}, slug, graphic_path(slug), {"model": diagram_path(slug)})
        for slug in list_diagrams(root)
        if (root / graphic_path(slug)).is_file()
    ]


def carto_rules(root: Path, query: ListQuery) -> list[Item]:
    return [Item({"slug": slug}, slug, rules_path(slug)) for slug in list_rules(root)]


def processes(root: Path, query: ListQuery) -> list[Item]:
    wanted = _given(query, "detail_of")
    out = []
    for key in list_processes(root):
        path = process_path(key)
        try:
            process = parse_process((root / path).read_text(encoding="utf-8"))
        except (CartographieError, UnicodeDecodeError):
            if not wanted:
                out.append(Item({"key": key}, key, path, {"readable": False}))
            continue
        if wanted and process.detail_of != wanted:
            continue
        summary = {
            "readable": True,
            "detail_of": process.detail_of,
            "has_diagram": process.has_diagram,
            "elements": process.elements,
        }
        out.append(Item({"key": key}, process.name or key, path, summary))
    return out


# --- Référentiels -------------------------------------------------------------------------


def referentiel_models(root: Path, query: ListQuery) -> list[Item]:
    out = []
    for summary in published.summaries(root):
        name = summary.pop("name")
        title = summary.get("title") or name
        out.append(Item({"referentiel": name}, title, model_path(name), summary))
    return out


def _scalars(value: dict[str, Any]) -> str:
    return " ".join(str(v) for v in value.values() if isinstance(v, str | int | float))


def referentiel_values(root: Path, query: ListQuery) -> list[Item]:
    """Les valeurs, triées par clé comme `search_referentiel_values` ; `equals` filtre
    par égalité exactement comme elle (même fonction du service)."""
    name = query.identity.get("referentiel")
    equals = _given(query, "equals") or {}
    if equals and not name:
        raise DocumentError(
            "list_filter_invalid",
            "$.filters.equals",
            "« equals » filtre les valeurs d'un référentiel : donner aussi identity.referentiel.",
        )
    names = [str(name)] if name else list_referentiels(root)
    out = []
    for ref_name in names:
        try:
            found = published.search_values(root, ref_name, equals, sys.maxsize, 0)
        except ReferentielError as exc:
            if exc.code == "referentiel_filter_unknown":
                prop = exc.detail.get("property")
                raise DocumentError(
                    "list_filter_invalid",
                    f"$.filters.equals.{prop}",
                    f"« {prop} » n'est pas une propriété simple de premier niveau du "
                    f"référentiel « {ref_name} » : elle ne peut pas servir de filtre.",
                    received=prop,
                ) from None
            if name:
                raise
            continue  # un référentiel illisible ne casse pas la liste de tous
        fields = model_key(published.model_of(root, ref_name))
        for value in found["values"]:
            parts = key_of(value, fields) or ()
            single = len(fields) == 1 and parts
            key: Any = parts[0] if single else dict(zip(fields, parts, strict=False))
            out.append(
                Item(
                    {"referentiel": ref_name, "key": key},
                    " / ".join(str(p) for p in parts),
                    values_path(ref_name),
                    {"value": value},
                    text=_scalars(value),
                    version=value_version(value),
                )
            )
    return out


# --- Sources, en lecture seule (epic « lectures communes », ticket 88fd2cc5) --------------


def sources(root: Path, query: ListQuery) -> list[Item]:
    """Ce que rendait `list_sources` : chaque source et son manifeste ; un manifeste
    illisible est listé avec son erreur (`ok: false`), jamais ignoré en silence."""
    wanted = _given(query, "type")
    out = []
    for entry in read_sources_tree(root):
        manifest = entry.manifest
        if wanted is not None and (manifest is None or manifest.type != wanted):
            continue
        valid = is_valid_source_slug(entry.slug)
        directory = source_rel_dir(entry.slug) if valid else f"{SOURCES_DIR}/{entry.slug}"
        if manifest is None:
            summary: dict[str, Any] = {"ok": False, "error": entry.error}
            name, text = entry.slug, ""
        else:
            summary = {
                "ok": True,
                "label": manifest.label,
                "type": manifest.type,
                "connection": manifest.connection,
                "parameters": manifest.parameters,
            }
            name, text = manifest.label or entry.slug, f"{manifest.type} {manifest.connection}"
        out.append(
            Item(
                {"source": entry.slug},
                name,
                f"{directory}/{MANIFEST_FILENAME}",
                summary,
                text=text,
                changes_under=directory,
            )
        )
    return out


def contracts(root: Path, query: ListQuery) -> list[Item]:
    """Ce que rendait `list_contracts` : chaque version déposée d'un contrat OpenAPI,
    ses faits de dépôt (index `contrats.yaml`) et si la collecte l'a pris."""
    out = []
    for source in _sources(_FileSources(root), query):
        base = f"{source_rel_dir(source)}/{CONTRATS_DIR}"
        for view in read_contracts_view(root, source):
            entry = view.entry
            out.append(
                Item(
                    {"source": source, "version": entry.version},
                    f"{source} {entry.version}",
                    f"{base}/{entry.file}",
                    # La version déposée est dans l'identité : « version » reste celle du
                    # document (à donner telle quelle à une écriture).
                    {
                        **{k: v for k, v in entry.to_mapping().items() if k != "version"},
                        "collected": view.collected,
                    },
                    text=f"{entry.original_file} {entry.declared_version}",
                )
            )
    return out


# --- Schémas raw et d'assets, en lecture seule (ticket 56f3784a) --------------------------


class _FileSources:
    """Les sources d'une vue, lues dans les fichiers."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def slugs(self) -> list[str]:
        return [entry.slug for entry in read_sources_tree(self.root)]

    def curation(self, source: str, rules: Any, show_excluded: bool) -> list[CurationObject]:
        return list_curation(self.root, source, rules, show_excluded)

    def tri_rules(self, source: str) -> list[TriRule]:
        return read_tri_rules(self.root, source)

    def application(self, source: str) -> _Application:
        return _Application.read(self.root, source)

    def extension(self, path: str) -> dict[str, Any] | None:
        file = self.root / path
        return _functions_summary(file) if file.is_file() else None

    def version(self, path: str) -> str | None:
        return None  # celle du fichier, calculée par la liste


class _IndexSources:
    """Les sources d'une vue, lues dans l'index des documents (ticket 4618ee38) : les
    mêmes règles, nourries par les faits que l'index garde de chaque fichier."""

    def __init__(self, snap: Snapshot) -> None:
        self.files = snap.files
        self.sources = IndexedSources(snap.files)

    def slugs(self) -> list[str]:
        return self.sources.slugs()

    def curation(self, source: str, rules: Any, show_excluded: bool) -> list[CurationObject]:
        return self.sources.source(source).curation(list(rules), show_excluded)

    def tri_rules(self, source: str) -> list[TriRule]:
        return self.sources.source(source).tri_rules()

    def application(self, source: str) -> _Application:
        return _Application.indexed(self.sources.source(source))

    def extension(self, path: str) -> dict[str, Any] | None:
        found = self.files.get(path)
        if found is None:
            return None
        facts = (found[1] or {}).get("raw_extend") or {}
        return dict(facts.get("summary") or {"readable": False})

    def version(self, path: str) -> str | None:
        found = self.files.get(path)
        return found[0] if found is not None else None


_Provider = _FileSources | _IndexSources


def _sources(provider: _Provider, query: ListQuery) -> list[str]:
    """La source demandée (refus nommé si elle n'existe pas), ou toutes."""
    wanted = query.identity.get("source")
    known = provider.slugs()
    if wanted is None:
        return known
    if wanted not in known:
        raise DocumentError(
            "write_identity_invalid",
            "source",
            f"La source « {wanted} » n'existe pas dans l'espace "
            "(list_documents, type de document « source »).",
            received=wanted,
        )
    return [str(wanted)]


class _Application:
    """La cartographie applicative d'une source, calculée comme la vue « Application »
    de l'Explorateur (même lecture de raw, mêmes règles) : ce que rendait
    `list_modules` pour chaque module, et les objets qui appartiennent à chacun."""

    def __init__(
        self,
        source: str,
        entries: SourceEntries,
        read: Callable[[str], str | None] | None = None,
        load: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        view = application_view(entries)
        modules: list[dict[str, Any]] = view["modules"]
        self.source, self.entries, self.modules = source, entries, modules
        # Les `.extend.yaml` de `raw/` : leur texte (fichiers) ou déjà lus (index).
        self._read = read or (lambda _name: None)
        self._load = load
        by_id = {m["id"]: m for m in modules}
        members = members_by_module(entries)

        def ref(module: dict[str, Any]) -> dict[str, str]:
            return {"name": module["file"], "title": module["title"]}

        self.summaries = {
            m["file"]: {
                "nature": m["nature"],
                "targets": m["targets"],
                "language": m["language"],
                "project": m["project"],
                "counts": {
                    "classes": m["counts"]["classes"],
                    "entry_points": m["counts"]["entry_points"],
                    "public_methods": m["counts"]["methods"],
                },
                "depends_on": [
                    ref(by_id[d["id"]])
                    if d["id"] in by_id
                    else {"project": d["project"], "external": True}
                    for d in m["dependencies"]
                ],
                "used_by": [ref(by_id[d]) for d in m["dependents"] if d in by_id],
            }
            for m in modules
        }
        self.members = {m["file"]: {o.file for o in members[m["id"]]} for m in modules}
        self.titles = {m["title"].casefold(): m["file"] for m in modules}

    @classmethod
    def read(cls, root: Path, source: str) -> _Application:
        """La cartographie applicative d'une source, lue dans les fichiers."""
        raw = root / source_rel_dir(source) / RAW_DIR

        def text(name: str) -> str | None:
            file = raw / name
            return file.read_text(encoding="utf-8") if file.is_file() else None

        return cls(source, collect_source_entries(root, source), read=text)

    @classmethod
    def indexed(cls, source: IndexedSource) -> _Application:
        """La cartographie applicative d'une source, lue dans l'index des documents."""
        return cls(source.slug, source.entries(), load=source.extension_document)

    def members_of(self, source: str, wanted: str) -> set[str]:
        """Les objets d'un module, désigné par son nom de schéma ou son titre."""
        return self.members[self.module_name(source, wanted)]

    def module_name(self, source: str, wanted: str) -> str:
        """Le nom de schéma d'un module désigné par son nom ou son titre."""
        name = wanted if wanted in self.members else self.titles.get(wanted.casefold())
        if name is None:
            raise DocumentError(
                "list_filter_invalid",
                "$.filters.module",
                f"« {wanted} » n'est pas un module de la source « {source} » "
                "(list_documents, type de document « schema_raw », filters {kind: 'module'}).",
                received=wanted,
                expected=sorted(self.members),
            )
        return name

    def entry_points(self) -> list[tuple[dict[str, str], dict[str, Any]]]:
        """Les points d'entrée de chaque module, comme l'aperçu d'un module de la vue
        « Application » (`module_detail`) : (identité, contenu)."""
        out = []
        for module in self.modules:
            detail = module_detail(self.entries, module["file"], self._read, self._load) or {}
            out += [_entry_point(p, module) for p in detail.get("entry_points", [])]
        return out


def _entry_point(
    point: dict[str, Any], module: dict[str, Any]
) -> tuple[dict[str, str], dict[str, Any]]:
    """Un point d'entrée : son identité (le schéma qui le porte, la méthode, et pour
    une route HTTP le verbe et la route ; jamais un chemin de fichier) et ce que
    rendait `list_entry_points`."""
    contract = point.get("contract")
    owner = point.get("owner_file") or module["file"]
    kind = str(point.get("kind") or "")
    verb_route = f"{point.get('verb') or ''} {point.get('route') or ''}".strip()
    endpoint = (verb_route or kind) if kind == "http" else kind
    # « * » : la classe elle-même est le point d'entrée (aucune méthode).
    identity = {"owner": owner, "method": point.get("method") or "*", "endpoint": endpoint}
    content = {
        "kind": point.get("kind"),
        "verb": point.get("verb"),
        "route": point.get("route"),
        "method": point.get("method"),
        "owner": point.get("owner"),
        "owner_name": owner,
        "module": module["title"],
        "module_name": module["file"],
        "contract_status": contract["status"] if contract else "not_compared",
        "contract_operations": (contract or {}).get("operations") or [],
    }
    return identity, content


def _extension_path(source: str, owner: str) -> str:
    return f"{source_rel_dir(source)}/{RAW_DIR}/{extend_file(raw_key_of(owner))}"


def entry_points(root: Path, query: ListQuery) -> list[Item]:
    """Ce que rendait `list_entry_points` (ticket 15c43ed1) : les points d'entrée d'une
    source, filtrables par module, par état au contrat et par nature."""
    return _entry_points(_FileSources(root), query)


def entry_points_indexed(snap: Snapshot, query: ListQuery) -> list[Item]:
    """`entry_points`, tirée de l'index des documents (ticket 4618ee38)."""
    return _entry_points(_IndexSources(snap), query)


def _entry_points(provider: _Provider, query: ListQuery) -> list[Item]:
    wanted_module = _module_filter(query)
    status = _given(query, "contract_status")
    nature = _given(query, "kind")
    out = []
    for source in _sources(provider, query):
        app = provider.application(source)
        module = app.module_name(source, wanted_module) if wanted_module else None
        for identity, content in app.entry_points():
            if module is not None and content["module_name"] != module:
                continue
            if status is not None and content["contract_status"] != status:
                continue
            if nature is not None and content["kind"] != nature:
                continue
            carrier = content["owner"] or content["module"]
            name = f"{carrier}.{identity['method']}"
            if content["kind"] == "http":
                name = identity["endpoint"]
            out.append(
                Item(
                    {"source": source, **identity},
                    name,
                    _extension_path(source, identity["owner"]),
                    content,
                    text=f"{content['method'] or ''} {content['owner'] or ''} {content['module']}",
                    version=value_version(content),
                )
            )
    return out


class EntryPointStore(ReadOnlyStore):
    """Un point d'entrée, lu comme la liste le calcule (une entrée du `.extend.yaml` de
    son porteur ; sa version est celle de son contenu)."""

    def read(self, root: Path, location: Location) -> Document | None:
        source = str(location.identity["source"])
        for identity, content in _Application.read(root, source).entry_points():
            if {"source": source, **identity} == dict(location.identity):
                return Document(content, value_version(content), location.path or "")
        return None


def _module_filter(query: ListQuery) -> Any:
    wanted = _given(query, "module")
    if wanted is not None and "source" not in query.identity:
        raise DocumentError(
            "list_filter_invalid",
            "$.filters.module",
            "« module » désigne un module d'une source : donner aussi identity.source.",
        )
    return wanted


def raw_schemas(root: Path, query: ListQuery) -> list[Item]:
    """Ce que rendait `list_raw_schemas` : chaque schéma collecté, son nombre de champs
    et s'il est retenu en asset. Un module porte en plus ce que rendait `list_modules`
    (ticket dcc52af7) ; filtres `kind` (nature de l'objet) et `module` (ses objets)."""
    return _raw_schemas(_FileSources(root), query)


def raw_schemas_indexed(snap: Snapshot, query: ListQuery) -> list[Item]:
    """`raw_schemas`, tirée de l'index des documents (ticket 4618ee38)."""
    return _raw_schemas(_IndexSources(snap), query)


def _raw_schemas(provider: _Provider, query: ListQuery) -> list[Item]:
    wanted_kind = _given(query, "kind")
    wanted_module = _module_filter(query)
    out = []
    for source in _sources(provider, query):
        directory = source_rel_dir(source)
        objects = [o for o in provider.curation(source, (), True) if not o.orphan]
        needed = wanted_module is not None or any(o.kind == MODULE_KIND for o in objects)
        app = provider.application(source) if needed else None
        members = app.members_of(source, wanted_module) if app and wanted_module else None
        for obj in objects:
            if wanted_kind is not None and obj.kind != wanted_kind:
                continue
            if members is not None and obj.path not in members:
                continue
            summary = {"title": obj.title, "kind": obj.kind, "fields": obj.fields}
            module = app.summaries.get(obj.path, {}) if app else {}
            path = f"{directory}/{RAW_DIR}/{obj.path}"
            out.append(
                Item(
                    {"source": source, "name": obj.path},
                    obj.title or obj.path,
                    path,
                    {**summary, "retained": obj.retained, **module},
                    version=provider.version(path),
                )
            )
    return out


def _functions_summary(file: Path) -> dict[str, Any]:
    try:
        data = load_schema(file.read_bytes())
    except SchemaReadError:
        data = None
    if not isinstance(data, dict):
        return {"readable": False}
    return {
        "methods": len(data.get("methods") or []),
        "functions": len(data.get("functions") or []),
        "entry_point": isinstance(data.get("entryPoint"), dict),
    }


def raw_extensions(root: Path, query: ListQuery) -> list[Item]:
    """Les objets raw qui ont une extension (`<clé>.extend.yaml` : méthodes d'une classe,
    fonctions libres d'un module), ce que lisait `get_functions` (ticket 2c069134).
    Filtre `module` : les objets d'un module (la règle de l'Explorateur)."""
    return _raw_extensions(_FileSources(root), query)


def raw_extensions_indexed(snap: Snapshot, query: ListQuery) -> list[Item]:
    """`raw_extensions`, tirée de l'index des documents (ticket 4618ee38)."""
    return _raw_extensions(_IndexSources(snap), query)


def _raw_extensions(provider: _Provider, query: ListQuery) -> list[Item]:
    wanted_module = _module_filter(query)
    out = []
    for source in _sources(provider, query):
        directory = f"{source_rel_dir(source)}/{RAW_DIR}"
        objects = [o for o in provider.curation(source, (), True) if not o.orphan]
        members = (
            provider.application(source).members_of(source, wanted_module)
            if (wanted_module is not None)
            else None
        )
        for obj in objects:
            if members is not None and obj.path not in members:
                continue
            extension = f"{directory}/{extend_file(raw_key_of(obj.path))}"
            summary = provider.extension(extension)
            if summary is None:
                continue
            out.append(
                Item(
                    {"source": source, "name": obj.path},
                    obj.title or obj.path,
                    extension,
                    {"title": obj.title, "kind": obj.kind, **summary},
                    version=provider.version(extension),
                )
            )
    return out


def asset_schemas(root: Path, query: ListQuery) -> list[Item]:
    """Ce que rendait `list_assets` : chaque asset retenu, orphelin ou non (son raw a
    disparu), et la règle de tri qui l'écarte."""
    return _asset_schemas(_FileSources(root), query)


def asset_schemas_indexed(snap: Snapshot, query: ListQuery) -> list[Item]:
    """`asset_schemas`, tirée de l'index des documents (ticket 4618ee38)."""
    return _asset_schemas(_IndexSources(snap), query)


def _asset_schemas(provider: _Provider, query: ListQuery) -> list[Item]:
    out = []
    for source in _sources(provider, query):
        directory = source_rel_dir(source)
        for obj in provider.curation(source, provider.tri_rules(source), True):
            if obj.retained == "none":
                continue
            base = f"{directory}/{ASSETS_DIR}"
            summary = {
                "title": obj.title,
                "kind": obj.kind,
                "fields": obj.fields,
                "retained": obj.retained,
                "orphan": obj.orphan,
                "excluded_by": obj.excluded_by,
            }
            out.append(
                Item(
                    {"source": source, "name": obj.path},
                    obj.title or obj.path,
                    f"{base}/{obj.path}",
                    summary,
                    version=provider.version(f"{base}/{obj.path}"),
                    # Le travail humain sur l'asset (non publié s'il l'est).
                    related=(f"{base}/{extend_name(obj.path)}",),
                )
            )
    return out


# --- Règles (ticket 71347227) ------------------------------------------------------------


def _rule_items(
    root: Path, store: Any, builtins: Any, files: Any, state_file: str, extra: Any
) -> list[Item]:
    """Les règles natives (fournies par Ossie, leur état dans le dépôt) puis celles du
    dépôt, valides ou non (une règle invalide porte ses erreurs)."""
    out = []
    for rule in builtins:
        doc = store.native_document(root, rule.id)
        summary = {"native": True, "enabled": doc["enabled"], **extra(rule), "errors": []}
        out.append(
            Item({"id": rule.id}, rule.name, state_file, summary, version=value_version(doc))
        )
    for found in files:
        rule_id = PurePosixPath(found.rel).stem
        rule = found.rule
        summary = {
            "native": False,
            "enabled": rule.enabled if rule else None,
            **(extra(rule) if rule else {}),
            "errors": list(found.errors),
        }
        out.append(Item({"id": rule_id}, rule.name if rule else rule_id, found.rel, summary))
    return out


def tag_rule_items(root: Path, query: ListQuery) -> list[Item]:
    return _rule_items(
        root,
        TagRuleStore(),
        builtin_rules(),
        read_tag_rules(root),
        tag_rule_service.BUILTIN_STATE_FILE,
        lambda r: {"tags": list(r.tags)},
    )


def classification_rule_items(root: Path, query: ListQuery) -> list[Item]:
    schemes = read_schemes(root)

    def extra(rule: Any) -> dict[str, Any]:
        return {
            "scheme": rule.scheme,
            "values": list(rule.values),
            "effect_errors": classification_rule_service.effect_errors(rule, schemes),
        }

    return _rule_items(
        root,
        ClassificationRuleStore(),
        builtin_classification_rules(),
        read_classification_rules(root),
        classification_rule_service.BUILTIN_STATE_FILE,
        extra,
    )


def tri_rules(root: Path, query: ListQuery) -> list[Item]:
    """Les règles de tri de chaque source (la section `triage` de son manifeste)."""
    out = []
    for source in _sources(_FileSources(root), query):
        rules = read_tri_rules_raw(root, source)
        out.append(
            Item(
                {"source": source},
                source,
                f"{source_rel_dir(source)}/{MANIFEST_FILENAME}",
                {"rules": len(rules)},
                version=value_version(rules),
            )
        )
    return out


# --- Gouvernance --------------------------------------------------------------------------


def relation_profile_items(root: Path, query: ListQuery) -> list[Item]:
    """Les profils de relations, comme l'écran les liste : le profil fourni n'apparaît
    qu'une fois (sa version de l'espace s'il en a une), puis ceux de l'espace."""
    files = {f.rel.rsplit("/", 1)[-1].removesuffix(".yaml"): f for f in read_profile_files(root)}
    builtins = {p.id: p for p in builtin_profiles()}
    out = []
    for profile_id in [*builtins, *sorted(set(files) - set(builtins))]:
        file = files.get(profile_id)
        profile = file.profile if file else builtins[profile_id]
        summary: dict[str, Any] = {
            "builtin": profile_id in builtins,
            "customized": file is not None and profile_id in builtins,
        }
        if profile is None:
            summary["errors"] = list(file.errors) if file else []
        else:
            summary |= {"name": profile.name, "rules": len(profile.rules)}
        doc = relation_profile_document(profile) if profile else None
        out.append(
            Item(
                {"id": profile_id},
                profile.name if profile else profile_id,
                relation_profile_path(profile_id),
                summary,
                text=profile.description if profile else "",
                # Le profil fourni non personnalisé n'a pas de fichier : la version de
                # son contenu, comme à la lecture.
                version=value_version(doc) if file is None and doc else None,
            )
        )
    return out


def tags(root: Path, query: ListQuery) -> list[Item]:
    out = []
    for tag in read_tags(root):
        summary = {k: v for k, v in asdict(tag).items() if k != "id"}
        out.append(Item({"id": tag.id}, tag.id, tag_path(tag.id), summary, text=tag.description))
    return out


def classifications(root: Path, query: ListQuery) -> list[Item]:
    out = []
    for scheme in read_schemes(root):
        summary = {k: v for k, v in asdict(scheme).items() if k not in ("id", "name")}
        out.append(
            Item(
                {"id": scheme.id},
                scheme.name,
                scheme_path(scheme.id),
                summary,
                text=scheme.description,
            )
        )
    return out


def certifications(root: Path, query: ListQuery) -> list[Item]:
    out = []
    for cert in read_certs(root):
        summary = {k: v for k, v in asdict(cert).items() if k not in ("slug", "name")}
        out.append(
            Item({"slug": cert.slug}, cert.name, cert_path(cert.id), summary, text=cert.description)
        )
    return out
