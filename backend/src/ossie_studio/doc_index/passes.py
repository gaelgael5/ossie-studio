"""Une passe de validation sur une vue (enabler 6b171b74), et le travailleur qui
vide la file une passe à la fois.

Ce que fait une passe :
1. comparer le disque à l'index (`service.compare_view`) : les fichiers nouveaux ou
   changés passent `a_valider` ;
2. valider les fichiers `a_valider` : le **document seul** d'abord, réutilisé s'il est
   déjà validé pour cette empreinte et cette version des règles (`RULES_VERSION`) ;
   puis les **règles entre fichiers** sur la vue ;
3. poser l'état de chaque fichier : `valide` ou `en_erreur`, avec ses problèmes ;
4. publier sa progression au fil de l'eau (`queue.report_progress`).

Les règles : celles que chaque type de document déclare dans le registre, appliquées
par `rules.py` (ticket d7139b17) : document seul, références, règles entre fichiers.

Une passe qui échoue libère la file, marque la vue comme non validée et l'écrit dans
le journal d'audit (auteur « system ») ; la demande suivante démarre.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import asyncpg
import structlog

from ossie_studio.app_settings.service import VALIDATION_INTERVAL_MINUTES, value_of
from ossie_studio.audit.service import UPDATED, AuditEntry, record
from ossie_studio.config import Settings
from ossie_studio.connections.service import db_get_connection, resolve_connection_token
from ossie_studio.crypto import CryptoService
from ossie_studio.doc_index import queue, store
from ossie_studio.doc_index.rules import (
    RULES_VERSION,
    document_findings,
    problem,
    reference_findings,
    view_rules,
)
from ossie_studio.doc_index.service import blob_reader, compare_view, tree_listing
from ossie_studio.doc_index.store import EN_ERREUR, VALIDE, View, ViewKey
from ossie_studio.drafts.store import git_blob_sha
from ossie_studio.drafts.validation import Finding, run_rules
from ossie_studio.forges.registry import get_strategy
from ossie_studio.spaces.branch_service import db_get_branch
from ossie_studio.spaces.service import db_get_space
from ossie_studio.spaces.views import DraftState, DraftViewManager, ViewContext
from ossie_studio.spaces.workdir import WorkdirManager

log = structlog.get_logger(__name__)

# Une progression publiée tous les N fichiers.
PROGRESS_EVERY = 25
# Le travailleur regarde la file toutes les N secondes quand elle est vide.
POLL_SECONDS = 2.0
# Attendre une passe (`Validation.ensure`) : on regarde la file toutes les N secondes,
# au plus WAIT_SECONDS.
POLL_WAIT_SECONDS = 0.2
WAIT_SECONDS = 300.0


class ValidationBusy(Exception):
    """La passe attendue n'a pas eu son tour à temps (position dans la file)."""

    def __init__(self, position: int) -> None:
        super().__init__(f"position {position}")
        self.position = position


class ValidationFailed(Exception):
    """La passe de la vue a échoué ; la vue n'est pas validée."""

Problem = dict[str, object]
Progress = Callable[[int, int], Awaitable[None]]


def _blocking(problems: list[Problem]) -> bool:
    return any(p.get("level") == "error" for p in problems)


# Vues dont une passe complète a tourné depuis le démarrage du processus. Une passe
# sans fichier changé n'est raccourcie que pour elles : après un déploiement, les
# règles entre fichiers ont pu changer, la première passe relance tout.
_FULL_PASS_DONE: set[ViewKey] = set()


async def _unchanged(
    conn: asyncpg.Connection, key: ViewKey, touched: int, trigger: str | None = None
) -> PassResult | None:
    """Le résultat inchangé de la vue si rien n'est à revalider ; sinon None."""
    if touched or key not in _FULL_PASS_DONE:
        return None
    view = await store.db_ensure_view(conn, key)
    files = await store.db_view_files(conn, view.id)
    if any(row.status == store.A_VALIDER for row in files.values()):
        return None
    await store.db_mark_validated(conn, view.id, trigger)
    errors = sum(row.status == EN_ERREUR for row in files.values())
    return PassResult(key, len(files), 0, errors)


@dataclass(frozen=True)
class PassResult:
    key: ViewKey
    files: int
    validated: int
    errors: int


async def validate_view(
    conn: asyncpg.Connection,
    view: View,
    root: Path,
    findings: list[Finding],
    progress: Progress,
    trigger: str | None = None,
) -> PassResult:
    """Étapes 2 et 3 d'une passe, une fois la vue comparée au disque : `root` est la
    vue sur disque, `findings` ce que les règles entre fichiers y ont trouvé, `trigger`
    ce qui a demandé la passe."""
    files = await store.db_view_files(conn, view.id)
    todo = sorted(p for p, row in files.items() if row.status == store.A_VALIDER)
    results = await store.db_document_results(conn, (row.content_id for row in files.values()))
    total = len(todo)
    await progress(0, total)
    for done, path in enumerate(todo, start=1):
        row = files[path]
        cached = results.get(row.content_id)
        if cached is None or cached[1] != RULES_VERSION:
            file = root / path
            data = file.read_bytes() if file.is_file() else b""
            if git_blob_sha(data) == row.blob:
                problems = [
                    problem(f, path, row.documents)
                    for f in document_findings(path, data, row.documents)
                ]
                await store.db_set_document_result(conn, row.content_id, problems, RULES_VERSION)
                results[row.content_id] = (problems, RULES_VERSION)
        if done % PROGRESS_EVERY == 0 or done == total:
            await progress(done, total)
    by_path: dict[str, list[Problem]] = {}
    for finding in [*findings, *reference_findings(files)]:
        row_or_none = files.get(finding.path)
        documents = row_or_none.documents if row_or_none is not None else []
        by_path.setdefault(finding.path, []).append(problem(finding, finding.path, documents))
    statuses: dict[str, tuple[str, list[Problem]]] = {}
    errors = 0
    for path, row in files.items():
        problems = [*results.get(row.content_id, ([], None))[0], *by_path.get(path, [])]
        blocking = _blocking(problems)
        errors += blocking
        statuses[path] = (EN_ERREUR if blocking else VALIDE, problems)
    await store.db_set_statuses(conn, view.id, statuses)
    await store.db_mark_validated(conn, view.id, trigger)
    return PassResult(view.key, len(files), total, errors)


@dataclass
class Validation:
    """De quoi faire une passe : la base, les accès aux dépôts, les vues."""

    pool: asyncpg.Pool
    crypto: CryptoService
    workdirs: WorkdirManager
    views: DraftViewManager
    settings: Settings

    async def _access(self, key: ViewKey) -> tuple[str, str, str, str | None]:
        """(branche, dépôt, identifiant, jeton) de la vue."""
        async with self.pool.acquire() as conn:
            space = await db_get_space(conn, key.space_id)
            branch = await db_get_branch(conn, key.branch_id)
            if space is None or branch is None or branch.space_id != key.space_id:
                raise LookupError("espace ou branche introuvable")
            connection = await db_get_connection(conn, space.connection_id)
            if connection is None:
                raise LookupError("connexion de l'espace introuvable")
            token = (await resolve_connection_token(conn, self.crypto, connection)).reveal()
        return branch.git_ref, space.repo_url, get_strategy(connection.forge).git_username(), token

    async def run(self, request: queue.Request) -> PassResult:
        """La passe d'une demande (sans toucher à la file)."""
        key = request.key
        slug, repo_url, username, token = await self._access(key)

        async def progress(done: int, total: int) -> None:
            async with self.pool.acquire() as conn:
                await queue.report_progress(conn, request.id, done, total)

        async def validate(
            root: Path, env: dict[str, str], head: str, overrides: dict[str, bytes | None]
        ) -> PassResult:
            listing = await tree_listing(root, env, head)
            known: dict[str, bytes] = {}
            for path, content in overrides.items():
                if content is None:
                    listing.pop(path, None)
                else:
                    known[git_blob_sha(content)] = content
                    listing[path] = git_blob_sha(content)
            async with self.pool.acquire() as conn:
                comparison = await compare_view(
                    conn, key, head, listing, blob_reader(root, env), known
                )
                unchanged = await _unchanged(conn, key, comparison.touched, request.trigger)
            if unchanged is not None:
                # Rien n'a bougé depuis la dernière passe complète de ce processus :
                # les règles rendraient le même verdict. Sans ce raccourci, une passe
                # « pour rien » relisait toute la vue (6 s pour 2 700 fichiers) en
                # tenant le verrou de la vue, et les pages attendaient (bug c6a4ab74).
                return unchanged
            findings = await asyncio.to_thread(run_rules, root, view_rules())
            async with self.pool.acquire() as conn, conn.transaction():
                view = await store.db_ensure_view(conn, key)
                result = await validate_view(
                    conn, view, root, findings, progress, request.trigger
                )
            _FULL_PASS_DONE.add(key)
            return result

        if key.user_id is None:
            async with self.workdirs.read_copy(key.space_id, slug, repo_url, username, token) as (
                root,
                env,
                head,
            ):
                return await validate(root, env, head, {})
        # Import local : le brouillon importe l'index (écriture), et la passe le brouillon.
        from ossie_studio.drafts.workdir import DraftWorkdir

        workdir = DraftWorkdir(self.workdirs, self.views, self.pool, key.user_id, key.branch_id)

        async def in_view(ctx: ViewContext, state: DraftState) -> PassResult:
            overrides = {o.path: o.content for o in state.objects}
            return await validate(ctx.path, ctx.env, ctx.head, overrides)

        return await workdir.run_in_view(key.space_id, slug, repo_url, username, token, in_view)

    async def run_next(self) -> queue.Request | None:
        """Prend la prochaine demande et fait sa passe ; None si la file est vide ou
        qu'une passe tourne déjà (ailleurs)."""
        async with self.pool.acquire() as conn:
            request = await queue.claim_next(conn)
        if request is None:
            return None
        started = time.monotonic()
        try:
            result = await self.run(request)
            log.info(
                "validation_pass_done",
                space_id=str(request.key.space_id),
                trigger=request.trigger,
                files=result.files,
                validated=result.validated,
                errors=result.errors,
                # Durée : une passe tient le verrou de la vue (bug c6a4ab74).
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        except Exception as exc:  # une passe qui échoue ne bloque jamais la file
            log.exception("validation_pass_failed", space_id=str(request.key.space_id))
            await self._failed(request, exc)
        finally:
            async with self.pool.acquire() as conn:
                await queue.finish(conn, request.id)
        return request

    async def _failed(self, request: queue.Request, exc: Exception) -> None:
        key = request.key
        view = "publiee" if key.user_id is None else str(key.user_id)
        async with self.pool.acquire() as conn:
            await store.db_mark_failed(conn, key, f"{type(exc).__name__} : {exc}")
            await record(
                conn,
                None,
                AuditEntry(
                    action=UPDATED,
                    object_type="validation_pass",
                    object_id=f"{key.branch_id}/{view}",
                    object_label=f"validation {view}",
                    detail={
                        "outcome": "failed",
                        "trigger": request.trigger,
                        "error": type(exc).__name__,
                    },
                ),
            )

    async def ensure(
        self, key: ViewKey, trigger: str = queue.PUBLICATION_PAGE, wait: float = WAIT_SECONDS
    ) -> None:
        """Une passe à jour sur la vue `key`, avant d'en lire le journal (page de
        publication, agent, conditions de publication). La demande passe par la file :
        une seule passe à la fois ; en attendant son tour, on fait celles qui
        précèdent (le travailleur de fond fait de même). Lève ValidationFailed si la
        passe de la vue échoue, ValidationBusy si l'attente dépasse `wait` secondes."""
        async with self.pool.acquire() as conn:
            await queue.request_pass(conn, key, trigger)
        deadline = time.monotonic() + wait
        while True:
            async with self.pool.acquire() as conn:
                state = await queue.queue_state(conn)
            if state.position(key) is None:
                break
            if await self.run_next() is None:
                if time.monotonic() > deadline:
                    raise ValidationBusy(state.position(key) or 0)
                await asyncio.sleep(POLL_WAIT_SECONDS)
        async with self.pool.acquire() as conn:
            summary = await store.db_view_summary(conn, key)
        if summary is None or summary.validation_error:
            raise ValidationFailed(summary.validation_error if summary else "")

    async def request_periodic(self) -> int:
        """Demande une passe pour chaque vue qui a des fichiers à valider ; rend le
        nombre de demandes ajoutées."""
        async with self.pool.acquire() as conn:
            added = 0
            for key in await queue.views_to_validate(conn):
                added += await queue.request_pass(conn, key, queue.PERIODIC)
        return added

    async def interval_seconds(self) -> float:
        async with self.pool.acquire() as conn:
            minutes = await value_of(conn, self.settings, VALIDATION_INTERVAL_MINUTES)
        return float(minutes) * 60

    async def forever(self) -> None:
        """Le travailleur : une passe à la fois, et toutes les N minutes une demande
        pour les vues qui ont des fichiers à valider."""
        async with self.pool.acquire() as conn:
            recovered = await queue.recover(conn)
        if recovered:
            log.info("validation_passes_recovered", count=recovered)
        last_periodic = time.monotonic()
        while True:
            try:
                if time.monotonic() - last_periodic >= await self.interval_seconds():
                    last_periodic = time.monotonic()
                    await self.request_periodic()
                    async with self.pool.acquire() as conn:
                        await queue.recover(conn)
                if await self.run_next() is not None:
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("validation_worker_error")
            await asyncio.sleep(POLL_SECONDS)


def view_key(space_id: uuid.UUID, branch_id: uuid.UUID, user_id: uuid.UUID | None) -> ViewKey:
    return ViewKey(space_id, branch_id, user_id)
