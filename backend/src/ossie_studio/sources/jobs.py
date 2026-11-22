"""Collectes en tâche de fond.

Une collecte peut durer plusieurs minutes (clone, milliers de fichiers) : liée à
la requête HTTP, elle était perdue pour l'écran dès que la page était rechargée
(le proxy enregistrait un 499) alors que le serveur la menait à son terme. Elle
tourne donc dans une tâche indépendante de la requête ; l'écran suit son état.

État en mémoire du processus (un seul worker) : un redémarrage du backend perd
le suivi des collectes en cours — le brouillon, lui, ne garde que ce qui a été
écrit. Les collectes terminées restent consultables une heure.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog

from ossie_studio.errors import ApiError

log = structlog.get_logger(__name__)

RUNNING, DONE, FAILED = "running", "done", "failed"
_KEEP_FINISHED = timedelta(hours=1)

# (espace, branche, utilisateur, source) : chacun collecte dans SON brouillon.
JobKey = tuple[uuid.UUID, uuid.UUID, uuid.UUID, str]


@dataclass
class CollectJob:
    state: str
    started_at: datetime
    finished_at: datetime | None = None
    report: dict[str, object] | None = None
    error: dict[str, object] | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    def payload(self) -> dict[str, object]:
        return {
            "state": self.state,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "report": self.report,
            "error": self.error,
        }


class CollectJobs:
    def __init__(self) -> None:
        self._jobs: dict[JobKey, CollectJob] = {}

    def get(self, key: JobKey) -> CollectJob | None:
        return self._jobs.get(key)

    def start(self, key: JobKey, run: Callable[[], Awaitable[dict[str, object]]]) -> CollectJob:
        """Lance la collecte, ou rend celle qui tourne déjà pour cette clé
        (deux clics ne lancent pas deux collectes)."""
        self._forget_old()
        current = self._jobs.get(key)
        if current is not None and current.state == RUNNING:
            return current
        job = CollectJob(state=RUNNING, started_at=datetime.now(UTC))
        self._jobs[key] = job
        job.task = asyncio.create_task(self._run(job, run))
        return job

    async def _run(self, job: CollectJob, run: Callable[[], Awaitable[dict[str, object]]]) -> None:
        try:
            job.report = await run()
            job.state = DONE
        except ApiError as exc:
            job.error = {"error": exc.code, "error_args": exc.args_dict, "status": exc.status_code}
            job.state = FAILED
        except Exception:
            log.exception("collect_job_crashed")
            job.error = {"error": "collect_failed", "error_args": {}, "status": 502}
            job.state = FAILED
        finally:
            job.finished_at = datetime.now(UTC)

    def _forget_old(self) -> None:
        limit = datetime.now(UTC) - _KEEP_FINISHED
        for key, job in list(self._jobs.items()):
            if job.state != RUNNING and job.finished_at and job.finished_at < limit:
                del self._jobs[key]
