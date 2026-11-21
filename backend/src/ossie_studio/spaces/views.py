"""Vue éphémère d'un brouillon : la branche publiée avec le brouillon par-dessus.

Décision d'architecture (plan de l'epic « Brouillon personnel ») : les lectures
et écritures existantes travaillent sur un répertoire (`read_fn(Path)`,
`write_fn(Path)`, parseurs qui lisent des fichiers). Pour un utilisateur qui a un
brouillon, on matérialise donc un `git worktree` **détaché** posé sur la copie de
lecture (historique partagé : seul le checkout coûte), où le brouillon est
appliqué. La vue n'est **jamais une source de vérité** : elle se reconstruit à
l'identique depuis la base. Elle est supprimée après inactivité et à la
publication, si bien que l'espace occupé est borné par le nombre d'utilisateurs
**en train de travailler**, pas par le nombre de brouillons.
"""

from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import structlog

from ossie_studio.drafts.store import DraftChange, DraftObject
from ossie_studio.spaces.gitcli import run_git
from ossie_studio.spaces.workdir import WorkdirError, WorkdirManager

log = structlog.get_logger(__name__)

_T = TypeVar("_T")

VIEWS_SUFFIX = ".views"
DEFAULT_MAX_IDLE_SECONDS = 1800


@dataclass(frozen=True)
class DraftState:
    """Ce que la vue doit refléter : identité et révision du brouillon (None =
    pas de brouillon, la vue égale la tête publiée) et ses objets."""

    draft_id: uuid.UUID | None
    revision: int
    objects: list[DraftObject]

    def stamp(self, head: str) -> str:
        return f"{head} {self.draft_id or '-'} {self.revision}"


@dataclass(frozen=True)
class ViewContext:
    """Remis à la fonction exécutée dans la vue."""

    path: Path
    env: dict[str, str]
    head: str


class DraftViewManager:
    """Vues éphémères, une par (espace, branche, utilisateur), sous un verrou par
    vue : deux gestes du même utilisateur sur la même branche se sérialisent."""

    def __init__(self, workdir: WorkdirManager) -> None:
        self._workdir = workdir
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    def _view_dir(self, space_id: uuid.UUID, slug: str, user_id: uuid.UUID) -> Path:
        copy = self._workdir.copy_dir(space_id, slug)
        return copy.parent / f"{copy.name}{VIEWS_SUFFIX}" / str(user_id)

    @staticmethod
    def _stamp_file(view: Path) -> Path:
        return view.parent / f"{view.name}.stamp"

    async def run(
        self,
        *,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        user_id: uuid.UUID,
        load: Callable[[], Awaitable[DraftState]],
        fn: Callable[[ViewContext], Awaitable[tuple[_T, DraftState | None]]],
    ) -> _T:
        """Prépare la vue de l'utilisateur (alignée sur la tête publiée, brouillon
        appliqué), puis exécute `fn` dedans. `load` est appelé **sous le verrou de
        la vue** (pas de mise à jour perdue entre deux onglets). `fn` rend son
        résultat et, s'il a modifié le brouillon, le nouvel état (la vue le
        reflète alors déjà : seul le témoin est mis à jour)."""
        view = self._view_dir(space_id, slug, user_id)
        stamp = self._stamp_file(view)
        async with self._lock(str(view)):
            state = await load()
            async with self._workdir.read_copy(space_id, slug, repo_url, username, token) as (
                copy,
                env,
                head,
            ):
                await self._ensure_worktree(copy, view, head, env)
                current = stamp.read_text(encoding="utf-8") if stamp.exists() else ""
                if current != state.stamp(head):
                    await self._reset_to(view, head, env)
                    _apply(view, state.objects)
                    stamp.write_text(state.stamp(head), encoding="utf-8")
            result, new_state = await fn(ViewContext(path=view, env=env, head=head))
            if new_state is not None:
                stamp.write_text(new_state.stamp(head), encoding="utf-8")
            else:
                stamp.touch()
            return result

    async def _ensure_worktree(
        self, copy: Path, view: Path, head: str, env: dict[str, str]
    ) -> None:
        if not _prepare_view_dir(view):
            return
        await run_git(["worktree", "prune"], copy, env)
        code, _, err = await run_git(
            ["worktree", "add", "-q", "--detach", str(view), head], copy, env
        )
        if code != 0:
            log.warning("draft_view_create_failed", view=str(view), err=err[:200])
            raise WorkdirError("git_failed")
        self._stamp_file(view).unlink(missing_ok=True)

    @staticmethod
    async def _reset_to(view: Path, head: str, env: dict[str, str]) -> None:
        code, _, _ = await run_git(["checkout", "-q", "--detach", "-f", head], view, env)
        if code != 0:
            raise WorkdirError("git_failed")
        await run_git(["clean", "-q", "-fdx"], view, env)

    async def discard(self, space_id: uuid.UUID, slug: str, user_id: uuid.UUID) -> None:
        """Supprime la vue (publication, abandon du brouillon)."""
        view = self._view_dir(space_id, slug, user_id)
        async with self._lock(str(view)):
            await self._remove(view)

    async def _remove(self, view: Path) -> None:
        copy = view.parent.parent / view.parent.name.removesuffix(VIEWS_SUFFIX)
        if (copy / ".git").exists():
            await run_git(["worktree", "remove", "--force", str(view)], copy, {})
            await run_git(["worktree", "prune"], copy, {})
        shutil.rmtree(view, ignore_errors=True)
        self._stamp_file(view).unlink(missing_ok=True)

    async def purge_idle(self, max_idle_seconds: int = DEFAULT_MAX_IDLE_SECONDS) -> int:
        """Supprime les vues inutilisées depuis `max_idle_seconds` ; rend leur nombre."""
        root = self._workdir.root
        now = time.time()
        removed = 0
        for stamp in root.glob(f"*/*{VIEWS_SUFFIX}/*.stamp"):
            if now - stamp.stat().st_mtime < max_idle_seconds:
                continue
            view = stamp.parent / stamp.name.removesuffix(".stamp")
            lock = self._lock(str(view))
            if lock.locked():
                continue
            async with lock:
                await self._remove(view)
            removed += 1
        if removed:
            log.info("draft_views_purged", count=removed)
        return removed


def _prepare_view_dir(view: Path) -> bool:
    """True s'il faut créer le worktree (absent) ; nettoie un reste éventuel."""
    if (view / ".git").exists():
        return False
    if view.exists():
        shutil.rmtree(view, ignore_errors=True)
    view.parent.mkdir(parents=True, exist_ok=True)
    return True


def _apply(view: Path, objects: list[DraftObject]) -> None:
    for obj in objects:
        target = view / obj.path
        if obj.content is None:
            target.unlink(missing_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(obj.content)


async def capture_changes(ctx: ViewContext) -> list[DraftChange]:
    """Tous les objets de la vue qui diffèrent de la tête publiée, avec le blob
    du même chemin à la tête (None s'il n'y existe pas). C'est l'état complet du
    brouillon après une écriture."""
    await run_git(["add", "-A"], ctx.path, ctx.env)
    code, out, _ = await run_git(
        ["diff", "--cached", "--name-status", "--no-renames", "-z", "HEAD"], ctx.path, ctx.env
    )
    if code != 0:
        raise WorkdirError("git_failed")
    fields = [f for f in out.split("\0") if f]
    entries = list(zip(fields[0::2], fields[1::2], strict=False))
    if not entries:
        return []
    code, tree, _ = await run_git(["ls-tree", "-r", "-z", "HEAD"], ctx.path, ctx.env)
    blobs: dict[str, str] = {}
    for line in tree.split("\0"):
        if not line:
            continue
        meta, path = line.split("\t", 1)
        blobs[path] = meta.split()[2]
    changes: list[DraftChange] = []
    for status, path in entries:
        content = None if status.startswith("D") else (ctx.path / path).read_bytes()
        changes.append(DraftChange(path=path, current_blob=blobs.get(path), content=content))
    return changes
