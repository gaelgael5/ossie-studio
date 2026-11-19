from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

import structlog

from ossie_studio.models import GitSpace
from ossie_studio.parsers.registry import all_parsers, openapi_parsers
from ossie_studio.sources.collect_core import (
    RAW_DIR,
    TECH_MANIFEST_NAME,
    CollectError,
    TechManifest,
    dump_tech_manifest,
    parse_tech_manifest,
    run_collection,
)
from ossie_studio.sources.collect_core import CollectionPlan as _Plan
from ossie_studio.sources.download import SourceDownloader, SourceDownloadError
from ossie_studio.sources.paths import CONTRATS_DIR, CONTRATS_INDEX, SOURCES_DIR
from ossie_studio.spaces.workdir import WorkdirError, WorkdirManager

log = structlog.get_logger(__name__)


class CollectionError(Exception):
    """Collecte refusée ou impossible, portée par un code stable (branch_behind,
    branch_not_found, repo_not_found, auth_failed, forge_unreachable, git_failed)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


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
    errors: list[CollectError]
    duration_ms: int


def _read_source_files(root: Path) -> dict[str, bytes]:
    """Lit les fichiers du code descendu (hors .git), chemin relatif POSIX → octets."""
    files: dict[str, bytes] = {}
    if not root.is_dir():
        return files
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if ".git" in rel.parts:
            continue
        files[rel.as_posix()] = path.read_bytes()
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


def _read_raw(work_copy: Path, source_slug: str) -> tuple[TechManifest, dict[str, str]]:
    raw = _raw_dir(work_copy, source_slug)
    manifest_file = raw / TECH_MANIFEST_NAME
    previous = (
        parse_tech_manifest(manifest_file.read_text(encoding="utf-8"))
        if manifest_file.is_file()
        else TechManifest()
    )
    schemas: dict[str, str] = {}
    if raw.is_dir():
        for schema in raw.glob("*.json"):
            schemas[schema.name] = schema.read_text(encoding="utf-8")
    return previous, schemas


def _apply_plan(work_copy: Path, source_slug: str, plan: _Plan) -> None:
    raw = _raw_dir(work_copy, source_slug)
    raw.mkdir(parents=True, exist_ok=True)
    for dest, content in plan.writes.items():
        (raw / dest).write_text(content, encoding="utf-8")
    for dest in plan.deletes:
        (raw / dest).unlink(missing_ok=True)
    (raw / TECH_MANIFEST_NAME).write_text(dump_tech_manifest(plan.manifest), encoding="utf-8")


class SourceCollector:
    """Orchestre une collecte : branche à jour → descente → lecture incrémentale
    → commit local. Un verrou par (espace, branche, source) garantit qu'une seule
    collecte tourne à la fois sur une même source."""

    def __init__(self, workdir: WorkdirManager, downloader: SourceDownloader) -> None:
        self._workdir = workdir
        self._downloader = downloader
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    async def collect(
        self,
        *,
        space: GitSpace,
        branch_slug: str,
        branche_label: str,
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
    ) -> CollectReport:
        key = f"{space.id}/{branch_slug}/{source_slug}"
        async with self._lock(key):
            start = time.monotonic()
            # 1. Branche de travail à jour ; refus si périmée.
            try:
                ahead, behind = await self._workdir.divergence(
                    space.id, branch_slug, space.repo_url, space_username, space_token
                )
            except WorkdirError as exc:
                raise CollectionError(exc.code) from None
            if ahead > 0 and behind > 0:
                raise CollectionError("branch_behind")

            # 2. Source à jour (descente), identifiant du commit descendu.
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
            source_files = await asyncio.to_thread(_read_source_files, source_dir)

            # 3. Lecture incrémentale sur l'état courant de raw/.
            previous, existing = await self._workdir.read_branch(
                space.id,
                branch_slug,
                space.repo_url,
                space_username,
                space_token,
                lambda wc: _read_raw(wc, source_slug),
            )
            plan = run_collection(
                source_files, all_parsers(), previous, existing, branche=branche_label, force=force
            )

            # 4-5. Commit local unique (message : source, commit source, compteurs).
            message = (
                f"collecte {source_slug} {source_commit[:7]} : "
                f"+{plan.added} ~{plan.modified} -{plan.deleted}"
            )
            try:
                local_commit = await self._workdir.commit_to_branch(
                    space.id,
                    branch_slug,
                    space.repo_url,
                    space_username,
                    space_token,
                    author_name,
                    author_email,
                    message,
                    lambda wc: _apply_plan(wc, source_slug, plan),
                )
            except WorkdirError as exc:
                raise CollectionError(exc.code) from None

            log.info(
                "source_collected",
                space_id=str(space.id),
                slug=source_slug,
                source_commit=source_commit,
                added=plan.added,
                modified=plan.modified,
                deleted=plan.deleted,
            )
            return CollectReport(
                source_commit=source_commit,
                local_commit=local_commit,
                added=plan.added,
                modified=plan.modified,
                deleted=plan.deleted,
                unchanged=plan.unchanged,
                read=plan.read,
                retained=plan.retained,
                errors=plan.errors,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

    async def collect_openapi(
        self,
        *,
        space: GitSpace,
        branch_slug: str,
        branche_label: str,
        space_username: str,
        space_token: str | None,
        source_slug: str,
        author_name: str,
        author_email: str,
        force: bool,
    ) -> CollectReport:
        """Collecte une source `openapi` : pas de descente (les contrats sont déjà
        versionnés dans `contrats/`), lecture incrémentale → `raw/`, commit local.
        `source_commit` est vide : rien n'est descendu."""
        key = f"{space.id}/{branch_slug}/{source_slug}"
        async with self._lock(key):
            start = time.monotonic()
            try:
                ahead, behind = await self._workdir.divergence(
                    space.id, branch_slug, space.repo_url, space_username, space_token
                )
            except WorkdirError as exc:
                raise CollectionError(exc.code) from None
            if ahead > 0 and behind > 0:
                raise CollectionError("branch_behind")

            previous, existing, contrats = await self._workdir.read_branch(
                space.id,
                branch_slug,
                space.repo_url,
                space_username,
                space_token,
                lambda wc: (*_read_raw(wc, source_slug), _read_contrats(wc, source_slug)),
            )
            plan = run_collection(
                contrats, openapi_parsers(), previous, existing, branche=branche_label, force=force
            )
            message = (
                f"collecte {source_slug} (contrats) : "
                f"+{plan.added} ~{plan.modified} -{plan.deleted}"
            )
            try:
                local_commit = await self._workdir.commit_to_branch(
                    space.id,
                    branch_slug,
                    space.repo_url,
                    space_username,
                    space_token,
                    author_name,
                    author_email,
                    message,
                    lambda wc: _apply_plan(wc, source_slug, plan),
                )
            except WorkdirError as exc:
                raise CollectionError(exc.code) from None

            log.info(
                "source_collected",
                space_id=str(space.id),
                slug=source_slug,
                source="contrats",
                added=plan.added,
                modified=plan.modified,
                deleted=plan.deleted,
            )
            return CollectReport(
                source_commit="",
                local_commit=local_commit,
                added=plan.added,
                modified=plan.modified,
                deleted=plan.deleted,
                unchanged=plan.unchanged,
                read=plan.read,
                retained=plan.retained,
                errors=plan.errors,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
