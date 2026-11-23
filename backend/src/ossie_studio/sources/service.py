from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import structlog

from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.models import GitSpace
from ossie_studio.sources.exclusion_prune import prune_excluded_raw
from ossie_studio.sources.manifest import (
    GIT_TYPE,
    ManifestError,
    SourceManifest,
    dump_manifest,
    parse_manifest,
)
from ossie_studio.sources.paths import MANIFEST_FILENAME, SOURCES_DIR, safe_source_path

log = structlog.get_logger(__name__)


class SourceExistsError(Exception):
    """Une source du même slug existe déjà dans la branche."""

    def __init__(self, slug: str) -> None:
        super().__init__(slug)
        self.slug = slug


class SourceNotFoundError(Exception):
    """Aucune source de ce slug dans la branche."""

    def __init__(self, slug: str) -> None:
        super().__init__(slug)
        self.slug = slug


@dataclass(frozen=True)
class SourceEntry:
    """Une entrée de la liste : soit un manifeste valide, soit une erreur (un
    répertoire `sources/<slug>/` sans manifeste valide n'est jamais ignoré en
    silence — critère de la feature)."""

    slug: str
    manifest: SourceManifest | None
    error: str | None


def read_sources_tree(workdir: Path) -> list[SourceEntry]:
    """Scanne `sources/*/` de la copie de travail. Chaque sous-répertoire est une
    source ; sans manifeste valide, il est listé en erreur."""
    base = workdir / SOURCES_DIR
    if not base.is_dir():
        return []
    entries: list[SourceEntry] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        slug = child.name
        manifest_file = child / MANIFEST_FILENAME
        if not manifest_file.is_file():
            entries.append(SourceEntry(slug, None, "manifest_missing"))
            continue
        try:
            manifest = parse_manifest(manifest_file.read_text(encoding="utf-8"))
        except ManifestError as exc:
            reason = exc.fields[0] if exc.fields else "manifest_invalid"
            entries.append(SourceEntry(slug, None, reason))
            continue
        if manifest.slug != slug:
            entries.append(SourceEntry(slug, None, "slug_mismatch"))
            continue
        entries.append(SourceEntry(slug, manifest, None))
    return entries


def _write_manifest_fn(manifest: SourceManifest, *, must_be_new: bool) -> Callable[[Path], None]:
    def write(workdir: Path) -> None:
        target = safe_source_path(workdir, manifest.slug)
        if must_be_new and target.exists():
            raise SourceExistsError(manifest.slug)
        if not must_be_new and not (target / MANIFEST_FILENAME).is_file():
            raise SourceNotFoundError(manifest.slug)
        target.mkdir(parents=True, exist_ok=True)
        (target / MANIFEST_FILENAME).write_text(dump_manifest(manifest), encoding="utf-8")
        if manifest.type == GIT_TYPE:
            # Dans la même écriture : un objet venu d'un répertoire exclu sort
            # aussitôt du brouillon (ticket docflow 64e7b42f).
            prune_excluded_raw(workdir, manifest.slug, _exclusions(manifest))

    return write


def _exclusions(manifest: SourceManifest) -> list[str]:
    value = manifest.parameters.get("exclusions")
    return [str(v) for v in value] if isinstance(value, list) else []


def _remove_source_fn(slug: str) -> Callable[[Path], None]:
    def write(workdir: Path) -> None:
        target = safe_source_path(workdir, slug)
        if not target.exists():
            raise SourceNotFoundError(slug)
        shutil.rmtree(target)

    return write


async def list_sources(
    workdir: DraftWorkdir, space: GitSpace, branch_slug: str, username: str, token: str | None
) -> list[SourceEntry]:
    return await workdir.read_branch(
        space.id, branch_slug, space.repo_url, username, token, read_sources_tree
    )


async def declare_source(
    workdir: DraftWorkdir,
    space: GitSpace,
    branch_slug: str,
    username: str,
    token: str | None,
    author_name: str,
    author_email: str,
    manifest: SourceManifest,
) -> None:
    await workdir.commit_to_branch(
        space.id,
        branch_slug,
        space.repo_url,
        username,
        token,
        author_name,
        author_email,
        f"déclare la source {manifest.slug}",
        _write_manifest_fn(manifest, must_be_new=True),
    )
    log.info("source_declared", space_id=str(space.id), slug=manifest.slug, type=manifest.type)


async def update_source(
    workdir: DraftWorkdir,
    space: GitSpace,
    branch_slug: str,
    username: str,
    token: str | None,
    author_name: str,
    author_email: str,
    manifest: SourceManifest,
) -> None:
    await workdir.commit_to_branch(
        space.id,
        branch_slug,
        space.repo_url,
        username,
        token,
        author_name,
        author_email,
        f"modifie la source {manifest.slug}",
        _write_manifest_fn(manifest, must_be_new=False),
    )
    log.info("source_updated", space_id=str(space.id), slug=manifest.slug)


async def delete_source(
    workdir: DraftWorkdir,
    space: GitSpace,
    branch_slug: str,
    username: str,
    token: str | None,
    author_name: str,
    author_email: str,
    slug: str,
) -> None:
    await workdir.commit_to_branch(
        space.id,
        branch_slug,
        space.repo_url,
        username,
        token,
        author_name,
        author_email,
        f"supprime la source {slug}",
        _remove_source_fn(slug),
    )
    log.info("source_deleted", space_id=str(space.id), slug=slug)
