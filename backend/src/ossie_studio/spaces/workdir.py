from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import structlog

from ossie_studio.spaces.gitcli import (
    classify,
    default_branch,
    git_env,
    run_git,
    write_askpass,
)

log = structlog.get_logger(__name__)

WORK_PREFIX = "work/"
_SPACE_DESCRIPTION = "ossie-space.yaml"


class WorkdirError(Exception):
    """Échec d'un geste git sur une copie de travail, porté par un code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _access_code(cause: str) -> str:
    # Les causes d'accès (identifiants, dépôt injoignable) désignent l'accès ;
    # les autres restent une erreur git générique.
    if cause in ("auth_failed", "repo_not_found", "forge_unreachable"):
        return "repo_access_lost"
    return "git_failed"


class WorkdirManager:
    """Copies de travail persistantes : un répertoire par (espace, branche) sur
    le volume de travail, réalignées sur la tête distante à chaque geste, avec
    un verrou par répertoire pour que deux gestes ne se piétinent pas."""

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self._root = root
        self._askpass = write_askpass(root)
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    def _dir(self, space_id: uuid.UUID, slug: str) -> Path:
        return self._root / str(space_id) / slug

    def _env(self, username: str, token: str | None) -> dict[str, str]:
        return git_env(self._root, self._askpass, username, token)

    async def _clone_or_refresh(self, workdir: Path, repo_url: str, env: dict[str, str]) -> None:
        if (workdir / ".git").exists():
            code, _, err = await run_git(["fetch", "-q", "origin"], workdir, env)
            if code != 0:
                raise WorkdirError(_access_code(classify(err)))
            return
        workdir.parent.mkdir(parents=True, exist_ok=True)
        code, _, err = await run_git(["clone", "-q", repo_url, str(workdir)], self._root, env)
        if code != 0:
            raise WorkdirError(_access_code(classify(err)))

    async def _remote_branch_exists(self, workdir: Path, ref: str, env: dict[str, str]) -> bool:
        code, out, _ = await run_git(["ls-remote", "--heads", "origin", ref], workdir, env)
        return code == 0 and bool(out.strip())

    async def _default_branch(self, workdir: Path, env: dict[str, str]) -> str:
        code, out, err = await run_git(["ls-remote", "--heads", "origin"], workdir, env)
        if code != 0:
            raise WorkdirError(_access_code(classify(err)))
        heads = [line.split("\t")[1].removeprefix("refs/heads/") for line in out.splitlines()]
        if not heads:
            raise WorkdirError("branch_not_found")
        return default_branch(heads)

    async def create_base_branch(
        self, space_id: uuid.UUID, slug: str, repo_url: str, username: str, token: str | None
    ) -> None:
        ref = f"{WORK_PREFIX}{slug}"
        workdir = self._dir(space_id, slug)
        async with self._lock(str(workdir)):
            env = self._env(username, token)
            await self._clone_or_refresh(workdir, repo_url, env)
            if await self._remote_branch_exists(workdir, ref, env):
                raise WorkdirError("branch_exists")
            # La base se tire de la racine (branche par défaut du dépôt),
            # déterminée depuis les têtes distantes (le HEAD d'un bare peut être
            # incohérent).
            default = await self._default_branch(workdir, env)
            await run_git(["fetch", "-q", "origin", default], workdir, env)
            await run_git(["checkout", "-q", "-B", ref, "FETCH_HEAD"], workdir, env)
            self._pose_initial_tree(workdir)
            await run_git(["add", "-A"], workdir, env)
            code, out, _ = await run_git(["status", "--porcelain"], workdir, env)
            if out.strip():
                await run_git(["commit", "-q", "-m", "Arborescence initiale"], workdir, env)
            code, _, err = await run_git(["push", "-q", "origin", ref], workdir, env)
            if code != 0:
                raise WorkdirError(_access_code(classify(err)))
            log.info("base_branch_created", space_id=str(space_id), ref=ref)

    async def create_child_branch(
        self,
        space_id: uuid.UUID,
        base_slug: str,
        child_slug: str,
        repo_url: str,
        username: str,
        token: str | None,
    ) -> None:
        base_ref = f"{WORK_PREFIX}{base_slug}"
        child_ref = f"{WORK_PREFIX}{child_slug}"
        workdir = self._dir(space_id, child_slug)
        async with self._lock(str(workdir)):
            env = self._env(username, token)
            await self._clone_or_refresh(workdir, repo_url, env)
            if not await self._remote_branch_exists(workdir, base_ref, env):
                raise WorkdirError("branch_not_found")
            if await self._remote_branch_exists(workdir, child_ref, env):
                raise WorkdirError("branch_exists")
            # Une fille se tire de la tête de la base, sans commit (pointeur).
            code, _, err = await run_git(["fetch", "-q", "origin", base_ref], workdir, env)
            if code != 0:
                raise WorkdirError(_access_code(classify(err)))
            code, _, err = await run_git(
                ["push", "-q", "origin", f"FETCH_HEAD:refs/heads/{child_ref}"], workdir, env
            )
            if code != 0:
                raise WorkdirError(_access_code(classify(err)))
            log.info("child_branch_created", space_id=str(space_id), ref=child_ref)

    def _pose_initial_tree(self, workdir: Path) -> None:
        # Pose l'arborescence attendue si elle manque ; ne touche jamais à
        # ce qui existe déjà.
        sources = workdir / "sources"
        if not sources.exists():
            sources.mkdir()
            (sources / ".gitkeep").write_text("")
        description = workdir / _SPACE_DESCRIPTION
        if not description.exists():
            description.write_text("# Espace Ossie Studio\n")
