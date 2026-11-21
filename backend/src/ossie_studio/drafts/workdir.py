"""Copie de travail **vue par un utilisateur** : la branche publiée, avec son
brouillon par-dessus. Façade aux mêmes signatures que l'ancien
`WorkdirManager.read_branch` / `commit_to_branch`, pour que les services
existants (sources, curation, collecte) n'aient rien d'autre à changer que le
type de l'objet reçu : toute écriture va désormais dans le brouillon de la
personne, jamais dans un commit."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import asyncpg
import structlog

from ossie_studio.drafts.store import (
    DraftObject,
    DraftObjectTooLargeError,
    DraftPathError,
    db_get_draft,
    db_list_objects,
    db_replace_draft,
)
from ossie_studio.spaces.gitcli import run_git
from ossie_studio.spaces.views import (
    DraftState,
    DraftViewManager,
    ViewContext,
    capture_changes,
)
from ossie_studio.spaces.workdir import (
    _COMMIT_PREFIX,
    _VERSION_FETCH_REFSPEC,
    _VERSION_PREFIX,
    UnpublishedCommit,
    VersionRestored,
    WorkdirError,
    WorkdirManager,
)

log = structlog.get_logger(__name__)

_T = TypeVar("_T")


@dataclass(frozen=True)
class ObjectVersions:
    current_blob: str | None
    theirs: bytes | None
    base: bytes | None


async def _blob_bytes(copy: Path, env: dict[str, str], blob: str | None) -> bytes | None:
    if blob is None:
        return None
    proc = await asyncio.create_subprocess_exec(
        "git",
        "cat-file",
        "blob",
        blob,
        cwd=str(copy),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    return out if proc.returncode == 0 else None


class DraftWorkdir:
    """Lectures et écritures d'**un** utilisateur sur **une** branche."""

    def __init__(
        self,
        manager: WorkdirManager,
        views: DraftViewManager,
        pool: asyncpg.Pool,
        user_id: uuid.UUID,
        branch_id: uuid.UUID,
        *,
        published_only: bool = False,
        conn: asyncpg.Connection | None = None,
    ) -> None:
        self._manager = manager
        self._views = views
        self._pool = pool
        self.user_id = user_id
        self.branch_id = branch_id
        # Vue « publiée » demandée par l'écran (?view=published) : les lectures
        # ignorent le brouillon. Les écritures, elles, vont toujours au brouillon.
        self.published_only = published_only
        # Connexion de la requête, réutilisée : en prendre une seconde au pool
        # pendant que la requête tient déjà la sienne épuiserait le pool sous
        # charge (interblocage). Hors requête (tests, outils), le pool sert.
        self._conn = conn

    @asynccontextmanager
    async def _db(self) -> AsyncIterator[asyncpg.Connection]:
        if self._conn is not None:
            yield self._conn
        else:
            async with self._pool.acquire() as conn:
                yield conn

    async def _state(self) -> DraftState:
        async with self._db() as conn:
            draft = await db_get_draft(conn, self.user_id, self.branch_id)
            if draft is None:
                return DraftState(draft_id=None, revision=0, objects=[])
            return DraftState(
                draft_id=draft.id,
                revision=draft.revision,
                objects=await db_list_objects(conn, draft.id),
            )

    @property
    def manager(self) -> WorkdirManager:
        return self._manager

    def draft_lock(self, space_id: uuid.UUID, slug: str) -> asyncio.Lock:
        return self._views.lock_for(space_id, slug, self.user_id)

    async def discard_view(self, space_id: uuid.UUID, slug: str) -> None:
        await self._views.discard(space_id, slug, self.user_id)

    async def has_draft(self) -> bool:
        async with self._db() as conn:
            return await db_get_draft(conn, self.user_id, self.branch_id) is not None

    async def _store(
        self, space_id: uuid.UUID, ctx: ViewContext, before: DraftState
    ) -> tuple[bool, DraftState]:
        """Capte la vue, remplace le brouillon ; rend (modifié ?, nouvel état)."""
        changes = await capture_changes(ctx)
        previous = {o.path: o.content for o in before.objects}
        current = {c.path: c.content for c in changes}
        if previous == current:
            return False, before
        try:
            async with self._db() as conn:
                draft = await db_replace_draft(
                    conn,
                    user_id=self.user_id,
                    space_id=space_id,
                    branch_id=self.branch_id,
                    head_commit=ctx.head,
                    changes=changes,
                )
                if draft is None:
                    return True, DraftState(draft_id=None, revision=0, objects=[])
                objects = await db_list_objects(conn, draft.id)
        except DraftPathError:
            raise WorkdirError("draft_path_invalid") from None
        except DraftObjectTooLargeError:
            raise WorkdirError("draft_object_too_large") from None
        return True, DraftState(draft_id=draft.id, revision=draft.revision, objects=objects)

    # --- Lectures ------------------------------------------------------------

    async def read_branch(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        read_fn: Callable[[Path], _T],
        *,
        published: bool = False,
    ) -> _T:
        """Lit la branche telle que la voit l'utilisateur (avec son brouillon),
        ou telle qu'elle est publiée (`published=True`, ou pas de brouillon)."""
        if published or self.published_only or not await self.has_draft():
            return await self._manager.read_branch(
                space_id, slug, repo_url, username, token, read_fn
            )

        async def fn(ctx: ViewContext) -> tuple[_T, DraftState | None]:
            return read_fn(ctx.path), None

        return await self._views.run(
            space_id=space_id,
            slug=slug,
            repo_url=repo_url,
            username=username,
            token=token,
            user_id=self.user_id,
            load=self._state,
            fn=fn,
        )

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
        if self.published_only or not await self.has_draft():
            return await self._manager.review_inputs(
                space_id, slug, repo_url, username, token, raw_rel, base_sha_fn, tree_fn
            )

        async def fn(ctx: ViewContext) -> tuple[tuple[str, str | None, dict[str, str], _T], None]:
            base_sha = base_sha_fn(ctx.path)
            raw_base = (
                await self._manager.tree_text(ctx.path, base_sha, raw_rel, ctx.env)
                if base_sha
                else {}
            )
            return (ctx.head, base_sha, raw_base, tree_fn(ctx.path)), None

        return await self._views.run(
            space_id=space_id,
            slug=slug,
            repo_url=repo_url,
            username=username,
            token=token,
            user_id=self.user_id,
            load=self._state,
            fn=fn,
        )

    async def published_blobs(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        paths: list[str],
    ) -> dict[str, str]:
        """Blob actuel de chaque chemin à la tête publiée (absent = n'y existe pas)."""
        if not paths:
            return {}
        async with self._manager.read_copy(space_id, slug, repo_url, username, token) as (
            copy,
            env,
            _head,
        ):
            code, out, _ = await run_git(["ls-tree", "-r", "-z", "HEAD", "--", *paths], copy, env)
        blobs: dict[str, str] = {}
        for line in out.split("\0") if code == 0 else []:
            if line:
                meta, path = line.split("\t", 1)
                blobs[path] = meta.split()[2]
        return blobs

    async def object_versions(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        objects: list[DraftObject],
    ) -> dict[str, ObjectVersions]:
        """Pour chaque objet du brouillon : blob et contenu actuels côté publié,
        et contenu de sa version de base (lus dans la copie de lecture)."""
        out: dict[str, ObjectVersions] = {}
        if not objects:
            return out
        async with self._manager.read_copy(space_id, slug, repo_url, username, token) as (
            copy,
            env,
            _head,
        ):
            paths = [o.path for o in objects]
            code, tree, _ = await run_git(["ls-tree", "-r", "-z", "HEAD", "--", *paths], copy, env)
            current: dict[str, str] = {}
            for line in tree.split("\0") if code == 0 else []:
                if line:
                    meta, path = line.split("\t", 1)
                    current[path] = meta.split()[2]
            for obj in objects:
                blob = current.get(obj.path)
                out[obj.path] = ObjectVersions(
                    current_blob=blob,
                    theirs=await _blob_bytes(copy, env, blob),
                    base=await _blob_bytes(copy, env, obj.base_blob),
                )
        return out

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
        # L'historique est celui du travail publié : un brouillon n'a pas d'histoire.
        return await self._manager.file_history(
            space_id, slug, repo_url, username, token, rel_path, limit
        )

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
        return await self._manager.file_at(space_id, slug, repo_url, username, token, sha, rel_path)

    # --- Écritures : toujours dans le brouillon ---------------------------------

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
        *,
        expect_head: str | None = None,
    ) -> str | None:
        """Applique `write_fn` dans la vue de l'utilisateur et range le résultat
        dans son brouillon. Rend un repère de révision (`draft:<n>`) si le
        brouillon a changé, None sinon (jamais de « commit vide »). Aucun commit,
        aucun push. `expect_head` : tête publiée attendue (revue) ; si elle a
        bougé, refus `review_stale` avant d'écrire."""

        async def fn(ctx: ViewContext) -> tuple[str | None, DraftState | None]:
            if expect_head is not None and ctx.head != expect_head:
                raise WorkdirError("review_stale")
            before = await self._state()
            write_fn(ctx.path)
            changed, state = await self._store(space_id, ctx, before)
            log.info(
                "draft_write",
                space_id=str(space_id),
                slug=slug,
                changed=changed,
                intent=message[:80],
            )
            return (f"draft:{state.revision}" if changed else None), state

        return await self._views.run(
            space_id=space_id,
            slug=slug,
            repo_url=repo_url,
            username=username,
            token=token,
            user_id=self.user_id,
            load=self._state,
            fn=fn,
        )

    async def restore_version(
        self,
        space_id: uuid.UUID,
        slug: str,
        repo_url: str,
        username: str,
        token: str | None,
        number: int,
        author_name: str,
        author_email: str,
    ) -> VersionRestored:
        """Revenir à une version = remettre son arbre **dans le brouillon** (il se
        publie ensuite comme toute modification). L'historique n'est pas réécrit."""

        async def fn(ctx: ViewContext) -> tuple[VersionRestored, DraftState | None]:
            await run_git(["fetch", "-q", "origin", _VERSION_FETCH_REFSPEC], ctx.path, ctx.env)
            tag = f"{_VERSION_PREFIX}{number}"
            code, commit_out, _ = await run_git(["rev-list", "-n", "1", tag], ctx.path, ctx.env)
            if code != 0 or not commit_out.strip():
                raise WorkdirError("version_not_found")
            reach, _, _ = await run_git(
                ["merge-base", "--is-ancestor", commit_out.strip(), "HEAD"], ctx.path, ctx.env
            )
            if reach != 0:
                raise WorkdirError("version_not_found")
            _, subject, _ = await run_git(
                ["tag", "-l", "--format=%(contents:subject)", tag], ctx.path, ctx.env
            )
            label = subject.strip()
            before = await self._state()
            await run_git(["read-tree", "--reset", "-u", f"{tag}^{{tree}}"], ctx.path, ctx.env)
            changed, state = await self._store(space_id, ctx, before)
            if not changed and not state.objects:
                return VersionRestored(number, label, already_current=True, created=False), state
            log.info(
                "version_restored_to_draft",
                space_id=str(space_id),
                number=number,
                intent=f"{_COMMIT_PREFIX}Retour à la version {number}",
            )
            return VersionRestored(number, label, already_current=False, created=changed), state

        return await self._views.run(
            space_id=space_id,
            slug=slug,
            repo_url=repo_url,
            username=username,
            token=token,
            user_id=self.user_id,
            load=self._state,
            fn=fn,
        )
