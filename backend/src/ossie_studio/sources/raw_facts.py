"""Les faits d'un fichier raw ou asset, gardés dans l'index des documents, et les sources
reconstruites depuis l'index (ticket 4618ee38, epic « Index des documents et validation
avant publication »).

Gael (28 novembre 2026) : tout mettre dans l'index des documents, pour qu'il n'y ait
qu'un seul index à terme. Les listes de l'agent (schémas raw, assets, extensions,
points d'entrée) se calculent alors sans relire `raw/` ni `assets/`.

**Faits** (`file_facts`) : fonctions pures du chemin et du contenu, calculées par les
fonctions de l'Explorateur et de la curation (`_summarize`, `function_counts`,
`anchors_of`, `read_tech_manifest`, `docs_of`, `contracts_of`, `source_meta`). Jamais
de contenu entier : pour un schéma, son résumé et ce que lisent la rétention et les
règles de tri ; pour une extension, ses compteurs et ses seuls points d'entrée.

**Sources reconstruites** (`IndexedSources`) : depuis les fichiers d'une vue de
l'index, la même `RawLayer` que les fichiers donnent à `build_source_entries`, les
mêmes entrées de curation pour `curation_objects` et les mêmes extensions pour
`module_detail`. Une seule règle, deux façons de la nourrir.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from ossie_studio.parsers.conventions import (
    SCHEMA_SUFFIX,
    SchemaReadError,
    is_schema_name,
    load_schema,
)
from ossie_studio.parsers.markdown import DOCS_INDEX
from ossie_studio.parsers.openapi_full import CONTRACTS_INDEX
from ossie_studio.sources.collect_core import RAW_DIR, TechEntry, TechManifest
from ossie_studio.sources.curation import CurationObject, curation_objects
from ossie_studio.sources.explorer import (
    ExplorerContract,
    ExplorerDocument,
    FunctionCounts,
    ModuleFacts,
    RawLayer,
    SchemaRef,
    SchemaSummary,
    SourceEntries,
    SourceMeta,
    _summarize,
    anchors_of,
    build_source_entries,
    contracts_of,
    docs_of,
    function_counts,
    is_tech_manifest_name,
    read_tech_manifest,
    source_meta,
    tech_origins,
)
from ossie_studio.sources.paths import ASSETS_DIR, MANIFEST_FILENAME, SOURCES_DIR
from ossie_studio.sources.tri import TriRule, compile_rules

EXTEND_SUFFIX = ".extend.yaml"
VIEW_SUFFIX = ".view.yaml"
# Ce que lisent l'état de rétention (noms des propriétés) et les règles de tri
# (`_object_values` : titre, `$id`, nature, fichiers d'origine) d'un schéma.
_REDUCED_KEYS = ("title", "$id", "x-kind", "x-source")


# --- Les faits d'un fichier --------------------------------------------------------------


def _text(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _load(data: bytes) -> Any:
    text = _text(data)
    if text is None:
        return None
    try:
        return load_schema(text)
    except SchemaReadError:
        return None


def reduced_schema(schema: Any) -> dict[str, Any] | None:
    """Ce que la curation et les règles de tri lisent d'un schéma ; None s'il est
    illisible (pas un objet)."""
    if not isinstance(schema, dict):
        return None
    out = {k: schema[k] for k in _REDUCED_KEYS if k in schema}
    props = schema.get("properties")
    if props is not None:
        out["properties"] = {k: True for k in props} if isinstance(props, dict) else props
    return out


def functions_summary(document: Any) -> dict[str, Any]:
    """Ce que la liste des extensions raw montre d'un `.extend.yaml` lu."""
    if not isinstance(document, dict):
        return {"readable": False}
    return {
        "methods": len(document.get("methods") or []),
        "functions": len(document.get("functions") or []),
        "entry_point": isinstance(document.get("entryPoint"), dict),
    }


def entry_document(document: Any) -> dict[str, Any]:
    """Ce que `module_detail` lit d'un `.extend.yaml` pour ses points d'entrée : le
    point de l'objet, les fonctions et les méthodes publiques qui en portent un."""
    if not isinstance(document, dict):
        return {}
    out: dict[str, Any] = {}
    if isinstance(document.get("entryPoint"), dict):
        out["entryPoint"] = document["entryPoint"]
    functions = [
        {"name": f.get("name"), "source": f.get("source"), "entryPoint": f["entryPoint"]}
        for f in document.get("functions") or []
        if isinstance(f, dict) and isinstance(f.get("entryPoint"), dict)
    ]
    methods = [
        {"name": m.get("name"), "visibility": "public", "entryPoint": m["entryPoint"]}
        for m in document.get("methods") or []
        if isinstance(m, dict)
        and m.get("visibility") == "public"
        and isinstance(m.get("entryPoint"), dict)
    ]
    if functions:
        out["functions"] = functions
    if methods:
        out["methods"] = methods
    return out


def _summary_json(summary: SchemaSummary) -> dict[str, Any]:
    return asdict(summary)


def _summary_from_json(data: Mapping[str, Any]) -> SchemaSummary:
    module = data.get("module")
    return SchemaSummary(
        title=str(data.get("title") or ""),
        kind=str(data.get("kind") or ""),
        fields=int(data.get("fields") or 0),
        origins=tuple(data.get("origins") or ()),
        blob=str(data.get("blob") or ""),
        error=bool(data.get("error")),
        schema_id=str(data.get("schema_id") or ""),
        visibility=str(data.get("visibility") or ""),
        visibility_source=str(data.get("visibility_source") or ""),
        refs=tuple(SchemaRef(**r) for r in data.get("refs") or ()),
        description=str(data.get("description") or ""),
        top_fields=tuple(data.get("top_fields") or ()),
        annotations=tuple(data.get("annotations") or ()),
        field_names=tuple(data.get("field_names") or ()),
        namespace=str(data.get("namespace") or ""),
        field_types=tuple(tuple(t) for t in data.get("field_types") or ()),
        module=ModuleFacts(
            project=module["project"],
            nature=module["nature"],
            language=module["language"],
            targets=tuple(module.get("targets") or ()),
            dependencies=tuple((d[0], d[1]) for d in module.get("dependencies") or ()),
            anchors=tuple((a[0], a[1], a[2]) for a in module.get("anchors") or ()),
        )
        if isinstance(module, dict)
        else None,
    )


def _counts_json(counts: FunctionCounts) -> list[Any]:
    return [
        counts.methods,
        counts.entry_points,
        counts.http,
        counts.functions,
        counts.linked,
        [list(o) for o in counts.operations],
    ]


def _tech_json(tech: TechManifest | None) -> list[list[Any]] | None:
    if tech is None:
        return None
    return [
        [source, e.checksum, list(e.destinations), e.branch, e.parser]
        for source, e in sorted(tech.entries.items())
    ]


def _tech_from_json(rows: list[list[Any]] | None) -> TechManifest | None:
    if rows is None:
        return None
    return TechManifest(
        entries={
            r[0]: TechEntry(checksum=r[1], destinations=list(r[2]), branch=r[3], parser=r[4])
            for r in rows
        }
    )


def _source_meta_json(text: str | None) -> dict[str, Any] | None:
    """Ce que l'explorateur lit du manifeste, indépendamment du slug (le libellé par
    défaut est le slug : on garde le libellé tel qu'écrit, ou None)."""
    if text is None:
        return None
    probe = "\x00slug\x00"
    meta = source_meta(text, probe)
    return {
        "label": None if meta.label == probe else meta.label,
        "type": meta.type,
        "database": meta.database,
        "rules": list(meta.rules),
    }


def file_facts(path: str, data: bytes) -> dict[str, Any] | None:
    """Les faits d'un fichier de `sources/<source>/` que l'index garde ; None pour les
    autres fichiers. Plusieurs rôles possibles pour un même fichier (un schéma nommé
    `manifest-….schema.yaml` est aussi lu comme manifeste technique)."""
    parts = path.split("/")
    if len(parts) < 3 or parts[0] != SOURCES_DIR:
        return None
    rel = "/".join(parts[2:])
    folder, _sep, name = rel.partition("/")
    out: dict[str, Any] = {}
    if rel == MANIFEST_FILENAME:
        out["source_manifest"] = _source_meta_json(_text(data))
    elif folder == RAW_DIR and name and "/" not in name:
        if is_schema_name(name):
            out["raw_schema"] = {
                "summary": _summary_json(_summarize(data)),
                "doc": reduced_schema(_load(data)),
            }
        if name.endswith(VIEW_SUFFIX):
            out["raw_view"] = [list(a) for a in anchors_of(_text(data))]
        if name.endswith(EXTEND_SUFFIX):
            document = _load(data)
            out["raw_extend"] = {
                "counts": _counts_json(function_counts(document)),
                "summary": functions_summary(document),
                "doc": entry_document(document),
            }
        if is_tech_manifest_name(name):
            text = _text(data)
            out["tech_manifest"] = _tech_json(read_tech_manifest(text) if text else None)
        if name == DOCS_INDEX:
            text = _text(data)
            out["docs"] = (
                [[d.path, d.title, d.sections, list(d.mentions)] for d in docs_of(text)]
                if text is not None
                else []
            )
        if name == CONTRACTS_INDEX:
            text = _text(data)
            out["contracts"] = (
                [[c.path, c.source, c.title, c.version, c.operations] for c in contracts_of(text)]
                if text is not None
                else []
            )
    elif folder == ASSETS_DIR and name and "/" not in name and is_schema_name(name):
        out["asset_schema"] = reduced_schema(_load(data))
    return out or None


# --- Les sources reconstruites depuis l'index ----------------------------------------------


Files = Mapping[str, tuple[str, dict[str, Any] | None]]


class IndexedSource:
    """Une source vue par l'index : ses fichiers (chemin relatif à `sources/<slug>/`
    → empreinte et faits)."""

    def __init__(self, slug: str, files: Files) -> None:
        self.slug = slug
        self.files = dict(files)

    def _facts(self, rel: str, role: str) -> Any:
        found = self.files.get(rel)
        if found is None or found[1] is None:
            return None
        return found[1].get(role)

    def _top(self, folder: str) -> list[str]:
        """Les noms des fichiers posés directement dans `folder/`, triés."""
        prefix = f"{folder}/"
        return sorted(
            rel[len(prefix) :]
            for rel in self.files
            if rel.startswith(prefix) and "/" not in rel[len(prefix) :]
        )

    def meta(self) -> SourceMeta:
        if MANIFEST_FILENAME not in self.files:
            return SourceMeta(self.slug, "", "", [])
        facts = self._facts(MANIFEST_FILENAME, "source_manifest")
        if facts is None:
            return SourceMeta(self.slug, "", "", [])
        return SourceMeta(
            label=str(facts["label"]) if facts.get("label") is not None else self.slug,
            type=str(facts.get("type") or ""),
            database=str(facts.get("database") or ""),
            rules=list(facts.get("rules") or []),
        )

    def _collected(self) -> bool:
        return any(rel.startswith(f"{RAW_DIR}/") for rel in self.files)

    def layer(self) -> RawLayer | None:
        if not self._collected():
            return None
        raw = self._top(RAW_DIR)

        def facts(name: str, role: str) -> Any:
            return self._facts(f"{RAW_DIR}/{name}", role)

        schemas = []
        docs: dict[str, dict[str, Any]] = {}
        for name in raw:
            found = facts(name, "raw_schema")
            if is_schema_name(name) and found is not None:
                schemas.append((name, _summary_from_json(found["summary"])))
                docs[name] = found.get("doc") or {}

        def stem(name: str) -> str:
            return name[: -len(SCHEMA_SUFFIX)]

        def anchors(name: str) -> tuple[tuple[str, str, str], ...]:
            found = facts(f"{stem(name)}{VIEW_SUFFIX}", "raw_view") or []
            return tuple((a[0], a[1], a[2]) for a in found)

        def functions(name: str) -> FunctionCounts:
            found = facts(f"{stem(name)}{EXTEND_SUFFIX}", "raw_extend")
            return FunctionCounts.from_json(found["counts"]) if found else FunctionCounts()

        manifests = [
            (name, _tech_from_json(facts(name, "tech_manifest")))
            for name in raw
            if is_tech_manifest_name(name)
        ]
        return RawLayer(
            schemas=schemas,
            origins=tech_origins(manifests),
            retained=set(self._top(ASSETS_DIR)),
            full_schema=lambda name: docs.get(name, {}),
            anchors=anchors,
            functions=functions,
            documents=[
                ExplorerDocument(d[0], d[1], int(d[2]), tuple(d[3]))
                for d in facts(DOCS_INDEX, "docs") or []
            ],
            contracts=[
                ExplorerContract(c[0], c[1], c[2], c[3], int(c[4]))
                for c in facts(CONTRACTS_INDEX, "contracts") or []
            ],
        )

    def entries(self) -> SourceEntries:
        """Comme `collect_source_entries(workdir, slug)` (sans badges de point de
        reprise)."""
        return build_source_entries(self.slug, self.meta(), self.layer())

    def tri_rules(self) -> list[TriRule]:
        """Comme `read_tri_rules` : les règles d'un manifeste conforme (un manifeste
        absent, illisible ou non conforme n'en a aucune, comme pour `source_meta`)."""
        return compile_rules(self.meta().rules)

    def curation(self, rules: list[TriRule], show_excluded: bool) -> list[CurationObject]:
        """Comme `list_curation(workdir, slug, rules, show_excluded)`."""
        raws = {
            name: doc
            for name in self._top(RAW_DIR)
            if is_schema_name(name)
            and (doc := (self._facts(f"{RAW_DIR}/{name}", "raw_schema") or {}).get("doc"))
            is not None
        }
        assets = {
            name: doc
            for name in self._top(ASSETS_DIR)
            if is_schema_name(name)
            and (doc := self._facts(f"{ASSETS_DIR}/{name}", "asset_schema")) is not None
        }
        return curation_objects(raws, assets, rules, show_excluded)

    def extension(self, name: str) -> tuple[str, dict[str, Any]] | None:
        """(empreinte, faits) du `.extend.yaml` de `raw/` nommé `name` ; None s'il
        n'existe pas."""
        found = self.files.get(f"{RAW_DIR}/{name}")
        if found is None:
            return None
        return found[0], (found[1] or {}).get("raw_extend") or {}

    def extension_document(self, name: str) -> dict[str, Any]:
        """Ce que `module_detail` lit d'un `.extend.yaml` de `raw/` ({} s'il manque)."""
        found = self.extension(name)
        return dict(found[1].get("doc") or {}) if found else {}


class IndexedSources:
    """Les sources d'une vue de l'index (fichiers sous `sources/`)."""

    def __init__(self, files: Files) -> None:
        by_source: dict[str, dict[str, tuple[str, dict[str, Any] | None]]] = {}
        for path, found in files.items():
            parts = path.split("/")
            if len(parts) < 3 or parts[0] != SOURCES_DIR:
                continue
            by_source.setdefault(parts[1], {})["/".join(parts[2:])] = found
        self._sources = by_source

    def slugs(self) -> list[str]:
        """Comme `read_sources_tree` : chaque dossier de `sources/`, sauf celui d'une
        source supprimée qui ne garde que ses assets."""
        out = []
        for slug, files in sorted(self._sources.items()):
            assets_only = MANIFEST_FILENAME not in files and all(
                rel.startswith(f"{ASSETS_DIR}/") for rel in files
            )
            if not assets_only:
                out.append(slug)
        return out

    def source(self, slug: str) -> IndexedSource:
        return IndexedSource(slug, self._sources.get(slug, {}))
