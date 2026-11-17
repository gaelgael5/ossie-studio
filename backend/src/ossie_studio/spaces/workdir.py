from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
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
# Préfixe reconnaissable : distingue dans l'historique ce qu'a fait Ossie-studio.
_COMMIT_PREFIX = "ossie: "
_UNIT_SEP = "\x1f"


class WorkdirError(Exception):
    """Échec d'un geste git sur une copie de travail, porté par un code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class UnpublishedCommit:
    sha: str
    author: str
    date: str
    message: str


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

    async def _checkout_branch(self, workdir: Path, ref: str, env: dict[str, str]) -> None:
        """Met la copie sur `ref`. Si la branche locale n'a aucun commit en
        attente, elle se réaligne sur la forge (elle fait foi) ; s'il reste des
        commits non publiés, on garde le local pour ne rien perdre."""
        remote = f"origin/{ref}"
        code, _, _ = await run_git(
            ["rev-parse", "--verify", "--quiet", f"refs/heads/{ref}"], workdir, env
        )
        if code != 0:
            # Première venue sur cette copie : on part de la tête distante.
            code, _, err = await run_git(["checkout", "-q", "-B", ref, remote], workdir, env)
            if code != 0:
                raise WorkdirError(_access_code(classify(err)))
            return
        await run_git(["checkout", "-q", ref], workdir, env)
        if await self._count_ahead(workdir, ref, env) == 0:
            # Rien en attente : on se recale sur la forge.
            await run_git(["reset", "--hard", remote], workdir, env)

    async def _count_ahead(self, workdir: Path, ref: str, env: dict[str, str]) -> int:
        code, out, _ = await run_git(
            ["rev-list", "--count", f"origin/{ref}..{ref}"], workdir, env
        )
        if code != 0:
            return 0
        return int(out.strip() or "0")

    async def commit_to_branch(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        author_name: str,
        author_email: str,
        message: str,
        write_fn: Callable[[Path], None],
    ) -> bool:
        """Écrit dans la copie d'une branche de travail et produit un commit
        local (auteur = le compte déclencheur, message préfixé `ossie: `), sans
        pousser. Jamais de commit vide : renvoie False si l'écriture n'a rien
        changé."""
        ref = f"{WORK_PREFIX}{slug}"
        workdir = self._dir(space_id, slug)
        async with self._lock(str(workdir)):
            env = self._env(username, token)
            await self._clone_or_refresh(workdir, repo_url, env)
            if not await self._remote_branch_exists(workdir, ref, env):
                raise WorkdirError("branch_not_found")
            await self._checkout_branch(workdir, ref, env)
            # L'écriture ne touche que l'arborescence sous la copie.
            write_fn(workdir)
            await run_git(["add", "-A"], workdir, env)
            code, out, _ = await run_git(["status", "--porcelain"], workdir, env)
            if not out.strip():
                return False
            author_env = {
                **env,
                "GIT_AUTHOR_NAME": author_name,
                "GIT_AUTHOR_EMAIL": author_email,
                "GIT_COMMITTER_NAME": author_name,
                "GIT_COMMITTER_EMAIL": author_email,
            }
            code, _, err = await run_git(
                ["commit", "-q", "-m", f"{_COMMIT_PREFIX}{message}"], workdir, author_env
            )
            if code != 0:
                raise WorkdirError(_access_code(classify(err)))
            log.info("commit_to_branch", space_id=str(space_id), ref=ref)
            return True

    async def list_unpublished(
        self, space_id: uuid.UUID, slug: str, repo_url: str, username: str, token: str | None
    ) -> list[UnpublishedCommit]:
        """Liste les commits locaux pas encore sur la forge, du plus récent au
        plus ancien, sans rien modifier."""
        ref = f"{WORK_PREFIX}{slug}"
        workdir = self._dir(space_id, slug)
        async with self._lock(str(workdir)):
            env = self._env(username, token)
            await self._clone_or_refresh(workdir, repo_url, env)
            if not await self._remote_branch_exists(workdir, ref, env):
                raise WorkdirError("branch_not_found")
            code, _, _ = await run_git(
                ["rev-parse", "--verify", "--quiet", f"refs/heads/{ref}"], workdir, env
            )
            if code != 0:
                # Pas de copie locale : rien n'est en attente.
                return []
            fmt = _UNIT_SEP.join(["%H", "%an", "%aI", "%s"])
            code, out, err = await run_git(
                ["log", f"--pretty=format:{fmt}", f"origin/{ref}..{ref}"], workdir, env
            )
            if code != 0:
                raise WorkdirError(_access_code(classify(err)))
            commits: list[UnpublishedCommit] = []
            for line in out.splitlines():
                if not line.strip():
                    continue
                sha, author, date, message = line.split(_UNIT_SEP, 3)
                commits.append(
                    UnpublishedCommit(sha=sha, author=author, date=date, message=message)
                )
            return commits

    async def publish(
        self, space_id: uuid.UUID, slug: str, repo_url: str, username: str, token: str | None
    ) -> int:
        """Pousse les commits en attente sur la forge, sans force. Renvoie leur
        nombre (0 si rien à publier). Si la branche a avancé sur la forge, le
        push est rejeté (non fast-forward) → `branch_changed`, rien de perdu."""
        ref = f"{WORK_PREFIX}{slug}"
        workdir = self._dir(space_id, slug)
        async with self._lock(str(workdir)):
            env = self._env(username, token)
            await self._clone_or_refresh(workdir, repo_url, env)
            if not await self._remote_branch_exists(workdir, ref, env):
                raise WorkdirError("branch_not_found")
            code, _, _ = await run_git(
                ["rev-parse", "--verify", "--quiet", f"refs/heads/{ref}"], workdir, env
            )
            if code != 0:
                return 0
            ahead = await self._count_ahead(workdir, ref, env)
            if ahead == 0:
                return 0
            code, _, err = await run_git(["push", "-q", "origin", f"{ref}:{ref}"], workdir, env)
            if code != 0:
                low = err.lower()
                if "non-fast-forward" in low or "fetch first" in low or "[rejected]" in low:
                    raise WorkdirError("branch_changed")
                raise WorkdirError(_access_code(classify(err)))
            log.info("published", space_id=str(space_id), ref=ref, count=ahead)
            return ahead

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
