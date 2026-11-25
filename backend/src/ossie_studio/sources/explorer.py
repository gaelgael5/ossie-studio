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
import subprocess
import threading
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from ossie_studio.parsers.application import custom_links
from ossie_studio.parsers.conventions import (
    CODE_LANGUAGE_KEY,
    SCHEMA_SUFFIX,
    VISIBILITIES,
    VISIBILITY_KEY,
    SchemaReadError,
    is_schema_name,
    load_schema,
)
from ossie_studio.parsers.markdown import DOCS_INDEX, parse_docs_index
from ossie_studio.parsers.openapi_full import CONTRACTS_INDEX, parse_contracts_index
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
# 9 : badges « nouveau » / « modifié » par rapport à la tête publiée (aad1d3c6).
# 10 : schémas en YAML, `<clé>.schema.yaml` (ticket 5cc1cb07).
# 13 : modules et compteurs de fonctions (cartographie applicative, 0fa620be).
INDEX_FORMAT = "15"
DEFAULT_MODE = "origin"

# Badges d'état (clés stables, traduites côté interface).
BADGE_RETAINED = "retained"
BADGE_NEW = "new"
BADGE_MODIFIED = "modified"
BADGE_EXCLUDED = "excluded"
BADGE_ERROR = "error"

# Dossier virtuel des documents dans les modes `kind` et `parser`.
DOCUMENTS_FOLDER = "documents"
# Dossier virtuel des contrats complets dans les modes `kind` et `parser`.
CONTRACTS_FOLDER = "openapi"
MARKDOWN_PARSER = "markdown"

# Résultats rendus au plus par une recherche (toutes sources confondues).
SEARCH_LIMIT = 200


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
class ModuleFacts:
    """Ce que l'explorateur retient d'un objet module (cartographie applicative) :
    son fichier de projet, sa nature, ses cibles, ses dépendances
    (`(projet, $id du module ou "")`, "" pour une dépendance externe) et les
    ancres choisies de ses liens, lues dans son `.view.yaml` (ticket a1fd0663) :
    `(module d'arrivée, départ, arrivée)`, chaque ancre `auto` ou `côté:position`."""

    project: str
    nature: str
    language: str
    targets: tuple[str, ...] = ()
    dependencies: tuple[tuple[str, str], ...] = ()
    anchors: tuple[tuple[str, str, str], ...] = ()


def anchor_text(anchor: Any) -> str:
    """Une ancre du `.view.yaml` en texte d'index : `auto` ou `côté:position`."""
    if isinstance(anchor, dict):
        return f"{anchor.get('side')}:{anchor.get('at')}"
    return "auto"


def anchor_value(text: str) -> Any:
    """L'inverse d'`anchor_text` : `auto` ou `{side, at}`."""
    side, sep, at = text.partition(":")
    if not sep:
        return "auto"
    return {"side": side, "at": float(at)}


def view_anchors(path: Path) -> tuple[tuple[str, str, str], ...]:
    """Les ancres choisies des liens d'un module (son `.view.yaml`, absent ou
    illisible : aucune)."""
    try:
        text = path.read_text(encoding="utf-8") if path.is_file() else None
    except (OSError, UnicodeDecodeError):
        return ()
    return tuple(
        (target, anchor_text(link.get("from")), anchor_text(link.get("to")))
        for target, link in custom_links(text).items()
    )


@dataclass(frozen=True)
class FunctionCounts:
    """Les fonctions d'un objet, lues dans son `<clé>.extend.yaml` : méthodes
    publiques, points d'entrée (dont routes HTTP), fonctions libres (module) ;
    points d'entrée reliés au contrat et opérations reliées (fichier du contrat
    sous `contracts/`, verbe, chemin) — ticket 90ed0967."""

    methods: int = 0
    entry_points: int = 0
    http: int = 0
    functions: int = 0
    linked: int = 0
    operations: tuple[tuple[str, str, str], ...] = ()

    @classmethod
    def from_json(cls, row: list[Any]) -> FunctionCounts:
        counts = [int(n) for n in row[:5]] + [0] * (5 - len(row[:5]))
        return cls(
            methods=counts[0],
            entry_points=counts[1],
            http=counts[2],
            functions=counts[3],
            linked=counts[4],
            operations=tuple((o[0], o[1], o[2]) for o in row[5]) if len(row) > 5 else (),
        )


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
    annotations: tuple[str, ...] = ()  # attributs / annotations / décorateurs (règles de tags)
    field_names: tuple[str, ...] = ()  # tous les champs (règles de tags)
    namespace: str = ""  # x-namespace (détails d'une vignette au survol)
    # Types de chaque champ, dans l'ordre de `field_names` (règles de classification).
    field_types: tuple[tuple[str, ...], ...] = ()
    module: ModuleFacts | None = None


@dataclass(frozen=True)
class ObjectTag:
    """Un tag gouverné posé par une règle (voir `governance/tag_rules`), avec sa
    provenance : la condition reconnue (`reason` + `value`), ou la classe dont il
    est hérité (`via`, « source/fichier », et son titre)."""

    tag: str
    rule: str
    rule_name: str
    reason: str = ""  # inherits | annotation | name | path | field ; "" si hérité
    value: str = ""  # ce qui a été reconnu (ex. « ControllerBase »)
    via: str = ""
    via_title: str = ""


@dataclass(frozen=True)
class ObjectFacts:
    """Ce que les règles de tags regardent (au-delà des bases, déjà dans `refs`).
    Gardé dans l'index : l'aperçu d'une règle et le décompte de ses objets se
    calculent alors sans relire raw/."""

    annotations: tuple[str, ...] = ()
    field_names: tuple[str, ...] = ()
    # Types de chaque champ (mêmes positions que `field_names`) : ce que la
    # condition « type » des règles de classification compare.
    field_types: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class FieldClass:
    """Classification posée sur un champ par une règle de classification (voir
    `governance/classification_rules`) : schéma, valeur(s), règle."""

    field: str
    scheme: str
    values: tuple[str, ...]
    rule: str
    rule_name: str


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
    tags: tuple[ObjectTag, ...] = ()
    # Tags qu'une règle poserait mais que l'utilisateur a retirés de CET objet
    # (montrés à part, avec « remettre ») ; les descendants qui ne les tenaient
    # que de lui ne les portent pas et n'apparaissent pas ici.
    removed_tags: tuple[ObjectTag, ...] = ()
    namespace: str = ""  # espace de noms d'origine (x-namespace) ; vide pour SQL, contrats
    field_classes: tuple[FieldClass, ...] = ()
    facts: ObjectFacts = field(default=ObjectFacts(), compare=False, repr=False)
    # Cartographie applicative (0fa620be) : un objet module, et les fonctions de
    # l'objet (son `.extend.yaml`).
    module: ModuleFacts | None = None
    functions: FunctionCounts = FunctionCounts()


@dataclass(frozen=True)
class ExplorerDocument:
    path: str  # chemin sous raw/docs/ (= chemin dans le dépôt)
    title: str
    sections: int
    mentions: tuple[str, ...] = ()  # identifiants cités (rapprochés des objets)


@dataclass(frozen=True)
class ExplorerContract:
    """Copie intégrale d'un contrat OpenAPI (ticket e79d008f), sous
    `raw/contracts/` : `path` y est relatif et finit par
    `contrat.full.openapi.yaml` ; `source` est le contrat d'origine."""

    path: str
    source: str
    title: str
    version: str
    operations: int


@dataclass
class _Folder:
    path: str
    folders: set[str] = field(default_factory=set)
    objects: list[ExplorerObject] = field(default_factory=list)
    documents: list[ExplorerDocument] = field(default_factory=list)
    contracts: list[ExplorerContract] = field(default_factory=list)


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
    contracts: list[ExplorerContract] = field(default_factory=list)


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
        _EXTEND_CACHE.clear()


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
        schema = load_schema(data.decode("utf-8"))
    except (UnicodeDecodeError, SchemaReadError):
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
        annotations=_annotation_names(schema),
        field_names=tuple(props) if isinstance(props, dict) else (),
        namespace=str(schema.get("x-namespace") or ""),
        field_types=tuple(type_candidates(p) for p in props.values())
        if isinstance(props, dict)
        else (),
        module=module_facts(schema),
    )


def module_facts(schema: dict[str, Any]) -> ModuleFacts | None:
    """Les faits d'un objet module (`x-kind: module`), None pour tout autre objet."""
    meta = schema.get("x-module")
    if schema.get("x-kind") != "module" or not isinstance(meta, dict):
        return None
    deps = meta.get("dependencies")
    return ModuleFacts(
        project=str(meta.get("project") or ""),
        nature=str(meta.get("nature") or ""),
        language=str(schema.get("x-language") or ""),
        targets=tuple(str(t) for t in meta.get("targets") or []),
        dependencies=tuple(
            (str(d.get("project") or ""), str(d.get("module") or ""))
            for d in deps or []
            if isinstance(d, dict)
        ),
    )


def function_counts(document: Any) -> FunctionCounts:
    """Compteurs d'un `.extend.yaml` : méthodes publiques, points d'entrée (ceux
    des méthodes, des fonctions libres et de l'objet lui-même), routes HTTP,
    fonctions libres."""
    if not isinstance(document, dict):
        return FunctionCounts()
    methods = [m for m in document.get("methods") or [] if isinstance(m, dict)]
    functions = [f for f in document.get("functions") or [] if isinstance(f, dict)]
    entries = [f.get("entryPoint") for f in (*methods, *functions)]
    entries.append(document.get("entryPoint"))
    found = [e for e in entries if isinstance(e, dict)]
    contracts = [c for c in (e.get("contract") for e in found) if isinstance(c, dict)]
    linked = [c for c in contracts if c.get("status") == "linked"]
    operations = {
        (str(o.get("file", "")), str(o.get("verb", "")), str(o.get("path", "")))
        for c in linked
        for o in c.get("operations") or []
        if isinstance(o, dict)
    }
    return FunctionCounts(
        methods=sum(1 for m in methods if m.get("visibility") == "public"),
        entry_points=len(found),
        http=sum(1 for e in found if e.get("kind") == "http"),
        functions=len(functions),
        linked=len(linked),
        operations=tuple(sorted(operations)),
    )


_EXTEND_CACHE: dict[str, tuple[int, int, FunctionCounts]] = {}


def _extend_name(schema_name: str) -> str:
    """`commande.schema.yaml` → `commande.extend.yaml` (fonctions de l'objet)."""
    return f"{schema_name[: -len(SCHEMA_SUFFIX)]}.extend.yaml"


def _view_name(schema_name: str) -> str:
    """`module-x.schema.yaml` → `module-x.view.yaml` (ancres des liens du module)."""
    return f"{schema_name[: -len(SCHEMA_SUFFIX)]}.view.yaml"


def _with_anchors(module: ModuleFacts | None, view: Path) -> ModuleFacts | None:
    """Les faits d'un module complétés des ancres de son `.view.yaml`."""
    return replace(module, anchors=view_anchors(view)) if module else None


def extend_counts(path: Path) -> FunctionCounts:
    """Compteurs du `.extend.yaml` d'un objet (absent : zéro), relus seulement
    si le fichier a changé."""
    if not path.is_file():
        return FunctionCounts()
    stat = path.stat()
    key = str(path)
    with _LOCK:
        cached = _EXTEND_CACHE.get(key)
    if cached is not None and cached[0] == stat.st_size and cached[1] == stat.st_mtime_ns:
        return cached[2]
    try:
        counts = function_counts(load_schema(path.read_text(encoding="utf-8")))
    except (UnicodeDecodeError, SchemaReadError):
        counts = FunctionCounts()
    with _LOCK:
        if len(_EXTEND_CACHE) >= _SCHEMA_CACHE_MAX:
            _EXTEND_CACHE.clear()
        _EXTEND_CACHE[key] = (stat.st_size, stat.st_mtime_ns, counts)
    return counts


def type_candidates(prop: Any) -> tuple[str, ...]:
    """Ce qu'une condition « type » peut reconnaître sur un champ : le type JSON
    Schema (`string`, `integer`…), son format (`int32`, `date-time`), `enum`,
    le type tel qu'écrit dans le code quand il n'est pas résolu, le nom court
    de l'objet référencé, et pour une liste `array` et `<type>[]`."""
    if not isinstance(prop, dict):
        return ()
    out: list[str] = []
    kind = prop.get("type")
    for k in kind if isinstance(kind, list) else [kind]:
        if isinstance(k, str) and k != "null":
            out.append(k)
    for key in ("format", "x-unresolved-type"):
        if isinstance(prop.get(key), str):
            out.append(str(prop[key]))
    if "enum" in prop:
        out.append("enum")
    if isinstance(prop.get("$ref"), str):
        out.append(str(prop["$ref"]).rsplit("/", 1)[-1].rsplit(".", 1)[-1])
    items = prop.get("items")
    if isinstance(items, dict):
        out += [f"{t}[]" for t in type_candidates(items)]
    return tuple(dict.fromkeys(o for o in out if o))


def _annotation_names(schema: dict[str, Any]) -> tuple[str, ...]:
    """Noms des attributs de l'objet (`x-annotations` : `{"nom": …}` pour les
    parseurs de code, chaîne pour d'anciens schémas)."""
    value = schema.get("x-annotations")
    names: list[str] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict) and item.get("nom"):
            names.append(str(item["nom"]))
        elif isinstance(item, str) and item:
            names.append(item)
    return tuple(dict.fromkeys(names))


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
        data = load_schema(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SchemaReadError):
        return {}
    return data if isinstance(data, dict) else {}


# --- Point de reprise de la revue ---------------------------------------------


def head_commit(workdir: Path) -> str | None:
    """Sha de HEAD d'une copie : la tête publiée (la vue de brouillon est un
    worktree détaché posé sur elle). None hors dépôt git (tests sur dossier nu)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--verify", "-q", "HEAD"],  # noqa: S607
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (out.stdout.strip() or None) if out.returncode == 0 else None


def published_raw_blobs(workdir: Path, slug: str, base_sha: str | None) -> dict[str, str] | None:
    """Blobs des schémas de `raw/` à la tête publiée (nom → id de blob), ou None
    si le commit est illisible. Base des badges « nouveau » / « modifié » (ticket
    docflow aad1d3c6 : écart entre ma vue et la branche publiée, plus de point de
    reprise). Lit l'arbre git (`ls-tree`) sans relire aucun contenu, une fois par
    commit."""
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
        out = subprocess.run(  # noqa: S603 — arguments fixes, sha issu de git rev-parse
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
            if len(parts) == 3 and parts[1] == "blob" and path.endswith(SCHEMA_SUFFIX):
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


def _read_contracts(raw_dir: Path) -> list[ExplorerContract]:
    index_file = raw_dir / CONTRACTS_INDEX
    if not index_file.is_file():
        return []
    try:
        entries = parse_contracts_index(index_file.read_text(encoding="utf-8"))
    except OSError:
        return []
    return [
        ExplorerContract(e.path, e.source, e.title, e.version, e.operations)
        for e in sorted(entries.values(), key=lambda c: c.path)
    ]


def _contract_folder(mode: str, contract: ExplorerContract) -> str:
    """Le dossier de la copie est celui de ses schémas en arborescence d'origine
    (son chemin sous `contracts/` le porte déjà) ; dans les autres modes, le
    dossier du parseur `openapi` / des contrats."""
    if mode in ("kind", "parser"):
        return CONTRACTS_FOLDER
    return _dirname(contract.path)


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
    contracts: list[ExplorerContract] = field(default_factory=list)

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
                    [
                        [t.tag, t.rule, t.rule_name, t.reason, t.value, t.via, t.via_title]
                        for t in o.tags
                    ],
                    [
                        list(o.facts.annotations),
                        list(o.facts.field_names),
                        [list(t) for t in o.facts.field_types],
                    ],
                    [
                        [t.tag, t.rule, t.rule_name, t.reason, t.value, t.via, t.via_title]
                        for t in o.removed_tags
                    ],
                    o.namespace,
                    [
                        [c.field, c.scheme, list(c.values), c.rule, c.rule_name]
                        for c in o.field_classes
                    ],
                    [
                        o.module.project,
                        o.module.nature,
                        o.module.language,
                        list(o.module.targets),
                        [list(d) for d in o.module.dependencies],
                        [list(a) for a in o.module.anchors],
                    ]
                    if o.module
                    else None,
                    [
                        o.functions.methods,
                        o.functions.entry_points,
                        o.functions.http,
                        o.functions.functions,
                        o.functions.linked,
                        [list(op) for op in o.functions.operations],
                    ],
                ]
                for o in self.objects
            ],
            "documents": [[d.path, d.title, d.sections, list(d.mentions)] for d in self.documents],
            "contracts": [
                [c.path, c.source, c.title, c.version, c.operations] for c in self.contracts
            ],
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
                    tags=tuple(ObjectTag(*t) for t in o[16]) if len(o) > 16 else (),
                    facts=ObjectFacts(
                        tuple(o[17][0]),
                        tuple(o[17][1]),
                        tuple(tuple(t) for t in o[17][2]) if len(o[17]) > 2 else (),
                    )
                    if len(o) > 17
                    else ObjectFacts(),
                    removed_tags=tuple(ObjectTag(*t) for t in o[18]) if len(o) > 18 else (),
                    namespace=o[19] if len(o) > 19 else "",
                    field_classes=tuple(
                        FieldClass(c[0], c[1], tuple(c[2]), c[3], c[4]) for c in o[20]
                    )
                    if len(o) > 20
                    else (),
                    module=ModuleFacts(
                        o[21][0],
                        o[21][1],
                        o[21][2],
                        tuple(o[21][3]),
                        tuple((d[0], d[1]) for d in o[21][4]),
                        tuple((a[0], a[1], a[2]) for a in o[21][5]) if len(o[21]) > 5 else (),
                    )
                    if len(o) > 21 and o[21]
                    else None,
                    functions=FunctionCounts.from_json(o[22]) if len(o) > 22 else FunctionCounts(),
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
            contracts=[
                ExplorerContract(c[0], c[1], c[2], c[3], int(c[4]))
                for c in data.get("contracts") or []
            ],
        )


def collect_source_entries(
    workdir: Path, slug: str, *, published_base: str | None = None
) -> SourceEntries:
    """Lit la couche raw d'une source dans une copie de travail. `published_base` :
    tête publiée de la branche ; un objet absent d'elle est « nouveau », un objet
    au contenu différent « modifié ». None : pas de badge (copie hors git)."""
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
    base_blobs = published_raw_blobs(workdir, slug, published_base)
    rules: list[TriRule] = compile_rules(meta.rules) if meta.rules else []

    objects: list[ExplorerObject] = []
    for path in sorted(raw_dir.iterdir()):
        if not (path.is_file() and is_schema_name(path.name)):
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
                namespace=summary.namespace,
                facts=ObjectFacts(summary.annotations, summary.field_names, summary.field_types),
                module=_with_anchors(summary.module, raw_dir / _view_name(path.name)),
                functions=extend_counts(raw_dir / _extend_name(path.name)),
            )
        )
    return SourceEntries(
        slug,
        meta.label,
        meta.type,
        meta.database,
        True,
        objects,
        _read_docs(raw_dir),
        _read_contracts(raw_dir),
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
    for contract in entries.contracts:
        _ensure_folder(folders, _contract_folder(mode, contract)).contracts.append(contract)
    return SourceIndex(
        entries.slug,
        entries.label,
        entries.type,
        mode,
        entries.collected,
        objects,
        entries.documents,
        folders,
        entries.contracts,
    )


def build_source_index(
    workdir: Path,
    slug: str,
    mode: str = DEFAULT_MODE,
    *,
    error_origins: Iterable[str] = (),
    published_base: str | None = None,
) -> SourceIndex:
    """Index d'une source lu directement dans une copie de travail."""
    return index_from_entries(
        collect_source_entries(workdir, slug, published_base=published_base),
        mode,
        error_origins,
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
    return len(folder.folders) + len(folder.objects) + len(folder.documents) + len(folder.contracts)


def subtree_file_counts(index: SourceIndex, family: str = "") -> dict[str, int]:
    """Fichiers (objets + documents) de chaque dossier, sous-arbre compris, en
    une passe : chaque dossier ajoute les siens à tous ses ancêtres. `family`
    (schema | openapi | markdown) : ne compte que ce type,
    pour que l'arbre suive le filtre « Type »."""
    counts = dict.fromkeys(index.folders, 0)
    for path, folder in index.folders.items():
        if not family:
            own = len(folder.objects) + len(folder.documents) + len(folder.contracts)
        elif family == FAMILY_MARKDOWN:
            own = len(folder.documents)
        else:
            own = sum(1 for o in folder.objects if object_family(o) == family)
            # Le contrat complet est de la famille « Contrats OpenAPI ».
            own += len(folder.contracts) if family == FAMILY_OPENAPI else 0
        if not own:
            continue
        current: str | None = path
        while current is not None:
            counts[current] = counts.get(current, 0) + own
            current = _dirname(current) if current else None
    return counts


@dataclass(frozen=True)
class SearchCriteria:
    """Critères de recherche de l'explorateur, combinés en ET (ticket docflow
    be0f301c). Évalués AVANT la limite du nombre de résultats : filtrer après
    la coupe (comme le faisait l'écran) rendait 3 objets quand il en existait 40.
    Un critère vide ne filtre pas."""

    text: str = ""  # contenu dans le titre ou le nom de fichier, sans casse
    tag: str = ""
    visibilities: frozenset[str] = frozenset()  # vide : toutes
    kind: str = ""
    badge: str = ""
    hide_excluded: bool = False
    # « Objets modifiés » (ticket c8185c47) : nouveaux OU modifiés depuis la
    # revue ; le filtre État n'en prend qu'un à la fois.
    changed: bool = False
    # Famille (ticket 915c76d2) : schema | openapi | markdown ; "" : toutes. Ne
    # déclenche pas une recherche à elle seule (elle filtre aussi un dossier,
    # côté écran) : absente de `active`.
    family: str = ""

    def active(self) -> bool:
        return bool(
            self.text.strip()
            or self.tag
            or self.visibilities
            or self.kind
            or self.badge
            or self.hide_excluded
            or self.changed
        )


FAMILY_SCHEMA = "schema"
FAMILY_OPENAPI = "openapi"
FAMILY_MARKDOWN = "markdown"  # les documents : aucun objet n'en est
FAMILIES = (FAMILY_SCHEMA, FAMILY_OPENAPI, FAMILY_MARKDOWN)


def object_family(obj: ExplorerObject) -> str:
    """« openapi » pour un objet issu d'un contrat OpenAPI (parseur openapi,
    contrat déposé ou trouvé dans une source Git), « schema » pour tout autre."""
    return FAMILY_OPENAPI if obj.parser.startswith("openapi") else FAMILY_SCHEMA


def object_matches(obj: ExplorerObject, criteria: SearchCriteria) -> bool:
    needle = criteria.text.strip().casefold()
    if needle and needle not in obj.title.casefold() and needle not in obj.file.casefold():
        return False
    if criteria.tag and not any(t.tag == criteria.tag for t in obj.tags):
        return False
    if criteria.visibilities and obj.visibility not in criteria.visibilities:
        return False
    if criteria.kind and obj.kind != criteria.kind:
        return False
    if criteria.badge and criteria.badge not in obj.badges:
        return False
    if criteria.changed and BADGE_NEW not in obj.badges and BADGE_MODIFIED not in obj.badges:
        return False
    if criteria.family and object_family(obj) != criteria.family:
        return False
    return not (criteria.hide_excluded and BADGE_EXCLUDED in obj.badges)


def scope_objects(index: SourceIndex, path: str = "") -> Iterator[tuple[str, ExplorerObject]]:
    """(dossier, objet) du dossier `path` ET de tous ses sous-dossiers, dans
    l'ordre des dossiers. Un dossier qui ne contient que des sous-dossiers
    (cas de `src`) a donc une portée non vide : ses filtres servent."""
    prefix = f"{path}/" if path else ""
    for folder in sorted(index.folders):
        if path and folder != path and not folder.startswith(prefix):
            continue
        yield from ((folder, obj) for obj in index.folders[folder].objects)


@dataclass
class SearchFacets:
    """Valeurs présentes dans la portée (sources et dossier), indépendamment des
    autres filtres : ce que les listes déroulantes proposent."""

    tags: set[str] = field(default_factory=set)
    visibilities: set[str] = field(default_factory=set)
    kinds: set[str] = field(default_factory=set)
    badges: set[str] = field(default_factory=set)

    def add(self, obj: ExplorerObject) -> None:
        self.tags.update(t.tag for t in obj.tags)
        if obj.visibility:
            self.visibilities.add(obj.visibility)
        self.kinds.add(obj.kind)
        self.badges.update(obj.badges)


def search_objects(
    index: SourceIndex,
    criteria: SearchCriteria,
    path: str = "",
    facets: SearchFacets | None = None,
    limit: int = SEARCH_LIMIT,
) -> list[tuple[str, ExplorerObject]]:
    """Objets de la portée qui remplissent tous les critères, au plus `limit`.
    `facets`, si fourni, reçoit les valeurs de TOUTE la portée (le parcours
    continue au-delà de la limite pour elles). Aucun critère : aucun résultat
    (l'écran montre alors le dossier)."""
    found: list[tuple[str, ExplorerObject]] = []
    searching = criteria.active()
    for folder, obj in scope_objects(index, path):
        if facets is not None:
            facets.add(obj)
        elif not searching or len(found) >= limit:
            break
        if searching and len(found) < limit and object_matches(obj, criteria):
            found.append((folder, obj))
    return found


def match_counts(index: SourceIndex, criteria: SearchCriteria) -> dict[str, int]:
    """Objets correspondant aux critères par dossier, sous-dossiers compris
    (« » = toute la source). Sert à l'arbre filtré : un dossier absent n'a aucun
    objet correspondant, ni lui ni sa descendance, et disparaît de l'arbre.
    Toute la source, sans limite : l'arbre dit OÙ sont les objets trouvés."""
    counts: dict[str, int] = {}
    for path, folder in index.folders.items():
        own = sum(1 for o in folder.objects if object_matches(o, criteria))
        if not own:
            continue
        current: str | None = path
        while current is not None:
            counts[current] = counts.get(current, 0) + own
            current = _dirname(current) if current else None
    return counts


def sort_objects(objects: Sequence[ExplorerObject]) -> list[ExplorerObject]:
    return sorted(objects, key=lambda o: (o.title.casefold(), o.file))
