from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

import structlog

from ossie_studio.sources.paths import is_valid_source_slug
from ossie_studio.spaces.gitcli import classify, git_env, run_git, write_askpass

log = structlog.get_logger(__name__)


class SourceDownloadError(Exception):
    """Échec de descente du code d'une source, porté par un code stable
    (branch_not_found, repo_not_found, auth_failed, forge_unreachable,
    git_failed, slug_invalid). Jamais de jeton ni de corps d'erreur dedans."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _ref_error(err: str) -> str:
    s = err.lower()
    if "remote branch" in s or "couldn't find remote ref" in s:
        return "branch_not_found"
    cause = classify(err)
    if cause in ("forge_unreachable", "auth_failed", "repo_not_found"):
        return cause
    return "git_failed"


class SourceDownloader:
    """Descend le code des sources Git sur le **volume des sources** (non
    versionné) : un répertoire par (espace, slug de source), aligné sur la tête
    de la branche suivie. Lecture seule vis-à-vis du dépôt source ; la copie est
    jetable (corrompue → supprimée et reclonée) ; un verrou par répertoire évite
    deux descentes concurrentes."""

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self._root = root
        self._askpass = write_askpass(root)
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    def _safe_dir(self, space_id: uuid.UUID, slug: str) -> Path:
        # L'espace sépare deux sources de même slug ; la garde empêche toute
        # sortie du volume depuis une saisie.
        if not is_valid_source_slug(slug):
            raise SourceDownloadError("slug_invalid")
        base = (self._root / str(space_id)).resolve()
        target = (self._root / str(space_id) / slug).resolve()
        if base != target.parent:
            raise SourceDownloadError("slug_invalid")
        return self._root / str(space_id) / slug

    async def download(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        branch: str,
        username: str,
        token: str | None,
    ) -> str:
        """Clone (premier passage) ou met à jour (passages suivants) le code à la
        tête de `branch`, et rend l'identifiant du commit descendu."""
        workdir = self._safe_dir(space_id, slug)
        async with self._lock(str(workdir)):
            env = git_env(self._root, self._askpass, username, token)
            if (workdir / ".git").is_dir():
                # Santé locale : une copie corrompue est supprimée et reclonée,
                # sans la confondre avec une erreur distante (jeton, réseau).
                code, _, _ = await run_git(["rev-parse", "--git-dir"], workdir, env)
                if code != 0:
                    shutil.rmtree(workdir, ignore_errors=True)
            if (workdir / ".git").is_dir():
                return await self._update(workdir, branch, env)
            return await self._clone(workdir, repo_url, branch, env)

    async def _clone(self, workdir: Path, repo_url: str, branch: str, env: dict[str, str]) -> str:
        # Reste d'un clone interrompu : on repart propre (no-op si absent).
        shutil.rmtree(workdir, ignore_errors=True)
        workdir.parent.mkdir(parents=True, exist_ok=True)
        code, _, err = await run_git(
            ["clone", "-q", "--single-branch", "--branch", branch, repo_url, str(workdir)],
            self._root,
            env,
        )
        if code != 0:
            shutil.rmtree(workdir, ignore_errors=True)
            raise SourceDownloadError(_ref_error(err))
        return await self._head(workdir, env)

    async def _update(self, workdir: Path, branch: str, env: dict[str, str]) -> str:
        # Fetch explicite de la branche suivie (gère aussi un changement de
        # branche dans le manifeste). La copie existante n'est pas touchée si la
        # forge refuse.
        code, _, err = await run_git(["fetch", "-q", "origin", branch], workdir, env)
        if code != 0:
            raise SourceDownloadError(_ref_error(err))
        code, _, err = await run_git(["reset", "-q", "--hard", "FETCH_HEAD"], workdir, env)
        if code != 0:
            raise SourceDownloadError("git_failed")
        return await self._head(workdir, env)

    async def _head(self, workdir: Path, env: dict[str, str]) -> str:
        code, out, _ = await run_git(["rev-parse", "HEAD"], workdir, env)
        if code != 0:
            raise SourceDownloadError("git_failed")
        return out.strip()

    async def remove(self, space_id: uuid.UUID, slug: str) -> None:
        """Supprime le code descendu d'une source du volume (à la suppression de
        la source)."""
        workdir = self._safe_dir(space_id, slug)
        async with self._lock(str(workdir)):
            shutil.rmtree(workdir, ignore_errors=True)
            log.info("source_code_removed", space_id=str(space_id), slug=slug)
