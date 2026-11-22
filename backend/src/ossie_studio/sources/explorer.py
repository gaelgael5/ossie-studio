"""Explorateur raw : index en lecture seule de la couche raw d'une source.

L'explorateur parcourt `sources/<slug>/raw/` comme un explorateur de fichiers.
Les schémas sont **à plat** à la racine de `raw/` (un fichier par objet) : les
« dossiers » affichés ne sont donc pas des répertoires de `raw/`, ils sont
**calculés** selon un mode d'affichage :

- `origin` : l'arborescence d'origine — dossiers du dépôt (Git, d'après les
  manifestes techniques), base > schéma (catalogue), contrat (OpenAPI) ;
- `kind` : la nature de l'objet (`x-kind`) ;
- `parser` : le parseur qui l'a produit.

Rien n'est jamais écrit dans la copie. `collect_source_entries` lit la couche
raw (une fois par changement : le résultat est mis en index en base, voir
`explorer_store`) ; `index_from_entries` range ensuite, à chaque requête et sans
toucher au disque, les objets en dossiers pour le mode demandé. La lecture
elle-même ne relit un schéma que si son fichier a changé (cache par chemin,
taille et date de modification).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from ossie_studio.parsers.conventions import CODE_LANGUAGE_KEY, VISIBILITIES, VISIBILITY_KEY
from ossie_studio.parsers.markdown import DOCS_INDEX, parse_docs_index
from ossie_studio.sources.collect_core import RAW_DIR, TECH_MANIFEST_NAME, parse_tech_manifest
from ossie_studio.sources.manifest import ManifestError, parse_manifest
from ossie_studio.sources.paths import (
    ASSETS_DIR,
    MANIFEST_FILENAME,
    SOURCES_DIR,
    safe_source_path,
)
from ossie_studio.sources.tri import TriRule, compile_rules, object_excluded, rule_label

MODES: tuple[str, ...] = ("origin", "kind", "parser")
GIT_SOURCE_TYPE = "git"
# Version du format des entrées mises en index : la changer (champ ajouté…)
# force la reconstruction des index existants à la livraison suivante.
INDEX_FORMAT = "4"
DEFAULT_MODE = "origin"

# Badges d'état (clés stables, traduites côté interface).
BADGE_RETAINED = "retained"
BADGE_NEW = "new"
BADGE_MODIFIED = "modified"
BADGE_EXCLUDED = "excluded"
BADGE_ERROR = "error"

# Dossier virtuel des documents dans les modes `kind` et `parser`.
DOCUMENTS_FOLDER = "documents"
MARKDOWN_PARSER = "markdown"

_SEARCH_LIMIT = 50


@dataclass(frozen=True)
class SchemaRef:
    """Une référence sortante lue dans un schéma, avec sa cardinalité (côté
    objet qui porte le champ). `target` : `$ref` (un `$id`), table SQL
    `schéma.table` (clé étrangère), ou nom tel qu'écrit si `external`."""

    field: str  # "" pour l'héritage
    kind: str  # field | list | map | foreign_key | inherits
    target: str
    cardinality: str  # 1 | 0..1 | 1..N | 0..N | "" (héritage)
    external: bool = False


@dataclass(frozen=True)
class SchemaSummary:
    """Ce que l'explorateur retient d'un schéma (relu seulement s'il change)."""

    title: str
    kind: str
    fields: int
    origins: tuple[str, ...]  # fichiers d'origine lus sous `x-source`
    blob: str  # identifiant de blob git du contenu (comparaison au point de reprise)
    error: bool  # JSON illisible ou pas un objet
    schema_id: str = ""  # `$id` : cible des `$ref` des autres schémas
    visibility: str = ""  # x-visibility ; "unknown" pour du code qui n'en porte pas
    visibility_source: str = ""  # x-visibility-source (mot-clé tel qu'écrit)
    refs: tuple[SchemaRef, ...] = ()  # références sortantes (vue graphe)
    description: str = ""  # description courte (info-bulle du graphe)
    top_fields: tuple[str, ...] = ()  # premiers champs (info-bulle du graphe)


@dataclass(frozen=True)
class ExplorerObject:
    file: str  # nom de fichier du schéma, racine de raw/
    title: str
    kind: str
    fields: int
    origin: str  # fichier d'origine principal (ou "")
    parser: str  # parseur producteur (ou "")
    badges: tuple[str, ...]
    excluded_by: str  # libellé de la règle de tri qui l'écarte (ou "")
    schema_id: str = ""  # `$id` du schéma (résolution des références)
    checksum: str = ""  # empreinte du fichier d'origine (manifeste technique)
    branch: str = ""  # branche qui a mis le schéma à jour (manifeste technique)
    visibility: str = ""  # public | internal | protected | private | unknown | "" (SQL, contrat)
    visibility_source: str = ""  # mot-clé d'origine, s'il diffère de la valeur normalisée
    refs: tuple[SchemaRef, ...] = ()
    description: str = ""
    top_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExplorerDocument:
    path: str  # chemin sous raw/docs/ (= chemin dans le dépôt)
    title: str
    sections: int
    mentions: tuple[str, ...] = ()  # identifiants cités (rapprochés des objets)


@dataclass
class _Folder:
    path: str
    folders: set[str] = field(default_factory=set)
    objects: list[ExplorerObject] = field(default_factory=list)
    documents: list[ExplorerDocument] = field(default_factory=list)


@dataclass
class SourceIndex:
    """Index d'une source pour un mode d'affichage donné."""

    slug: str
    label: str
    type: str
    mode: str
    collected: bool  # raw/ existe (la source a été collectée au moins une fois)
    objects: list[ExplorerObject]
    documents: list[ExplorerDocument]
    folders: dict[str, _Folder]


# --- Cache des schémas -------------------------------------------------------

# Clé : chemin absolu ; valeur : (taille, mtime_ns, résumé). Borné : une instance
# sert plusieurs espaces et branches, on ne garde pas tout indéfiniment.
_SCHEMA_CACHE: dict[str, tuple[int, int, SchemaSummary]] = {}
_SCHEMA_CACHE_MAX = 200_000
# Blobs de `raw/` au point de reprise : immuables pour un commit donné, donc
# jamais invalidés (seulement bornés).
_REVIEW_CACHE: dict[tuple[str, str], dict[str, str] | None] = {}
_REVIEW_CACHE_MAX = 256
_LOCK = threading.Lock()


def clear_caches() -> None:
    with _LOCK:
        _SCHEMA_CACHE.clear()
        _REVIEW_CACHE.clear()


def git_blob_id(data: bytes) -> str:
    """Identifiant de blob git d'un contenu : comparable à `git ls-tree` sans
    relire le contenu au point de reprise."""
    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def _leaf_strings(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        # `file` en premier : c'est le chemin d'origine (OpenAPI : file + pointer).
        if isinstance(value.get("file"), str):
            out.append(value["file"])
        for key, inner in value.items():
            if key not in ("file", "pointer", "line"):
                out.extend(_leaf_strings(inner))
    elif isinstance(value, list):
        for inner in value:
            out.extend(_leaf_strings(inner))
    return out


VISIBILITY_UNKNOWN = "unknown"
VISIBILITY_SOURCE_KEY = "x-visibility-source"


def visibility_of(node: dict[str, Any]) -> str:
    """Visibilité affichable d'un objet ou d'un champ : `x-visibility` telle que
    lue ; « unknown » pour du code qui n'en porte pas (schéma antérieur aux
    conventions) ; vide pour une table SQL ou un contrat, qui n'en ont pas. On
    ne la déduit jamais d'autre chose (latitude du ticket : s'arrêter et demander)."""
    value = node.get(VISIBILITY_KEY)
    if isinstance(value, str) and value in VISIBILITIES:
        return value
    if CODE_LANGUAGE_KEY in node or value is not None:
        return VISIBILITY_UNKNOWN
    return ""


DESCRIPTION_MAX = 280
TOP_FIELDS = 6


def _ref_of(node: dict[str, Any]) -> tuple[str, bool] | None:
    if "$ref" in node:
        return str(node["$ref"]), False
    if "x-unresolved-type" in node:
        return str(node["x-unresolved-type"]), True
    return None


def schema_refs(schema: dict[str, Any]) -> tuple[SchemaRef, ...]:
    """Références sortantes d'un schéma, cardinalités selon les règles du ticket
    « vue graphe » : `$ref` direct → 1 (requis) ou 0..1 ; tableau d'items `$ref`
    → 1..N (`minItems` ≥ 1) ou 0..N ; dictionnaire → 0..N ; clé étrangère SQL →
    1 (non nullable, donc requise) ou 0..1 ; héritage (`allOf` + `$ref`). Rien
    n'est déduit que le schéma ne porte pas (pas de rapprochement par nom)."""
    out: list[SchemaRef] = []
    for part in schema.get("allOf") or []:
        if isinstance(part, dict) and (found := _ref_of(part)) is not None:
            out.append(SchemaRef("", "inherits", found[0], "", found[1]))
    required = set(schema.get("required") or [])
    props = schema.get("properties")
    for name, prop in props.items() if isinstance(props, dict) else []:
        if not isinstance(prop, dict):
            continue
        items, extra = prop.get("items"), prop.get("additionalProperties")
        if prop.get("type") == "array" and isinstance(items, dict):
            if (found := _ref_of(items)) is not None:
                many = "1..N" if int(prop.get("minItems") or 0) >= 1 else "0..N"
                out.append(SchemaRef(name, "list", found[0], many, found[1]))
        elif prop.get("type") == "object" and isinstance(extra, dict):
            if (found := _ref_of(extra)) is not None:
                out.append(SchemaRef(name, "map", found[0], "0..N", found[1]))
        elif (found := _ref_of(prop)) is not None:
            one = "1" if name in required else "0..1"
            out.append(SchemaRef(name, "field", found[0], one, found[1]))
        fks = prop.get("x-references")
        for fk in fks if isinstance(fks, list) else [fks] if isinstance(fks, dict) else []:
            table = str(fk.get("table") or "")
            if table:
                one = "1" if name in required else "0..1"
                out.append(SchemaRef(name, "foreign_key", table, one))
    return tuple(out)


def _summarize(data: bytes) -> SchemaSummary:
    blob = git_blob_id(data)
    try:
        schema = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return SchemaSummary("", "", 0, (), blob, True)
    if not isinstance(schema, dict):
        return SchemaSummary("", "", 0, (), blob, True)
    props = schema.get("properties")
    return SchemaSummary(
        title=str(schema.get("title") or schema.get("$id") or ""),
        kind=str(schema.get("x-kind") or "object"),
        fields=len(props) if isinstance(props, dict) else 0,
        origins=tuple(dict.fromkeys(_leaf_strings(schema.get("x-source")))),
        blob=blob,
        error=False,
        schema_id=str(schema.get("$id") or ""),
        visibility=visibility_of(schema),
        visibility_source=str(schema.get(VISIBILITY_SOURCE_KEY) or ""),
        refs=schema_refs(schema),
        description=str(schema.get("description") or "")[:DESCRIPTION_MAX],
        top_fields=tuple(list(props)[:TOP_FIELDS]) if isinstance(props, dict) else (),
    )


def schema_summary(path: Path) -> SchemaSummary:
    """Résumé d'un schéma, relu seulement si le fichier a changé."""
    stat = path.stat()
    key = str(path)
    with _LOCK:
        cached = _SCHEMA_CACHE.get(key)
    if cached is not None and cached[0] == stat.st_size and cached[1] == stat.st_mtime_ns:
        return cached[2]
    summary = _summarize(path.read_bytes())
    with _LOCK:
        if len(_SCHEMA_CACHE) >= _SCHEMA_CACHE_MAX:
            _SCHEMA_CACHE.clear()
        _SCHEMA_CACHE[key] = (stat.st_size, stat.st_mtime_ns, summary)
    return summary


def _full_schema(path: Path) -> dict[str, Any]:
    """Schéma complet, lu seulement quand une règle de tri doit s'y appliquer."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


# --- Point de reprise de la revue ---------------------------------------------


def review_blobs(workdir: Path, slug: str, base_sha: str | None) -> dict[str, str] | None:
    """Blobs des schémas de `raw/` au point de reprise (nom → id de blob), ou None
    si la source n'a jamais été revue ou que le commit est illisible. Lit l'arbre
    git (`ls-tree`) sans relire aucun contenu, une fois par commit."""
    if not base_sha:
        return None
    key = (base_sha, slug)
    with _LOCK:
        if key in _REVIEW_CACHE:
            return _REVIEW_CACHE[key]
    rel = f"{SOURCES_DIR}/{slug}/{RAW_DIR}/"
    try:
        # Appel local (aucun accès réseau) ; la vue de brouillon est un worktree
        # qui partage les objets de la copie, le commit y est donc lisible.
        out = subprocess.run(  # noqa: S603 — arguments fixes, sha issu de review.yaml
            ["git", "ls-tree", base_sha, "--", rel],  # noqa: S607
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    blobs: dict[str, str] | None
    if out.returncode != 0:
        blobs = None
    else:
        blobs = {}
        for line in out.stdout.splitlines():
            meta, _, path = line.partition("\t")
            parts = meta.split()
            if len(parts) == 3 and parts[1] == "blob" and path.endswith(".json"):
                blobs[path.rsplit("/", 1)[-1]] = parts[2]
    with _LOCK:
        if len(_REVIEW_CACHE) >= _REVIEW_CACHE_MAX:
            _REVIEW_CACHE.clear()
        _REVIEW_CACHE[key] = blobs
    return blobs


# --- Construction de l'index --------------------------------------------------


@dataclass(frozen=True)
class _SourceMeta:
    label: str
    type: str
    database: str
    rules: list[dict[str, Any]]


def _read_source_meta(source_dir: Path, slug: str) -> _SourceMeta:
    manifest_file = source_dir / MANIFEST_FILENAME
    try:
        text = manifest_file.read_text(encoding="utf-8")
    except OSError:
        return _SourceMeta(slug, "", "", [])
    try:
        manifest = parse_manifest(text)
    except ManifestError:
        # Explorer reste possible avec un manifeste non conforme (paramètre
        # manquant, clé inconnue) : on n'en lit que ce qui sert à l'affichage.
        return _lenient_meta(text, slug)
    return _SourceMeta(
        label=manifest.label or slug,
        type=manifest.type,
        database=str(manifest.parameters.get("database") or ""),
        rules=list(manifest.triage),
    )


def _lenient_meta(text: str, slug: str) -> _SourceMeta:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        data = None
    if not isinstance(data, dict):
        return _SourceMeta(slug, "", "", [])
    params = data.get("parameters", data.get("parametres"))
    params = params if isinstance(params, dict) else {}
    # Les règles d'un manifeste non conforme ne sont pas appliquées : on ne sait
    # pas les valider, mieux vaut un badge absent qu'un badge faux.
    return _SourceMeta(
        label=str(data.get("label") or data.get("libelle") or slug),
        type=str(data.get("type") or ""),
        database=str(params.get("database", params.get("base")) or ""),
        rules=[],
    )


def _tech_origins(raw_dir: Path) -> dict[str, tuple[str, str, str, str]]:
    """Schéma → (fichier d'origine, parseur, checksum, branche), d'après les manifestes techniques
    de `raw/` (`manifest.yaml` et un `manifest-<parseur>.yaml` par plugin)."""
    out: dict[str, tuple[str, str, str, str]] = {}
    index_files = sorted(raw_dir.glob("manifest*.yaml"))
    for index_file in index_files:
        try:
            tech = parse_tech_manifest(index_file.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError, KeyError, TypeError, AttributeError):
            continue
        default_parser = ""
        if index_file.name != TECH_MANIFEST_NAME:
            default_parser = index_file.name.removeprefix("manifest-").removesuffix(".yaml")
        for source, entry in sorted(tech.entries.items()):
            for dest in entry.destinations:
                out.setdefault(
                    dest, (source, entry.parser or default_parser, entry.checksum, entry.branch)
                )
    return out


def _read_docs(raw_dir: Path) -> list[ExplorerDocument]:
    index_file = raw_dir / DOCS_INDEX
    if not index_file.is_file():
        return []
    try:
        entries = parse_docs_index(index_file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    return [
        ExplorerDocument(
            path=e.path,
            title=e.title or e.path.rsplit("/", 1)[-1],
            sections=len(e.sections),
            mentions=tuple(e.mentions),
        )
        for e in sorted(entries.values(), key=lambda d: d.path)
    ]


def _dirname(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def object_folder(mode: str, entries: SourceEntries, obj: ExplorerObject) -> str:
    if mode == "kind":
        return obj.kind
    if mode == "parser":
        return obj.parser or "unknown"
    if entries.type == "git":
        return _dirname(obj.origin)
    if entries.type == "openapi":
        return obj.origin
    if entries.type:  # base de données : base > schéma (clé d'origine `schéma.objet`)
        schema = obj.origin.split(".", 1)[0] if "." in obj.origin else ""
        if not schema:
            schema = obj.file.split(".", 1)[0] if obj.file.count(".") >= 2 else ""
        return "/".join(p for p in (entries.database, schema) if p)
    return ""


def _document_folder(mode: str, entries: SourceEntries, doc: ExplorerDocument) -> str:
    if mode == "kind":
        return DOCUMENTS_FOLDER
    if mode == "parser":
        return MARKDOWN_PARSER
    return _dirname(doc.path) if entries.type == "git" else ""


def _ensure_folder(folders: dict[str, _Folder], path: str) -> _Folder:
    """Crée le dossier et ses ancêtres (chacun connaît ses sous-dossiers)."""
    if path in folders:
        return folders[path]
    folder = _Folder(path=path)
    folders[path] = folder
    if path:
        parent = _ensure_folder(folders, _dirname(path))
        parent.folders.add(path)
    return folder


@dataclass
class SourceEntries:
    """Ce que la couche raw d'une source contient, indépendamment du mode
    d'affichage : c'est ce qui est mis en index (base), les dossiers se calculant
    ensuite à la volée pour le mode demandé."""

    slug: str
    label: str
    type: str
    database: str
    collected: bool
    objects: list[ExplorerObject]
    documents: list[ExplorerDocument]

    def to_json(self) -> dict[str, Any]:
        return {
            "objects": [
                [
                    o.file,
                    o.title,
                    o.kind,
                    o.fields,
                    o.origin,
                    o.parser,
                    list(o.badges),
                    o.excluded_by,
                    o.schema_id,
                    o.checksum,
                    o.branch,
                    o.visibility,
                    o.visibility_source,
                    [[r.field, r.kind, r.target, r.cardinality, r.external] for r in o.refs],
                    o.description,
                    list(o.top_fields),
                ]
                for o in self.objects
            ],
            "documents": [[d.path, d.title, d.sections, list(d.mentions)] for d in self.documents],
            "database": self.database,
        }

    @classmethod
    def from_json(
        cls, slug: str, label: str, type_: str, collected: bool, data: dict[str, Any]
    ) -> SourceEntries:
        # Listes positionnelles plutôt qu'objets : l'index d'une grosse source
        # pèse deux fois moins lourd en base et se relit plus vite.
        return cls(
            slug=slug,
            label=label,
            type=type_,
            database=str(data.get("database") or ""),
            collected=collected,
            objects=[
                ExplorerObject(
                    file=o[0],
                    title=o[1],
                    kind=o[2],
                    fields=int(o[3]),
                    origin=o[4],
                    parser=o[5],
                    badges=tuple(o[6]),
                    excluded_by=o[7],
                    schema_id=o[8] if len(o) > 8 else "",
                    checksum=o[9] if len(o) > 9 else "",
                    branch=o[10] if len(o) > 10 else "",
                    visibility=o[11] if len(o) > 11 else "",
                    visibility_source=o[12] if len(o) > 12 else "",
                    refs=tuple(SchemaRef(*r) for r in o[13]) if len(o) > 13 else (),
                    description=o[14] if len(o) > 14 else "",
                    top_fields=tuple(o[15]) if len(o) > 15 else (),
                )
                for o in data.get("objects") or []
            ],
            documents=[
                ExplorerDocument(
                    path=d[0],
                    title=d[1],
                    sections=int(d[2]),
                    mentions=tuple(d[3]) if len(d) > 3 else (),
                )
                for d in data.get("documents") or []
            ],
        )


def collect_source_entries(
    workdir: Path, slug: str, *, review_base: str | None = None
) -> SourceEntries:
    """Lit la couche raw d'une source dans une copie de travail. `review_base` :
    commit du point de reprise (`review.yaml`), None si jamais revue."""
    source_dir = safe_source_path(workdir, slug)
    meta = _read_source_meta(source_dir, slug)
    raw_dir = source_dir / RAW_DIR
    if not raw_dir.is_dir():
        return SourceEntries(slug, meta.label, meta.type, meta.database, False, [], [])

    origins = _tech_origins(raw_dir)
    assets_dir = source_dir / ASSETS_DIR
    retained = (
        {p.name for p in assets_dir.iterdir() if p.is_file()} if assets_dir.is_dir() else set()
    )
    base_blobs = review_blobs(workdir, slug, review_base)
    rules: list[TriRule] = compile_rules(meta.rules) if meta.rules else []

    objects: list[ExplorerObject] = []
    for path in sorted(raw_dir.iterdir()):
        if not (path.is_file() and path.suffix == ".json"):
            continue
        summary = schema_summary(path)
        origin, parser, checksum, branch = origins.get(path.name, ("", "", "", ""))
        if not origin and summary.origins:
            origin = summary.origins[0]
        badges: list[str] = []
        if path.name in retained:
            badges.append(BADGE_RETAINED)
        if base_blobs is not None:
            known = base_blobs.get(path.name)
            if known is None:
                badges.append(BADGE_NEW)
            elif known != summary.blob:
                badges.append(BADGE_MODIFIED)
        excluded_by = ""
        if rules and not summary.error:
            rule = object_excluded(_full_schema(path), rules)
            if rule is not None:
                badges.append(BADGE_EXCLUDED)
                excluded_by = rule_label(rule)
        if summary.error:
            badges.append(BADGE_ERROR)
        objects.append(
            ExplorerObject(
                file=path.name,
                title=summary.title or path.stem,
                kind=summary.kind or "object",
                fields=summary.fields,
                origin=origin,
                parser=parser,
                badges=tuple(badges),
                excluded_by=excluded_by,
                schema_id=summary.schema_id,
                checksum=checksum,
                branch=branch,
                visibility=summary.visibility,
                visibility_source=summary.visibility_source,
                refs=summary.refs,
                description=summary.description,
                top_fields=summary.top_fields,
            )
        )
    return SourceEntries(
        slug, meta.label, meta.type, meta.database, True, objects, _read_docs(raw_dir)
    )


def index_from_entries(
    entries: SourceEntries, mode: str = DEFAULT_MODE, error_origins: Iterable[str] = ()
) -> SourceIndex:
    """Range les objets et documents en dossiers pour un mode d'affichage.
    `error_origins` : fichiers d'origine signalés en erreur par la dernière
    collecte (lus à part : ils changent sans que la couche raw change)."""
    if mode not in MODES:
        mode = DEFAULT_MODE
    errors = set(error_origins)
    objects = [
        replace(o, badges=(*o.badges, BADGE_ERROR))
        if o.origin and o.origin in errors and BADGE_ERROR not in o.badges
        else o
        for o in entries.objects
    ]
    folders: dict[str, _Folder] = {"": _Folder(path="")}
    for obj in objects:
        _ensure_folder(folders, object_folder(mode, entries, obj)).objects.append(obj)
    for doc in entries.documents:
        _ensure_folder(folders, _document_folder(mode, entries, doc)).documents.append(doc)
    return SourceIndex(
        entries.slug,
        entries.label,
        entries.type,
        mode,
        entries.collected,
        objects,
        entries.documents,
        folders,
    )


def build_source_index(
    workdir: Path,
    slug: str,
    mode: str = DEFAULT_MODE,
    *,
    error_origins: Iterable[str] = (),
    review_base: str | None = None,
) -> SourceIndex:
    """Index d'une source lu directement dans une copie de travail."""
    return index_from_entries(
        collect_source_entries(workdir, slug, review_base=review_base), mode, error_origins
    )


def list_source_slugs(workdir: Path) -> list[str]:
    """Slugs des sources déclarées dans la copie (un manifeste par répertoire)."""
    base = workdir / SOURCES_DIR
    if not base.is_dir():
        return []
    return sorted(
        p.name for p in base.iterdir() if p.is_dir() and (p / MANIFEST_FILENAME).is_file()
    )


# --- Vues rendues à l'API -----------------------------------------------------


def folder_item_count(folder: _Folder) -> int:
    return len(folder.folders) + len(folder.objects) + len(folder.documents)


def subtree_file_counts(index: SourceIndex) -> dict[str, int]:
    """Fichiers (objets + documents) de chaque dossier, sous-arbre compris, en
    une passe : chaque dossier ajoute les siens à tous ses ancêtres."""
    counts = dict.fromkeys(index.folders, 0)
    for path, folder in index.folders.items():
        own = len(folder.objects) + len(folder.documents)
        if not own:
            continue
        current: str | None = path
        while current is not None:
            counts[current] = counts.get(current, 0) + own
            current = _dirname(current) if current else None
    return counts


def search_objects(index: SourceIndex, query: str) -> list[tuple[str, ExplorerObject]]:
    """Objets dont le nom (titre ou fichier) contient `query`, insensible à la
    casse ; rend (dossier, objet), au plus 50 résultats."""
    needle = query.strip().casefold()
    if not needle:
        return []
    found: list[tuple[str, ExplorerObject]] = []
    for path in sorted(index.folders):
        for obj in index.folders[path].objects:
            if needle in obj.title.casefold() or needle in obj.file.casefold():
                found.append((path, obj))
                if len(found) >= _SEARCH_LIMIT:
                    return found
    return found


def sort_objects(objects: Sequence[ExplorerObject]) -> list[ExplorerObject]:
    return sorted(objects, key=lambda o: (o.title.casefold(), o.file))
