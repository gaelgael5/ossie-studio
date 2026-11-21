from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import structlog

from ossie_studio.spaces.gitcli import (
    classify,
    default_branch,
    git_env,
    run_git,
    write_askpass,
)
from ossie_studio.spaces.railmap import (
    BranchInput,
    CommitInput,
    RailMap,
    VersionAt,
    build_railmap,
)

log = structlog.get_logger(__name__)

_T = TypeVar("_T")

# Borne de l'historique chargé pour le diagramme : au-delà, on s'arrête (les
# arêtes vers des parents plus anciens deviennent des amorces « plus bas »).
_RAILMAP_CAP = 5000

WORK_PREFIX = "work/"
_SPACE_DESCRIPTION = "ossie-space.yaml"
# Préfixe reconnaissable : distingue dans l'historique ce qu'a fait Ossie-studio.
_COMMIT_PREFIX = "ossie: "
_UNIT_SEP = "\x1f"
# Une version est un tag annoté « version/<n> » posé sur la tête publiée.
_VERSION_PREFIX = "version/"
_VERSION_RE = re.compile(r"^version/(\d+)$")
_VERSION_TAGS = f"{_VERSION_PREFIX}*"
_VERSION_FETCH_REFSPEC = f"+refs/tags/{_VERSION_PREFIX}*:refs/tags/{_VERSION_PREFIX}*"


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


@dataclass(frozen=True)
class Version:
    number: int
    label: str
    commit: str
    author: str
    date: str


@dataclass(frozen=True)
class VersionCreated:
    number: int
    commit: str
    already_exists: bool


@dataclass(frozen=True)
class VersionRestored:
    number: int
    label: str
    already_current: bool
    created: bool


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
        """Aligne la copie de lecture sur la tête distante de `ref`. La copie ne
        porte jamais de commit local (le travail en cours vit dans les brouillons) :
        des commits locaux hérités de l'ancien modèle sont jetés, et journalisés."""
        remote = f"origin/{ref}"
        code, _, _ = await run_git(
            ["rev-parse", "--verify", "--quiet", f"refs/heads/{ref}"], workdir, env
        )
        if code == 0:
            ahead = await self._count_ahead(workdir, ref, env)
            if ahead > 0:
                log.warning(
                    "legacy_local_commits_discarded", workdir=str(workdir), ref=ref, count=ahead
                )
        code, _, err = await run_git(["checkout", "-q", "-B", ref, remote], workdir, env)
        if code != 0:
            raise WorkdirError(_access_code(classify(err)))
        await run_git(["reset", "-q", "--hard", remote], workdir, env)

    @asynccontextmanager
    async def read_copy(
        self, space_id: uuid.UUID, slug: str, repo_url: str, username: str, token: str | None
    ) -> AsyncIterator[tuple[Path, dict[str, str], str]]:
        """Copie de lecture alignée sur la forge, sous son verrou : rend
        (répertoire, environnement git, sha de la tête publiée)."""
        ref = f"{WORK_PREFIX}{slug}"
        workdir = self._dir(space_id, slug)
        async with self._lock(str(workdir)):
            env = self._env(username, token)
            await self._clone_or_refresh(workdir, repo_url, env)
            if not await self._remote_branch_exists(workdir, ref, env):
                raise WorkdirError("branch_not_found")
            await self._checkout_branch(workdir, ref, env)
            head = await self._sha(workdir, "HEAD", env) or ""
            yield workdir, env, head

    @property
    def root(self) -> Path:
        return self._root

    def copy_dir(self, space_id: uuid.UUID, slug: str) -> Path:
        return self._dir(space_id, slug)

    def env_for(self, username: str, token: str | None) -> dict[str, str]:
        return self._env(username, token)

    async def tree_text(
        self, workdir: Path, sha: str, rel: str, env: dict[str, str]
    ) -> dict[str, str]:
        return await self._tree_text(workdir, sha, rel, env)

    async def _count_ahead(self, workdir: Path, ref: str, env: dict[str, str]) -> int:
        code, out, _ = await run_git(
            ["rev-list", "--count", f"origin/{ref}..{ref}"], workdir, env
        )
        if code != 0:
            return 0
        return int(out.strip() or "0")

    async def read_branch(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        read_fn: Callable[[Path], _T],
    ) -> _T:
        """Aligne la copie d'une branche de travail sur la forge puis laisse
        `read_fn` lire son arborescence. Ne modifie rien, ne commite pas : c'est
        le pendant en lecture de `commit_to_branch` (pour lister des fichiers
        versionnés, ici les manifestes de sources)."""
        ref = f"{WORK_PREFIX}{slug}"
        workdir = self._dir(space_id, slug)
        async with self._lock(str(workdir)):
            env = self._env(username, token)
            await self._clone_or_refresh(workdir, repo_url, env)
            if not await self._remote_branch_exists(workdir, ref, env):
                raise WorkdirError("branch_not_found")
            await self._checkout_branch(workdir, ref, env)
            return read_fn(workdir)

    async def _tree_text(
        self, workdir: Path, sha: str, rel: str, env: dict[str, str]
    ) -> dict[str, str]:
        """Texte des fichiers `.json` **directement** sous `rel` à un commit donné
        (nom de fichier → contenu). Sert à relire `raw/` au point de reprise."""
        code, out, _ = await run_git(
            ["ls-tree", "-r", "--name-only", sha, "--", rel], workdir, env
        )
        if code != 0:
            return {}
        prefix = f"{rel}/"
        files: dict[str, str] = {}
        for path in out.splitlines():
            path = path.strip()
            if not path.startswith(prefix):
                continue
            name = path[len(prefix):]
            if "/" in name or not name.endswith(".json"):
                continue
            code2, text, _ = await run_git(["show", f"{sha}:{path}"], workdir, env)
            if code2 == 0:
                files[name] = text
        return files

    async def review_inputs(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        raw_rel: str,
        base_sha_fn: Callable[[Path], str | None],
        tree_fn: Callable[[Path], _T],
    ) -> tuple[str, str | None, dict[str, str], _T]:
        """Entrées d'une revue, prises sur **une seule copie cohérente** : le sha
        de HEAD, le point de reprise (`base_sha_fn` lit `review.yaml` dans l'arbre),
        le texte de `raw/` au point de reprise (via git), et ce que `tree_fn` lit
        dans l'arbre de travail (brut et assets à HEAD). Lecture seule."""
        ref = f"{WORK_PREFIX}{slug}"
        workdir = self._dir(space_id, slug)
        async with self._lock(str(workdir)):
            env = self._env(username, token)
            await self._clone_or_refresh(workdir, repo_url, env)
            if not await self._remote_branch_exists(workdir, ref, env):
                raise WorkdirError("branch_not_found")
            await self._checkout_branch(workdir, ref, env)
            head_sha = await self._sha(workdir, "HEAD", env) or ""
            base_sha = base_sha_fn(workdir)
            raw_base = await self._tree_text(workdir, base_sha, raw_rel, env) if base_sha else {}
            tree = tree_fn(workdir)
            return head_sha, base_sha, raw_base, tree

    async def file_history(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        rel_path: str,
        limit: int = 50,
    ) -> list[UnpublishedCommit]:
        """Historique Git d'un fichier (du plus récent au plus ancien) : les
        versions précédentes d'un schéma `raw`. Lecture seule."""
        ref = f"{WORK_PREFIX}{slug}"
        workdir = self._dir(space_id, slug)
        async with self._lock(str(workdir)):
            env = self._env(username, token)
            await self._clone_or_refresh(workdir, repo_url, env)
            if not await self._remote_branch_exists(workdir, ref, env):
                raise WorkdirError("branch_not_found")
            await self._checkout_branch(workdir, ref, env)
            fmt = _UNIT_SEP.join(["%H", "%an", "%aI", "%s"])
            code, out, err = await run_git(
                ["log", f"--pretty=format:{fmt}", f"--max-count={int(limit)}", "--", rel_path],
                workdir, env,
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

    async def file_at(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        sha: str,
        rel_path: str,
    ) -> str | None:
        """Contenu d'un fichier à un commit donné (version précédente d'un schéma),
        ou None s'il n'existait pas. Lecture seule."""
        ref = f"{WORK_PREFIX}{slug}"
        workdir = self._dir(space_id, slug)
        async with self._lock(str(workdir)):
            env = self._env(username, token)
            await self._clone_or_refresh(workdir, repo_url, env)
            if not await self._remote_branch_exists(workdir, ref, env):
                raise WorkdirError("branch_not_found")
            await self._checkout_branch(workdir, ref, env)
            code, out, _ = await run_git(["show", f"{sha}:{rel_path}"], workdir, env)
            return out if code == 0 else None

    async def _count(self, workdir: Path, rev_range: str, env: dict[str, str]) -> int:
        code, out, _ = await run_git(["rev-list", "--count", rev_range], workdir, env)
        return int(out.strip() or "0") if code == 0 else 0

    async def divergence(
        self, space_id: uuid.UUID, slug: str, repo_url: str, username: str, token: str | None
    ) -> tuple[int, int]:
        """Rend (commits locaux non publiés, commits dont la forge a avancé) pour
        une branche de travail. Une collecte refuse de tourner quand les deux sont
        non nuls (base périmée)."""
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
                return (0, 0)
            ahead = await self._count(workdir, f"origin/{ref}..{ref}", env)
            behind = await self._count(workdir, f"{ref}..origin/{ref}", env)
            return (ahead, behind)

    async def _remote_version_numbers(self, workdir: Path, env: dict[str, str]) -> list[int]:
        # La forge fait foi : les numéros sont uniques pour tout le dépôt.
        code, out, err = await run_git(
            ["ls-remote", "--tags", "origin", _VERSION_TAGS], workdir, env
        )
        if code != 0:
            raise WorkdirError(_access_code(classify(err)))
        numbers: set[int] = set()
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) != 2:
                continue
            ref = parts[1].removeprefix("refs/tags/").removesuffix("^{}")
            match = _VERSION_RE.match(ref)
            if match:
                numbers.add(int(match.group(1)))
        return sorted(numbers)

    async def _reachable_versions(
        self, workdir: Path, head: str, env: dict[str, str]
    ) -> list[Version]:
        # Synchronise les tags de version puis ne garde que les tags annotés bien
        # formés atteignables depuis la tête (la lignée de la branche).
        await run_git(["fetch", "-q", "origin", _VERSION_FETCH_REFSPEC], workdir, env)
        fmt = _UNIT_SEP.join(
            [
                "%(refname:strip=2)",
                "%(*objectname)",
                "%(taggername)",
                "%(taggerdate:iso-strict)",
                "%(contents:subject)",
            ]
        )
        _, out, _ = await run_git(
            ["for-each-ref", f"--format={fmt}", f"refs/tags/{_VERSION_PREFIX}*"], workdir, env
        )
        versions: list[Version] = []
        for line in out.splitlines():
            if not line.strip():
                continue
            fields = line.split(_UNIT_SEP)
            if len(fields) != 5:
                continue
            name, commit, tagger, date, subject = fields
            match = _VERSION_RE.match(name)
            # Un tag léger (non annoté) n'a pas de commit déréférencé : ignoré.
            if not match or not commit:
                continue
            reach, _, _ = await run_git(
                ["merge-base", "--is-ancestor", commit, head], workdir, env
            )
            if reach != 0:
                continue
            versions.append(Version(int(match.group(1)), subject, commit, tagger, date))
        versions.sort(key=lambda v: v.number, reverse=True)
        return versions

    async def list_versions(
        self, space_id: uuid.UUID, slug: str, repo_url: str, username: str, token: str | None
    ) -> list[Version]:
        ref = f"{WORK_PREFIX}{slug}"
        workdir = self._dir(space_id, slug)
        async with self._lock(str(workdir)):
            env = self._env(username, token)
            await self._clone_or_refresh(workdir, repo_url, env)
            if not await self._remote_branch_exists(workdir, ref, env):
                raise WorkdirError("branch_not_found")
            code, head, err = await run_git(
                ["rev-parse", f"refs/remotes/origin/{ref}"], workdir, env
            )
            if code != 0:
                raise WorkdirError(_access_code(classify(err)))
            return await self._reachable_versions(workdir, head.strip(), env)

    async def tag_version(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        label: str,
        author_name: str,
        author_email: str,
    ) -> VersionCreated:
        """Fige la tête publiée de la branche en un tag annoté « version/<n> ».
        Refuse si des commits locaux ne sont pas publiés, si rien n'a changé
        depuis la dernière version, ou si le numéro a été pris entre-temps."""
        ref = f"{WORK_PREFIX}{slug}"
        workdir = self._dir(space_id, slug)
        async with self._lock(str(workdir)):
            env = self._env(username, token)
            await self._clone_or_refresh(workdir, repo_url, env)
            if not await self._remote_branch_exists(workdir, ref, env):
                raise WorkdirError("branch_not_found")
            # Une version fige ce que tout le monde voit : pas de commit en attente.
            code, _, _ = await run_git(
                ["rev-parse", "--verify", "--quiet", f"refs/heads/{ref}"], workdir, env
            )
            if code == 0:
                await run_git(["checkout", "-q", ref], workdir, env)
                if await self._count_ahead(workdir, ref, env) > 0:
                    raise WorkdirError("publish_required")
            code, head_out, err = await run_git(
                ["rev-parse", f"refs/remotes/origin/{ref}"], workdir, env
            )
            if code != 0:
                raise WorkdirError(_access_code(classify(err)))
            head = head_out.strip()
            reachable = await self._reachable_versions(workdir, head, env)
            if reachable:
                latest = reachable[0]
                _, latest_tree, _ = await run_git(
                    ["rev-parse", f"{latest.commit}^{{tree}}"], workdir, env
                )
                _, head_tree, _ = await run_git(["rev-parse", f"{head}^{{tree}}"], workdir, env)
                if latest_tree.strip() == head_tree.strip():
                    if latest.commit == head and latest.label == label:
                        # Rejeu : même tête, même libellé → sans effet, signalé.
                        return VersionCreated(latest.number, head, already_exists=True)
                    raise WorkdirError("nothing_to_version")
            numbers = await self._remote_version_numbers(workdir, env)
            next_n = (max(numbers) + 1) if numbers else 1
            tag = f"{_VERSION_PREFIX}{next_n}"
            message = f"{label}\n\nBranche : {ref}\n"
            tag_env = {
                **env,
                "GIT_COMMITTER_NAME": author_name,
                "GIT_COMMITTER_EMAIL": author_email,
            }
            # Enlève un éventuel tag local périmé du même nom : la forge tranche.
            await run_git(["tag", "-d", tag], workdir, env)
            code, _, err = await run_git(["tag", "-a", tag, "-m", message, head], workdir, tag_env)
            if code != 0:
                raise WorkdirError(_access_code(classify(err)))
            # Push sans force : si le numéro a été pris entre-temps, refus atomique.
            code, _, err = await run_git(["push", "-q", "origin", f"refs/tags/{tag}"], workdir, env)
            if code != 0:
                await run_git(["tag", "-d", tag], workdir, env)
                low = err.lower()
                if "already exists" in low or "[rejected]" in low or "exists" in low:
                    raise WorkdirError("version_taken")
                raise WorkdirError(_access_code(classify(err)))
            log.info(
                "version_tagged",
                space_id=str(space_id),
                ref=ref,
                number=next_n,
                author=author_name,
            )
            return VersionCreated(next_n, head, already_exists=False)

    async def _sha(self, workdir: Path, ref: str, env: dict[str, str]) -> str | None:
        code, out, _ = await run_git(["rev-parse", "--verify", "--quiet", ref], workdir, env)
        return out.strip() if code == 0 and out.strip() else None

    async def railmap(
        self,
        space_id: uuid.UUID,
        repo_url: str,
        username: str,
        token: str | None,
        branches: list[tuple[str, str]],
        viewed_slug: str | None,
        *,
        offset: int = 0,
        limit: int = 200,
    ) -> RailMap:
        """Rassemble les données git (commits, têtes, tags de version, commits
        non publiés de la branche regardée) et délègue le placement au module
        pur `railmap`. `branches` : (slug, kind) des branches de l'espace, dans
        un ordre stable."""
        # La branche regardée fournit ses commits locaux non publiés ; sinon un
        # répertoire dédié à l'espace suffit.
        key = viewed_slug or "_map"
        workdir = self._dir(space_id, key)
        async with self._lock(str(workdir)):
            env = self._env(username, token)
            await self._clone_or_refresh(workdir, repo_url, env)
            await run_git(["fetch", "-q", "origin", _VERSION_FETCH_REFSPEC], workdir, env)
            default = await self._default_branch(workdir, env)

            root_sha = await self._sha(workdir, f"refs/remotes/origin/{default}", env)
            branch_inputs: list[BranchInput] = []
            if root_sha is not None:
                branch_inputs.append(
                    BranchInput(f"origin/{default}", default, "", "root", root_sha)
                )
            tips: list[str] = [root_sha] if root_sha else []
            unpublished: list[str] = []
            for slug, kind in branches:
                ref = f"{WORK_PREFIX}{slug}"
                origin_sha = await self._sha(workdir, f"refs/remotes/origin/{ref}", env)
                tip = origin_sha
                if slug == viewed_slug:
                    local_sha = await self._sha(workdir, f"refs/heads/{ref}", env)
                    if local_sha is not None:
                        tip = local_sha
                        if origin_sha is not None and origin_sha != local_sha:
                            _, out, _ = await run_git(
                                ["rev-list", f"origin/{ref}..{ref}"], workdir, env
                            )
                            unpublished = [line for line in out.split() if line]
                if tip is None:
                    # Branche supprimée sur la forge : pas de ligne vivante.
                    continue
                branch_inputs.append(BranchInput(ref, slug, slug, kind, tip))
                tips.append(tip)

            commits = await self._railmap_commits(workdir, tips, env)
            versions = self._railmap_versions(
                await self._railmap_tag_lines(workdir, env), {c.sha for c in commits}
            )
            return build_railmap(
                commits, branch_inputs, versions, unpublished, offset=offset, limit=limit
            )

    async def _railmap_commits(
        self, workdir: Path, tips: list[str], env: dict[str, str]
    ) -> list[CommitInput]:
        unique = list(dict.fromkeys(tips))
        if not unique:
            return []
        fmt = _UNIT_SEP.join(["%H", "%P", "%an", "%aI", "%ct", "%s"])
        code, out, err = await run_git(
            ["log", "--topo-order", "-n", str(_RAILMAP_CAP), f"--format={fmt}", *unique],
            workdir,
            env,
        )
        if code != 0:
            raise WorkdirError(_access_code(classify(err)))
        commits: list[CommitInput] = []
        for line in out.splitlines():
            if not line:
                continue
            fields = line.split(_UNIT_SEP)
            if len(fields) != 6:
                continue
            sha, parents, author, date, ts, subject = fields
            commits.append(
                CommitInput(
                    sha=sha,
                    parents=tuple(parents.split()),
                    author=author,
                    date=date,
                    timestamp=int(ts) if ts.isdigit() else 0,
                    subject=subject,
                )
            )
        return commits

    async def _railmap_tag_lines(self, workdir: Path, env: dict[str, str]) -> str:
        fmt = _UNIT_SEP.join(
            ["%(refname:strip=2)", "%(*objectname)", "%(taggername)", "%(contents:subject)"]
        )
        _, out, _ = await run_git(
            ["for-each-ref", f"--format={fmt}", f"refs/tags/{_VERSION_PREFIX}*"], workdir, env
        )
        return out

    def _railmap_versions(self, tag_lines: str, known: set[str]) -> list[VersionAt]:
        versions: list[VersionAt] = []
        for line in tag_lines.splitlines():
            if not line.strip():
                continue
            fields = line.split(_UNIT_SEP)
            if len(fields) != 4:
                continue
            name, commit, tagger, subject = fields
            match = _VERSION_RE.match(name)
            if not match or not commit or commit not in known:
                continue
            versions.append(VersionAt(commit, int(match.group(1)), subject, tagger))
        return versions

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
