from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import structlog

from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.models import GitSpace
from ossie_studio.roundtrip import write_document
from ossie_studio.sources.exclusion_prune import prune_excluded_raw
from ossie_studio.sources.manifest import (
    GIT_TYPE,
    ManifestError,
    SourceManifest,
    dump_manifest,
    parse_manifest,
)
from ossie_studio.sources.paths import (
    ASSETS_DIR,
    MANIFEST_FILENAME,
    SOURCES_DIR,
    safe_source_path,
)

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
        if is_assets_only(child):
            continue  # source supprimée : seuls ses assets orphelins restent (Curation)
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
        # Le manifeste, et non le dossier : une source supprimée laisse ses assets
        # (ticket ce21a9e8), et la redéclarer au même slug les retrouve.
        if must_be_new and (target / MANIFEST_FILENAME).exists():
            raise SourceExistsError(manifest.slug)
        if not must_be_new and not (target / MANIFEST_FILENAME).is_file():
            raise SourceNotFoundError(manifest.slug)
        target.mkdir(parents=True, exist_ok=True)
        # Aller-retour (14bb8eb8) : un manifeste modifié garde commentaires et forme.
        write_document(
            target / MANIFEST_FILENAME, manifest.to_mapping(), lambda _m: dump_manifest(manifest)
        )
        if manifest.type == GIT_TYPE:
            # Dans la même écriture : un objet venu d'un répertoire exclu sort
            # aussitôt du brouillon (ticket docflow 64e7b42f).
            prune_excluded_raw(workdir, manifest.slug, _exclusions(manifest))

    return write


def _exclusions(manifest: SourceManifest) -> list[str]:
    value = manifest.parameters.get("exclusions")
    return [str(v) for v in value] if isinstance(value, list) else []


def _remove_source_fn(slug: str) -> Callable[[Path], None]:
    """Supprime la source — manifeste, raw et le reste — mais **garde ses
    assets** (ticket ce21a9e8, Gael : « si je supprime les raws, je dois voir les
    orphelins sur les assets »). Le travail humain des `.extend.yaml` n'est jamais
    effacé en même temps que de la matière brute : les assets restent, orphelins,
    et retrouvent leur raw si la source est redéclarée et recollectée. Effacer
    tout le dossier avait fait perdre ses assets à Gael (bug 206d434c)."""

    def write(workdir: Path) -> None:
        target = safe_source_path(workdir, slug)
        if not (target / MANIFEST_FILENAME).is_file():
            raise SourceNotFoundError(slug)
        assets = target / ASSETS_DIR
        if not _has_files(assets):
            shutil.rmtree(target)
            return
        for child in target.iterdir():
            if child == assets:
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

    return write


def _has_files(directory: Path) -> bool:
    return directory.is_dir() and any(p.is_file() for p in directory.rglob("*"))


def is_assets_only(directory: Path) -> bool:
    """Dossier d'une source supprimée qui ne garde que ses assets (orphelins)."""
    return (
        not (directory / MANIFEST_FILENAME).is_file()
        and all(child.name == ASSETS_DIR for child in directory.iterdir())
        and _has_files(directory / ASSETS_DIR)
    )


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
