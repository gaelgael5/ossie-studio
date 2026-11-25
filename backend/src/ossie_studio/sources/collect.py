from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from ossie_studio.databases.base import DatabaseEngine, DbEngineError
from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.models import GitSpace
from ossie_studio.parsers.base import (
    CIBLE_RAW,
    STATUT_ERREUR,
    STATUT_IGNORE,
    FileRow,
    OutputAction,
    Plugin,
    PluginContext,
    PluginError,
    PluginResult,
    RawAccess,
    matches_globs,
)
from ossie_studio.parsers.openapi import FoundContract, inspect_contract
from ossie_studio.parsers.registry import openapi_plugin, select_code_plugins
from ossie_studio.sources.collect_core import RAW_DIR, checksum
from ossie_studio.sources.contract_links import sync_contract_links
from ossie_studio.sources.csharp_modules import csharp_module_actions
from ossie_studio.sources.download import SourceDownloader, SourceDownloadError
from ossie_studio.sources.jvm_modules import jvm_module_actions, read_jvm_projects
from ossie_studio.sources.module_functions import sync_module_functions
from ossie_studio.sources.module_views import sync_module_views
from ossie_studio.sources.npm_modules import npm_module_actions, read_npm_projects
from ossie_studio.sources.paths import CONTRATS_DIR, CONTRATS_INDEX, SOURCES_DIR
from ossie_studio.sources.progress import CollectProgress
from ossie_studio.sources.schema_resolve import (
    SchemaIndex,
    is_schema_path,
    read_project_map,
    resolve_source_schemas,
)
from ossie_studio.sources.sql_extract import object_to_source, source_key, sql_extractor
from ossie_studio.spaces.workdir import WorkdirError

log = structlog.get_logger(__name__)


class CollectionError(Exception):
    """Collecte refusée ou impossible, portée par un code stable (branch_behind,
    branch_not_found, repo_not_found, auth_failed, forge_unreachable, git_failed)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FileStatus:
    """Une ligne de la table de travail à l'issue d'une collecte."""

    path: str
    crc: str
    cible: str
    statut: str
    message: str = ""


@dataclass
class CollectReport:
    source_commit: str
    local_commit: str | None
    added: int
    modified: int
    deleted: int
    unchanged: int
    read: int
    retained: int
    errors: list[PluginError]
    duration_ms: int
    resolved: int = 0
    files: list[FileStatus] = field(default_factory=list)


# Dossiers exclus du scan par défaut (surchargeables dans le manifeste).
DEFAULT_EXCLUSIONS = ("node_modules", "vendor", ".git")


def normalize_exclusion(entry: str) -> str:
    """Une exclusion saisie : sans espaces ni barres de bord, chemin POSIX."""
    return entry.strip().replace("\\", "/").strip("/")


def is_excluded(rel: str, exclusions: list[str] | None) -> bool:
    """Le fichier (chemin relatif POSIX) tombe-t-il sous une exclusion ?

    - une exclusion **sans** `/` est un nom de dossier, exclu où qu'il soit
      (`tests`, `node_modules`) ;
    - une exclusion **avec** `/` est un chemin depuis la racine du dépôt,
      exclu avec tout ce qu'il contient (`src/Pssa.Colis21.Tests`) ;
    - une exclusion avec `*` est un motif sur le chemin (`**/test/**`).

    Les défauts (`node_modules`, `vendor`, `.git`) s'appliquent toujours : une
    exclusion saisie s'y ajoute, elle ne les remplace pas (sinon déclarer une
    exclusion faisait réapparaître les dépendances embarquées)."""
    parts = rel.split("/")
    names = set(DEFAULT_EXCLUSIONS)
    paths: list[str] = []
    patterns: list[str] = []
    for raw in exclusions or []:
        entry = normalize_exclusion(raw)
        if not entry:
            continue
        if "*" in entry or "?" in entry:
            patterns.append(entry)
        elif "/" in entry:
            paths.append(entry)
        else:
            names.add(entry)
    if any(part in names for part in parts[:-1]):
        return True
    if any(rel == p or rel.startswith(f"{p}/") for p in paths):
        return True
    return any(matches_globs([p], rel) for p in patterns)


def _read_source_files(
    root: Path,
    *,
    directories: list[str] | None = None,
    exclusions: list[str] | None = None,
    progress: CollectProgress | None = None,
    patterns: list[str] | None = None,
) -> dict[str, bytes]:
    """Lit les fichiers du code descendu, chemin relatif POSIX → octets. Le
    processus maître applique les sous-répertoires du manifeste (si donnés) et les
    exclusions (`is_excluded`). Un fichier exclu n'est pas lu : à la collecte
    suivante, les schémas qu'il avait produits sont retirés de raw/."""
    files: dict[str, bytes] = {}
    if not root.is_dir():
        return files
    roots = [r.strip("/") for r in (directories or []) if r.strip("/")]
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if is_excluded(rel, exclusions):
            continue
        if roots and not any(rel == r or rel.startswith(f"{r}/") for r in roots):
            continue
        # Lecture limitée à certains motifs (recherche des contrats : inutile de
        # lire tout le code pour n'en regarder que les YAML / JSON).
        if patterns is not None and not matches_globs(patterns, rel.lower()):
            continue
        files[rel] = path.read_bytes()
        if progress is not None:
            progress.scanned = len(files)  # compteur affiché pendant le scan
    return files


def _read_contrats(work_copy: Path, source_slug: str) -> dict[str, bytes]:
    """Les contrats déposés d'une source OpenAPI (hors fichier d'index), nom de
    fichier → octets. Ce sont les « fichiers source » d'une collecte OpenAPI."""
    base = work_copy / SOURCES_DIR / source_slug / CONTRATS_DIR
    files: dict[str, bytes] = {}
    if base.is_dir():
        for path in sorted(base.iterdir()):
            if path.is_file() and path.name != CONTRATS_INDEX:
                files[path.name] = path.read_bytes()
    return files


def _raw_dir(work_copy: Path, source_slug: str) -> Path:
    return work_copy / SOURCES_DIR / source_slug / RAW_DIR


def _read_raw_files(work_copy: Path, source_slug: str) -> dict[str, str]:
    """Instantané de `raw/` : chemin relatif → contenu texte. Sert d'accès lecture
    seule aux plugins (leur index, leurs sorties)."""
    raw = _raw_dir(work_copy, source_slug)
    files: dict[str, str] = {}
    if raw.is_dir():
        for path in sorted(raw.rglob("*")):
            if path.is_file():
                files[path.relative_to(raw).as_posix()] = path.read_text(encoding="utf-8")
    return files


class _RawSnapshot(RawAccess):
    """Accès lecture seule à `raw/` depuis un instantané en mémoire."""

    def __init__(self, files: dict[str, str]) -> None:
        self._files = files
        self._index: SchemaIndex | None = None

    def read_index(self, name: str) -> str | None:
        return self._files.get(name)

    def read_output(self, path: str) -> str | None:
        return self._files.get(path)

    def list_outputs(self) -> dict[str, str]:
        return dict(self._files)

    def find_schema(self, name: str) -> str | None:
        if self._index is None:
            self._index = SchemaIndex({p: c for p, c in self._files.items() if is_schema_path(p)})
        return self._index.resolve(name)


def _apply_actions(
    work_copy: Path, source_slug: str, writes: list[OutputAction], deletes: list[str]
) -> None:
    """Écrivain **unique** du socle : applique les actions de tous les plugins dans
    `raw/`. Les plugins ne touchent jamais au disque eux-mêmes."""
    raw = _raw_dir(work_copy, source_slug)
    raw.mkdir(parents=True, exist_ok=True)
    for action in writes:
        target = raw / action.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(action.content, encoding="utf-8")
    for dest in deletes:
        (raw / dest).unlink(missing_ok=True)


@dataclass
class _Aggregate:
    writes: list[OutputAction] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)
    errors: list[PluginError] = field(default_factory=list)
    added: int = 0
    modified: int = 0
    deleted: int = 0
    unchanged: int = 0
    read: int = 0
    retained: int = 0

    def merge(self, result: PluginResult) -> None:
        self.writes += result.writes
        self.deletes += result.deletes
        self.errors += result.errors
        self.added += result.added
        self.modified += result.modified
        self.deleted += result.deleted
        self.unchanged += result.unchanged
        self.read += result.read
        self.retained += result.retained


class SourceCollector:
    """Orchestre une collecte : branche à jour → inventaire des fichiers →
    exécution des plugins **en série** → écriture unique → un commit local. Un
    verrou par (espace, branche, source) garantit une seule collecte à la fois sur
    une même source."""

    def __init__(self, downloader: SourceDownloader) -> None:
        # La copie de travail n'est plus fixée ici : chaque collecte reçoit celle
        # de la personne qui la lance (DraftWorkdir), et écrit dans SON brouillon.
        self._downloader = downloader
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    async def _run(
        self,
        *,
        workdir: DraftWorkdir,
        space: GitSpace,
        branch_slug: str,
        branch_label: str,
        space_username: str,
        space_token: str | None,
        source_slug: str,
        source_files: dict[str, bytes],
        plugins: list[Plugin],
        author_name: str,
        author_email: str,
        force: bool,
        source_commit: str,
        start: float,
        plugin_params: dict[str, dict[str, str]],
        commit_descriptor: str = "",
        progress: CollectProgress | None = None,
    ) -> CollectReport:
        progress = progress or CollectProgress()
        progress.scanned = len(source_files)
        # 1. Instantané de raw/ (index et sorties des plugins), accès lecture seule.
        try:
            raw_files = await workdir.read_branch(
                space.id,
                branch_slug,
                space.repo_url,
                space_username,
                space_token,
                lambda wc: _read_raw_files(wc, source_slug),
            )
        except WorkdirError as exc:
            raise CollectionError(exc.code) from None
        raw = _RawSnapshot(raw_files)

        # 2. Inventaire des fichiers source (la table de travail), avec leur crc.
        working = {path: checksum(data) for path, data in source_files.items()}
        statuts: dict[str, str] = {path: STATUT_IGNORE for path in working}
        messages: dict[str, str] = {}

        # 3. Plugins en série ; chacun décide (index + crc), rend des actions.
        # La barre avance par parseur, au prorata de ses fichiers (un parseur
        # traite les siens en un seul lot : classes partielles, références).
        agg = _Aggregate()
        batches = [
            (plugin, {p: d for p, d in source_files.items() if matches_globs(plugin.globs(), p)})
            for plugin in plugins
        ]
        progress.enter("process")
        progress.total = sum(len(m) for _, m in batches)
        progress.processed = 0
        for plugin, matching in batches:
            progress.current = plugin.label
            stored = plugin_params.get(plugin.id, {})
            params = {p.slug: stored.get(p.slug, p.default) for p in plugin.parameters()}
            context = PluginContext(
                files=[FileRow(path=p, crc=working[p]) for p in sorted(matching)],
                contents=matching,
                raw=raw,
                params=params,
                force=force,
                branch=branch_label,
            )
            # Hors de la boucle d'événements : un parseur peut prendre des minutes,
            # et le serveur doit continuer de répondre (dont l'état de la collecte).
            result = await asyncio.to_thread(plugin.run, context)
            progress.processed += len(matching)
            agg.merge(result)
            for src, statut in result.statuses.items():
                statuts[src] = statut
            for err in result.errors:
                statuts[err.source] = STATUT_ERREUR
                # scan_files n'a qu'une colonne de message : le code d'abord, la
                # précision sur la ligne suivante (l'interface sépare les deux).
                messages[err.source] = f"{err.cause}\n{err.detail}" if err.detail else err.cause

        # 4. Service de recherche commun : le socle résout les références croisées
        # (x-unresolved-type → $ref) sur l'ensemble des schémas de la source.
        progress.enter("resolve")
        # Les fichiers du dépôt donnent la carte des projets (.csproj), lue une
        # seule fois : elle départage les homonymes entre projets d'une solution
        # et décrit chaque projet, qui devient un objet module (cartographie).
        projects = await asyncio.to_thread(read_project_map, source_files)
        modules = await asyncio.to_thread(
            csharp_module_actions, raw_files, list(agg.writes), list(agg.deletes), projects
        )
        # Projets Gradle et Maven : un objet module chacun (Kotlin / Java).
        jvm_projects = await asyncio.to_thread(read_jvm_projects, source_files)
        jvm = await asyncio.to_thread(
            jvm_module_actions, raw_files, modules.writes, modules.deletes, jvm_projects
        )
        # Paquets npm (`package.json`) : un objet module chacun (TypeScript / JavaScript).
        npm_projects = await asyncio.to_thread(read_npm_projects, source_files)
        npm = await asyncio.to_thread(
            npm_module_actions, raw_files, jvm.writes, jvm.deletes, npm_projects
        )
        for step in (modules, jvm, npm):
            statuts.update(step.statuses)
            messages.update(step.errors)
            agg.added += step.added
            agg.modified += step.modified
            agg.deleted += step.deleted
        # Les fonctions libres (minimal API…) vont dans le `.extend.yaml` de leur module.
        writes, deletes = await asyncio.to_thread(
            sync_module_functions, raw_files, npm.writes, npm.deletes
        )
        writes, resolved = await asyncio.to_thread(
            resolve_source_schemas, raw_files, writes, deletes, source_files, projects
        )
        # Points d'entrée HTTP ↔ opérations des contrats OpenAPI de la source :
        # l'état au contrat va dans les `.extend.yaml` (le contrat n'est pas touché).
        writes = await asyncio.to_thread(sync_contract_links, raw_files, writes, deletes)
        # Cartographie applicative : les `.view` des modules suivent les modules et
        # leurs dépendances, par ajouts et suppressions (jamais réécrits en entier).
        writes, deletes = await asyncio.to_thread(sync_module_views, raw_files, writes, deletes)

        # 5. Écriture unique + commit local unique.
        descripteur = commit_descriptor or (
            f" {source_commit[:7]}" if source_commit else " (contrats)"
        )
        message = (
            f"collecte {source_slug}{descripteur} : +{agg.added} ~{agg.modified} -{agg.deleted}"
        )
        progress.enter("write")
        try:
            local_commit = await workdir.commit_to_branch(
                space.id,
                branch_slug,
                space.repo_url,
                space_username,
                space_token,
                author_name,
                author_email,
                message,
                lambda wc: _apply_actions(wc, source_slug, writes, deletes),
            )
        except WorkdirError as exc:
            raise CollectionError(exc.code) from None

        progress.finish()
        log.info(
            "source_collected",
            space_id=str(space.id),
            slug=source_slug,
            source_commit=source_commit,
            added=agg.added,
            modified=agg.modified,
            deleted=agg.deleted,
            resolved=resolved,
        )
        files = [
            FileStatus(
                path=path,
                crc=working[path],
                cible=CIBLE_RAW,
                statut=statuts[path],
                message=messages.get(path, ""),
            )
            for path in sorted(working)
        ]
        return CollectReport(
            source_commit=source_commit,
            local_commit=local_commit,
            added=agg.added,
            modified=agg.modified,
            deleted=agg.deleted,
            unchanged=agg.unchanged,
            read=agg.read,
            retained=agg.retained,
            errors=agg.errors,
            duration_ms=int((time.monotonic() - start) * 1000),
            resolved=resolved,
            files=files,
        )

    async def collect(
        self,
        *,
        workdir: DraftWorkdir,
        space: GitSpace,
        branch_slug: str,
        branch_label: str,
        space_username: str,
        space_token: str | None,
        source_slug: str,
        source_clone_url: str,
        source_branch: str,
        source_username: str,
        source_token: str | None,
        author_name: str,
        author_email: str,
        force: bool,
        parsers: list[str] | None = None,
        directories: list[str] | None = None,
        exclusions: list[str] | None = None,
        plugin_params: dict[str, dict[str, str]] | None = None,
        progress: CollectProgress | None = None,
    ) -> CollectReport:
        progress = progress or CollectProgress()
        key = f"{space.id}/{branch_slug}/{source_slug}"
        async with self._lock(key):
            start = time.monotonic()
            progress.enter("download")
            try:
                source_commit = await self._downloader.download(
                    space.id,
                    source_slug,
                    source_clone_url,
                    source_branch,
                    source_username,
                    source_token,
                )
            except SourceDownloadError as exc:
                raise CollectionError(exc.code) from None
            source_dir = self._downloader.source_path(space.id, source_slug)
            progress.enter("scan")
            source_files = await asyncio.to_thread(
                _read_source_files,
                source_dir,
                directories=directories,
                exclusions=exclusions,
                progress=progress,
            )
            return await self._run(
                workdir=workdir,
                space=space,
                branch_slug=branch_slug,
                branch_label=branch_label,
                space_username=space_username,
                space_token=space_token,
                source_slug=source_slug,
                source_files=source_files,
                plugins=select_code_plugins(parsers),
                author_name=author_name,
                author_email=author_email,
                force=force,
                source_commit=source_commit,
                start=start,
                plugin_params=plugin_params or {},
                progress=progress,
            )

    async def find_contracts(
        self,
        *,
        space: GitSpace,
        source_slug: str,
        source_clone_url: str,
        source_branch: str,
        source_username: str,
        source_token: str | None,
        directories: list[str] | None = None,
        exclusions: list[str] | None = None,
    ) -> list[FoundContract]:
        """« Rechercher les contrats » (ticket 56be38c6) : descend le dépôt comme
        la collecte, puis liste les contrats OpenAPI trouvés — sans rien écrire
        dans le brouillon. Mêmes sous-répertoires et exclusions que la collecte,
        pour montrer exactement ce qu'elle prendrait."""
        try:
            await self._downloader.download(
                space.id,
                source_slug,
                source_clone_url,
                source_branch,
                source_username,
                source_token,
            )
        except SourceDownloadError as exc:
            raise CollectionError(exc.code) from None
        source_dir = self._downloader.source_path(space.id, source_slug)
        repo_plugin = next(p for p in select_code_plugins(["openapi"]))
        files = await asyncio.to_thread(
            _read_source_files,
            source_dir,
            directories=directories,
            exclusions=exclusions,
            patterns=repo_plugin.globs(),
        )

        def inspect_all() -> list[FoundContract]:
            found = (inspect_contract(path, data) for path, data in sorted(files.items()))
            return [c for c in found if c is not None]

        return await asyncio.to_thread(inspect_all)

    async def collect_openapi(
        self,
        *,
        workdir: DraftWorkdir,
        space: GitSpace,
        branch_slug: str,
        branch_label: str,
        space_username: str,
        space_token: str | None,
        source_slug: str,
        author_name: str,
        author_email: str,
        force: bool,
        plugin_params: dict[str, dict[str, str]] | None = None,
        progress: CollectProgress | None = None,
    ) -> CollectReport:
        """Collecte une source `openapi` : pas de descente (les contrats sont déjà
        versionnés dans `contrats/`), `source_commit` vide."""
        key = f"{space.id}/{branch_slug}/{source_slug}"
        async with self._lock(key):
            start = time.monotonic()
            progress = progress or CollectProgress()
            progress.enter("scan")
            try:
                contrats = await workdir.read_branch(
                    space.id,
                    branch_slug,
                    space.repo_url,
                    space_username,
                    space_token,
                    lambda wc: _read_contrats(wc, source_slug),
                )
            except WorkdirError as exc:
                raise CollectionError(exc.code) from None
            return await self._run(
                workdir=workdir,
                space=space,
                branch_slug=branch_slug,
                branch_label=branch_label,
                space_username=space_username,
                space_token=space_token,
                source_slug=source_slug,
                source_files=contrats,
                plugins=[openapi_plugin()],
                author_name=author_name,
                author_email=author_email,
                force=force,
                source_commit="",
                start=start,
                plugin_params=plugin_params or {},
                progress=progress,
            )

    async def collect_sql(
        self,
        *,
        workdir: DraftWorkdir,
        space: GitSpace,
        branch_slug: str,
        branch_label: str,
        space_username: str,
        space_token: str | None,
        source_slug: str,
        engine: DatabaseEngine,
        dialect: str,
        db_params: dict[str, str],
        secret_value: str,
        schemas: list[str] | None,
        exclusions: list[str] | None,
        author_name: str,
        author_email: str,
        force: bool,
        progress: CollectProgress | None = None,
    ) -> CollectReport:
        """Collecte une source base : lit le catalogue (lecture seule, structure
        seulement), transforme chaque objet en schéma. `source_commit` vide : il
        n'y a pas de dépôt descendu, l'original est la définition normalisée lue."""
        key = f"{space.id}/{branch_slug}/{source_slug}"
        async with self._lock(key):
            start = time.monotonic()
            progress = progress or CollectProgress()
            progress.enter("download")
            try:
                read = await engine.read_catalog(
                    db_params, secret_value, schemas=schemas, exclusions=exclusions
                )
            except DbEngineError as exc:
                raise CollectionError(exc.code) from None
            source_files = {source_key(obj): object_to_source(obj) for obj in read.objects}
            report = await self._run(
                workdir=workdir,
                space=space,
                branch_slug=branch_slug,
                branch_label=branch_label,
                space_username=space_username,
                space_token=space_token,
                source_slug=source_slug,
                source_files=source_files,
                plugins=[sql_extractor(dialect)],
                author_name=author_name,
                author_email=author_email,
                force=force,
                source_commit="",
                start=start,
                plugin_params={},
                commit_descriptor=" (catalogue)",
                progress=progress,
            )
            # Schémas inaccessibles (droits insuffisants) : signalés, sans bloquer.
            for schema_name, cause in read.errors:
                report.errors.append(PluginError(source=schema_name, cause=cause))
            return report
