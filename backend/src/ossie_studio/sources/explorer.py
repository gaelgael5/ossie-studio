"""Explorateur raw : index en lecture seule de la couche raw d'une source.

L'explorateur parcourt `sources/<slug>/raw/` comme un explorateur de fichiers.
Les schémas sont **à plat** à la racine de `raw/` (un fichier par objet) : les
« dossiers » affichés ne sont donc pas des répertoires de `raw/`, ils sont
**calculés** selon un mode d'affichage :

- `origin` : l'arborescence d'origine — dossiers du dépôt (Git, d'après les
  manifestes techniques), base > schéma (catalogue), contrat (OpenAPI) ;
- `kind` : la nature de l'objet (`x-kind`) ;
- `parser` : le parseur qui l'a produit.

Rien n'est jamais écrit. L'index est recalculé à chaque requête mais chaque
schéma n'est relu que si son fichier a changé (cache par chemin, taille et date
de modification) : un dossier s'affiche sans relire les milliers de schémas
d'une grosse source à chaque clic.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

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
class SchemaSummary:
    """Ce que l'explorateur retient d'un schéma (relu seulement s'il change)."""

    title: str
    kind: str
    fields: int
    origins: tuple[str, ...]  # fichiers d'origine lus sous `x-source`
    blob: str  # identifiant de blob git du contenu (comparaison au point de reprise)
    error: bool  # JSON illisible ou pas un objet


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


@dataclass(frozen=True)
class ExplorerDocument:
    path: str  # chemin sous raw/docs/ (= chemin dans le dépôt)
    title: str
    sections: int


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


def _tech_origins(raw_dir: Path) -> dict[str, tuple[str, str]]:
    """Schéma → (fichier d'origine, parseur), d'après les manifestes techniques
    de `raw/` (`manifest.yaml` et un `manifest-<parseur>.yaml` par plugin)."""
    out: dict[str, tuple[str, str]] = {}
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
                out.setdefault(dest, (source, entry.parser or default_parser))
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
            path=e.path, title=e.title or e.path.rsplit("/", 1)[-1], sections=len(e.sections)
        )
        for e in sorted(entries.values(), key=lambda d: d.path)
    ]


def _dirname(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _object_folder(mode: str, meta: _SourceMeta, obj: ExplorerObject) -> str:
    if mode == "kind":
        return obj.kind
    if mode == "parser":
        return obj.parser or "unknown"
    if meta.type == "git":
        return _dirname(obj.origin)
    if meta.type == "openapi":
        return obj.origin
    if meta.type:  # base de données : base > schéma (clé d'origine `schéma.objet`)
        schema = obj.origin.split(".", 1)[0] if "." in obj.origin else ""
        if not schema:
            schema = obj.file.split(".", 1)[0] if obj.file.count(".") >= 2 else ""
        return "/".join(p for p in (meta.database, schema) if p)
    return ""


def _document_folder(mode: str, meta: _SourceMeta, doc: ExplorerDocument) -> str:
    if mode == "kind":
        return DOCUMENTS_FOLDER
    if mode == "parser":
        return MARKDOWN_PARSER
    return _dirname(doc.path) if meta.type == "git" else ""


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


def build_source_index(
    workdir: Path,
    slug: str,
    mode: str = DEFAULT_MODE,
    *,
    error_origins: Iterable[str] = (),
    review_base: str | None = None,
) -> SourceIndex:
    """Index d'une source dans une copie de travail. `error_origins` : fichiers
    d'origine signalés en erreur par la dernière collecte ; `review_base` : commit
    du point de reprise (`review.yaml`), None si jamais revue."""
    if mode not in MODES:
        mode = DEFAULT_MODE
    source_dir = safe_source_path(workdir, slug)
    meta = _read_source_meta(source_dir, slug)
    raw_dir = source_dir / RAW_DIR
    folders: dict[str, _Folder] = {"": _Folder(path="")}
    if not raw_dir.is_dir():
        return SourceIndex(slug, meta.label, meta.type, mode, False, [], [], folders)

    origins = _tech_origins(raw_dir)
    assets_dir = source_dir / ASSETS_DIR
    retained = (
        {p.name for p in assets_dir.iterdir() if p.is_file()} if assets_dir.is_dir() else set()
    )
    base_blobs = review_blobs(workdir, slug, review_base)
    rules: list[TriRule] = compile_rules(meta.rules) if meta.rules else []
    errors = set(error_origins)

    objects: list[ExplorerObject] = []
    for path in sorted(raw_dir.iterdir()):
        if not (path.is_file() and path.suffix == ".json"):
            continue
        summary = schema_summary(path)
        origin, parser = origins.get(path.name, ("", ""))
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
        if summary.error or (origin and origin in errors):
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
            )
        )

    documents = _read_docs(raw_dir)

    for obj in objects:
        _ensure_folder(folders, _object_folder(mode, meta, obj)).objects.append(obj)
    for doc in documents:
        _ensure_folder(folders, _document_folder(mode, meta, doc)).documents.append(doc)
    return SourceIndex(slug, meta.label, meta.type, mode, True, objects, documents, folders)


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


def subtree_object_count(index: SourceIndex, path: str) -> int:
    prefix = f"{path}/" if path else ""
    return sum(
        len(f.objects)
        for p, f in index.folders.items()
        if p == path or (not path) or p.startswith(prefix)
    )


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
