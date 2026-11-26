from __future__ import annotations

import asyncio
import re
import time
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
class PathCommit:
    """Commit publié qui touche un répertoire, avec les fichiers changés
    (`A` ajouté, `M` modifié, `D` supprimé, `R…` renommé → nouveau chemin)."""

    sha: str
    author: str
    date: str
    message: str
    files: tuple[tuple[str, str], ...]


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


@dataclass(frozen=True)
class PublishObject:
    """Un objet à publier : chemin, empreinte de sa base (None = créé), contenu
    (None = supprimé)."""

    path: str
    base_blob: str | None
    content: bytes | None


def _write_objects(root: Path, objects: list[PublishObject]) -> None:
    for obj in objects:
        target = root / obj.path
        if obj.content is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(obj.content)


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

    # Délai minimal entre deux fetchs d'une même copie pour la vérification de
    # tête des écrans : dix écrans ouverts ne fetchent pas dix fois la forge.
    HEAD_FETCH_MIN_SECONDS = 10.0

    def __init__(self, root: Path, read_fresh_seconds: float = 0.0) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self._root = root
        self._head_cache: dict[str, tuple[float, str]] = {}
        # Lectures : une copie alignée sur la forge il y a moins de
        # `read_fresh_seconds` n'est pas réalignée (ticket 2daae568). Sans cela,
        # chaque lecture refaisait fetch + ls-remote sous le verrou, et les 5 à 6
        # lectures d'une page se sérialisaient derrière autant d'allers-retours
        # vers la forge (pages de 5 à 10 s). 0 : réalignement à chaque lecture.
        self._read_fresh_seconds = read_fresh_seconds
        self._aligned: dict[str, float] = {}
        self._clock: Callable[[], float] = time.monotonic
        self._askpass = write_askpass(root)
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    def _dir(self, space_id: uuid.UUID, slug: str) -> Path:
        # Une référence git peut contenir « / » (work/data) : un seul niveau de
        # répertoire par branche, « ~ » étant interdit dans un nom de branche.
        return self._root / str(space_id) / slug.replace("/", "~")

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

    async def _align_for_read(
        self, workdir: Path, repo_url: str, ref: str, env: dict[str, str], *, force: bool = False
    ) -> None:
        """Copie de lecture alignée sur la tête distante de `ref` (sous le verrou
        de la copie, tenu par l'appelant). Déjà alignée il y a moins de
        `read_fresh_seconds` : rien à refaire — les lectures qui attendaient le
        verrou profitent de l'alignement que la première vient de faire. Un
        changement poussé hors de l'application apparaît donc avec ce délai au
        plus (même compromis que `published_head`)."""
        key = str(workdir)
        at = self._aligned.get(key)
        if (
            not force
            and at is not None
            and self._clock() - at < self._read_fresh_seconds
            and (workdir / ".git").exists()
        ):
            return
        self._aligned.pop(key, None)
        await self._clone_or_refresh(workdir, repo_url, env)
        if not await self._remote_branch_exists(workdir, ref, env):
            raise WorkdirError("branch_not_found")
        await self._checkout_branch(workdir, ref, env)
        self._aligned[key] = self._clock()

    def _forget_aligned(self, workdir: Path) -> None:
        """Écriture, poussée ou fetch hors lecture dans cette copie : la lecture
        suivante se réaligne sur la forge (elle voit ce qui vient d'être publié)."""
        self._aligned.pop(str(workdir), None)

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

    async def remote_branches(
        self, space_id: uuid.UUID, repo_url: str, username: str, token: str | None
    ) -> tuple[list[str], str]:
        """Branches présentes sur la forge (triées) et la racine (branche par
        défaut), pour choisir la branche de base parmi l'existant."""
        workdir = self._dir(space_id, "_map")
        async with self._lock(str(workdir)):
            env = self._env(username, token)
            await self._clone_or_refresh(workdir, repo_url, env)
            code, out, err = await run_git(["ls-remote", "--heads", "origin"], workdir, env)
            if code != 0:
                raise WorkdirError(_access_code(classify(err)))
            heads = sorted(
                line.split("\t")[1].removeprefix("refs/heads/") for line in out.splitlines()
            )
            return heads, (default_branch(heads) if heads else "")

    async def create_base_branch(
        self, space_id: uuid.UUID, slug: str, repo_url: str, username: str, token: str | None
    ) -> None:
        ref = slug  # la clé de branche EST sa référence git (space_branches.git_ref)
        workdir = self._dir(space_id, slug)
        async with self._lock(str(workdir)):
            self._forget_aligned(workdir)
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
        base_ref = base_slug
        child_ref = child_slug
        workdir = self._dir(space_id, child_slug)
        async with self._lock(str(workdir)):
            self._forget_aligned(workdir)
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
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        *,
        force: bool = False,
    ) -> AsyncIterator[tuple[Path, dict[str, str], str]]:
        """Copie de lecture alignée sur la forge, sous son verrou : rend
        (répertoire, environnement git, sha de la tête publiée). `force` :
        réaligne même si la copie vient de l'être."""
        ref = slug  # la clé de branche EST sa référence git (space_branches.git_ref)
        workdir = self._dir(space_id, slug)
        async with self._lock(str(workdir)):
            env = self._env(username, token)
            await self._align_for_read(workdir, repo_url, ref, env, force=force)
            head = await self._sha(workdir, "HEAD", env) or ""
            yield workdir, env, head

    async def published_head(
        self, space_id: uuid.UUID, slug: str, repo_url: str, username: str, token: str | None
    ) -> str:
        """Sha de la tête publiée, pour que les écrans se rafraîchissent quand elle
        bouge. Au plus un fetch toutes les HEAD_FETCH_MIN_SECONDS par copie ;
        entre deux, la valeur en cache."""
        key = str(self._dir(space_id, slug))
        cached = self._head_cache.get(key)
        now = self._clock()
        if cached is not None and now - cached[0] < self.HEAD_FETCH_MIN_SECONDS:
            return cached[1]
        # La vérification de tête est faite pour voir la forge bouger : elle
        # réaligne toujours (déjà limitée à une fois par HEAD_FETCH_MIN_SECONDS).
        async with self.read_copy(space_id, slug, repo_url, username, token, force=True) as (
            _c,
            _e,
            head,
        ):
            self._head_cache[key] = (now, head)
            return head

    @property
    def root(self) -> Path:
        return self._root

    def copy_dir(self, space_id: uuid.UUID, slug: str) -> Path:
        return self._dir(space_id, slug)

    def env_for(self, username: str, token: str | None) -> dict[str, str]:
        return self._env(username, token)

    async def push_objects(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        read_username: str,
        read_token: str | None,
        push_username: str,
        push_token: str,
        objects: list[PublishObject],
        author_name: str,
        author_email: str,
        message: str,
        max_attempts: int = 3,
    ) -> str:
        """Publie des objets en UN commit sur la tête de la branche, poussé avec
        le jeton de la personne (jamais celui de l'espace), sans force. Le commit
        se construit dans un worktree temporaire, supprimé quoi qu'il arrive.
        Push refusé (la branche a bougé hors de l'application) : si aucun des
        objets publiés n'a bougé (empreintes de base), on reconstruit sur la
        nouvelle tête et on repousse ; sinon `branch_changed_conflict`.
        Rend le sha du commit publié."""
        ref = slug  # la clé de branche EST sa référence git (space_branches.git_ref)
        copy = self._dir(space_id, slug)
        async with self._lock(str(copy)):
            self._forget_aligned(copy)
            read_env = self._env(read_username, read_token)
            push_env = {
                **self._env(push_username, push_token),
                "GIT_AUTHOR_NAME": author_name,
                "GIT_AUTHOR_EMAIL": author_email,
                "GIT_COMMITTER_NAME": author_name,
                "GIT_COMMITTER_EMAIL": author_email,
            }
            for attempt in range(1, max_attempts + 1):
                await self._clone_or_refresh(copy, repo_url, read_env)
                if not await self._remote_branch_exists(copy, ref, read_env):
                    raise WorkdirError("branch_not_found")
                head = await self._sha(copy, f"refs/remotes/origin/{ref}", read_env) or ""
                if await self._objects_moved(copy, head, objects, read_env):
                    raise WorkdirError("branch_changed_conflict")
                sha, pushed, err = await self._commit_and_push(
                    copy, head, ref, objects, message, push_env
                )
                if pushed:
                    await self._checkout_branch(copy, ref, read_env)
                    log.info("draft_published", space_id=str(space_id), ref=ref, attempt=attempt)
                    return sha
                low = err.lower()
                if "non-fast-forward" not in low and "fetch first" not in low:
                    cause = classify(err)
                    if cause in ("auth_failed", "write_denied", "branch_protected"):
                        # Le jeton de la personne est refusé (révoqué, sans droit) :
                        # brouillon intact, à corriger dans « Saisir le secret ».
                        raise WorkdirError("publish_credential_rejected")
                    raise WorkdirError(_access_code(cause))
                log.info("publish_retry_after_remote_move", ref=ref, attempt=attempt)
            raise WorkdirError("branch_changed_conflict")

    async def _objects_moved(
        self, copy: Path, head: str, objects: list[PublishObject], env: dict[str, str]
    ) -> bool:
        paths = [o.path for o in objects]
        code, out, _ = await run_git(["ls-tree", "-r", "-z", head, "--", *paths], copy, env)
        current: dict[str, str] = {}
        for line in out.split("\0") if code == 0 else []:
            if line:
                meta, path = line.split("\t", 1)
                current[path] = meta.split()[2]
        return any(current.get(o.path) != o.base_blob for o in objects)

    async def _commit_and_push(
        self,
        copy: Path,
        head: str,
        ref: str,
        objects: list[PublishObject],
        message: str,
        env: dict[str, str],
    ) -> tuple[str, bool, str]:
        tmp = copy.parent / f"{copy.name}.publish-{uuid.uuid4().hex[:8]}"
        await run_git(["worktree", "prune"], copy, env)
        code, _, err = await run_git(
            ["worktree", "add", "-q", "--detach", str(tmp), head], copy, env
        )
        if code != 0:
            raise WorkdirError("git_failed")
        try:
            _write_objects(tmp, objects)
            await run_git(["add", "-A"], tmp, env)
            code, _, err = await run_git(["commit", "-q", "-m", message], tmp, env)
            if code != 0:
                raise WorkdirError("git_failed")
            sha = await self._sha(tmp, "HEAD", env) or ""
            code, _, err = await run_git(
                ["push", "-q", "origin", f"HEAD:refs/heads/{ref}"], tmp, env
            )
            return sha, code == 0, err
        finally:
            await run_git(["worktree", "remove", "--force", str(tmp)], copy, env)
            await run_git(["worktree", "prune"], copy, env)

    async def _count_ahead(self, workdir: Path, ref: str, env: dict[str, str]) -> int:
        code, out, _ = await run_git(["rev-list", "--count", f"origin/{ref}..{ref}"], workdir, env)
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
        ref = slug  # la clé de branche EST sa référence git (space_branches.git_ref)
        workdir = self._dir(space_id, slug)
        async with self._lock(str(workdir)):
            env = self._env(username, token)
            await self._align_for_read(workdir, repo_url, ref, env)
            return read_fn(workdir)

    async def review_inputs(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        tree_fn: Callable[[Path], _T],
    ) -> tuple[str, _T]:
        """Entrées d'une revue sur la vue publiée, prises sur **une seule copie
        cohérente** : le sha de HEAD et ce que `tree_fn` lit dans l'arbre (brut et
        assets). Sans brouillon, le brut publié EST le brut lu : rien d'autre à
        relire (ticket docflow aad1d3c6, plus de point de reprise). Lecture seule."""
        ref = slug  # la clé de branche EST sa référence git (space_branches.git_ref)
        workdir = self._dir(space_id, slug)
        async with self._lock(str(workdir)):
            env = self._env(username, token)
            await self._align_for_read(workdir, repo_url, ref, env)
            head_sha = await self._sha(workdir, "HEAD", env) or ""
            return head_sha, tree_fn(workdir)

    async def file_history(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        rel_path: str | list[str],
        limit: int = 50,
    ) -> list[UnpublishedCommit]:
        """Historique Git d'un fichier (du plus récent au plus ancien) : les
        versions précédentes d'un schéma `raw`. Plusieurs chemins : les commits
        qui touchent l'un d'eux (un asset et son `.extend.yaml`). Lecture seule."""
        paths = [rel_path] if isinstance(rel_path, str) else list(rel_path)
        ref = slug  # la clé de branche EST sa référence git (space_branches.git_ref)
        workdir = self._dir(space_id, slug)
        async with self._lock(str(workdir)):
            env = self._env(username, token)
            await self._align_for_read(workdir, repo_url, ref, env)
            fmt = _UNIT_SEP.join(["%H", "%an", "%aI", "%s"])
            code, out, err = await run_git(
                ["log", f"--pretty=format:{fmt}", f"--max-count={int(limit)}", "--", *paths],
                workdir,
                env,
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

    async def path_log(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        rel_path: str,
        limit: int = 200,
    ) -> list[PathCommit]:
        """Commits publiés (du plus récent au plus ancien) qui touchent
        `rel_path`, avec leurs fichiers changés : l'historique de la gouvernance
        pour la page Historique (ticket cf4434da). Lecture seule."""
        ref = slug
        workdir = self._dir(space_id, slug)
        async with self._lock(str(workdir)):
            env = self._env(username, token)
            await self._align_for_read(workdir, repo_url, ref, env)
            fmt = "\x1e" + _UNIT_SEP.join(["%H", "%an", "%aI", "%s"])
            code, out, err = await run_git(
                [
                    "log",
                    f"--pretty=format:{fmt}",
                    "--name-status",
                    f"--max-count={int(limit)}",
                    "--",
                    rel_path,
                ],
                workdir,
                env,
            )
            if code != 0:
                raise WorkdirError(_access_code(classify(err)))
            commits: list[PathCommit] = []
            for block in out.split("\x1e"):
                lines = [ln for ln in block.splitlines() if ln.strip()]
                if not lines:
                    continue
                sha, author, date, message = lines[0].split(_UNIT_SEP, 3)
                files: list[tuple[str, str]] = []
                for line in lines[1:]:
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        files.append((parts[0][:1], parts[-1]))
                commits.append(PathCommit(sha, author, date, message, tuple(files)))
            return commits

    async def local_file_at(
        self, space_id: uuid.UUID, slug: str, sha: str, rel_path: str
    ) -> str | None:
        """Contenu d'un fichier à un commit déjà présent dans la copie, **sans**
        fetch ni verrou : une lecture d'objet git est sûre pendant qu'un autre
        geste écrit la copie. Pour les lectures à chaque clic (aperçu de
        l'explorateur raw) ; le commit vient de `published_head`, qui l'a récupéré.
        None si la copie, le commit ou le fichier n'existe pas."""
        workdir = self._dir(space_id, slug)
        if not (workdir / ".git").exists():
            return None
        code, out, _ = await run_git(
            ["cat-file", "blob", f"{sha}:{rel_path}"], workdir, self._env("", None)
        )
        return out if code == 0 else None

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
        ref = slug  # la clé de branche EST sa référence git (space_branches.git_ref)
        workdir = self._dir(space_id, slug)
        async with self._lock(str(workdir)):
            env = self._env(username, token)
            await self._align_for_read(workdir, repo_url, ref, env)
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
        ref = slug  # la clé de branche EST sa référence git (space_branches.git_ref)
        workdir = self._dir(space_id, slug)
        async with self._lock(str(workdir)):
            self._forget_aligned(workdir)
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
            reach, _, _ = await run_git(["merge-base", "--is-ancestor", commit, head], workdir, env)
            if reach != 0:
                continue
            versions.append(Version(int(match.group(1)), subject, commit, tagger, date))
        versions.sort(key=lambda v: v.number, reverse=True)
        return versions

    async def list_versions(
        self, space_id: uuid.UUID, slug: str, repo_url: str, username: str, token: str | None
    ) -> list[Version]:
        ref = slug  # la clé de branche EST sa référence git (space_branches.git_ref)
        workdir = self._dir(space_id, slug)
        async with self._lock(str(workdir)):
            self._forget_aligned(workdir)
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
        ref = slug  # la clé de branche EST sa référence git (space_branches.git_ref)
        workdir = self._dir(space_id, slug)
        async with self._lock(str(workdir)):
            self._forget_aligned(workdir)
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
        branches: list[tuple[str, str, str]],
        viewed_slug: str | None,
        *,
        offset: int = 0,
        limit: int = 200,
    ) -> RailMap:
        """Rassemble les données git (commits, têtes, tags de version, commits
        non publiés de la branche regardée) et délègue le placement au module
        pur `railmap`. `branches` : (référence git, slug, kind) des branches de
        l'espace, dans un ordre stable ; `viewed_slug` : référence de la regardée."""
        # La branche regardée fournit ses commits locaux non publiés ; sinon un
        # répertoire dédié à l'espace suffit.
        key = viewed_slug or "_map"
        workdir = self._dir(space_id, key)
        async with self._lock(str(workdir)):
            self._forget_aligned(workdir)
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
            for ref, slug, kind in branches:
                origin_sha = await self._sha(workdir, f"refs/remotes/origin/{ref}", env)
                tip = origin_sha
                if ref == viewed_slug:
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
