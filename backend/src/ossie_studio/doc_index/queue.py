"""La file de validation (enabler 6b171b74) : les demandes de passe sur une vue.

- **Une seule passe à la fois**, pour toute l'application : tous les utilisateurs,
  tous les espaces, même avec plusieurs processus serveur (index unique sur l'état
  « en cours », migration 0055).
- Les demandes attendent **dans l'ordre d'arrivée**.
- **Déduplication** : une vue qui a déjà une demande en attente n'en reçoit pas une
  seconde. Une vue en cours de passe peut en recevoir une (ce qui a changé pendant
  la passe sera vu par la suivante).
- La file **survit à un redémarrage** (table en base) ; une passe interrompue (son
  signe de vie est trop ancien) est remise en attente (`recover`).
- Son **état** se lit : la passe en cours (quelle vue, combien de fichiers faits sur
  combien), les demandes en attente et la position d'une vue (`queue_state`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

import asyncpg

from ossie_studio.doc_index.store import ViewKey

IMPORT = "import"
COLLECT = "collect"
WRITE_BATCH = "write_batch"
PUBLICATION_PAGE = "publication_page"
PERIODIC = "periodic"
PUBLISH = "publish"
TRIGGERS = (IMPORT, COLLECT, WRITE_BATCH, PUBLICATION_PAGE, PERIODIC, PUBLISH)

# Une passe dont le signe de vie a plus de 5 minutes est tenue pour interrompue.
STALE_SECONDS = 300


@dataclass(frozen=True)
class Request:
    id: int
    key: ViewKey
    trigger: str
    state: str
    requested_at: datetime
    started_at: datetime | None = None
    done: int = 0
    total: int = 0

    def payload(self) -> dict[str, object]:
        return {
            "space_id": str(self.key.space_id),
            "branch_id": str(self.key.branch_id),
            "user_id": str(self.key.user_id) if self.key.user_id else None,
            "view": "published" if self.key.user_id is None else "draft",
            "trigger": self.trigger,
            "requested_at": self.requested_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "done": self.done,
            "total": self.total,
        }


def _request(row: asyncpg.Record) -> Request:
    return Request(
        row["id"],
        ViewKey(row["space_id"], row["branch_id"], row["user_id"]),
        row["trigger"],
        row["state"],
        row["requested_at"],
        row["started_at"],
        row["done"],
        row["total"],
    )


_COLUMNS = "id, space_id, branch_id, user_id, trigger, state, requested_at, started_at, done, total"


async def request_pass(conn: asyncpg.Connection, key: ViewKey, trigger: str) -> bool:
    """Demande une passe sur la vue `key` ; rend False si elle en avait déjà une en
    attente (rien n'est ajouté)."""
    if trigger not in TRIGGERS:
        raise ValueError(f"déclencheur de validation inconnu : {trigger}")
    created = await conn.fetchval(
        """
        INSERT INTO validation_requests (space_id, branch_id, user_id, trigger)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT DO NOTHING
        RETURNING id
        """,
        key.space_id,
        key.branch_id,
        key.user_id,
        trigger,
    )
    return created is not None


async def claim_next(conn: asyncpg.Connection) -> Request | None:
    """Prend la plus ancienne demande en attente, si aucune passe ne tourne ; None
    sinon. Une seule passe à la fois : l'index unique sur « en cours » refuse la
    seconde, même posée par un autre processus au même instant."""
    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                f"""
                UPDATE validation_requests
                SET state = 'running', started_at = now(), heartbeat_at = now(),
                    done = 0, total = 0
                WHERE id = (
                    SELECT id FROM validation_requests WHERE state = 'pending'
                    ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED
                )
                RETURNING {_COLUMNS}
                """  # noqa: S608
            )
    except asyncpg.UniqueViolationError:
        return None  # une passe tourne déjà
    return _request(row) if row is not None else None


async def report_progress(conn: asyncpg.Connection, request_id: int, done: int, total: int) -> None:
    """La progression de la passe en cours (et son signe de vie)."""
    await conn.execute(
        "UPDATE validation_requests SET done = $2, total = $3, heartbeat_at = now() WHERE id = $1",
        request_id,
        done,
        total,
    )


async def finish(conn: asyncpg.Connection, request_id: int) -> None:
    """La passe est terminée (réussie ou non) : la file est libérée."""
    await conn.execute("DELETE FROM validation_requests WHERE id = $1", request_id)


async def recover(conn: asyncpg.Connection, stale_seconds: int = STALE_SECONDS) -> int:
    """Remet en attente une passe interrompue (signe de vie trop ancien) ; si la vue a
    déjà une demande en attente, l'interrompue est simplement retirée. Rend leur
    nombre."""
    async with conn.transaction():
        stale = await conn.fetch(
            "SELECT id, space_id, branch_id, user_id FROM validation_requests "
            "WHERE state = 'running' AND heartbeat_at < now() - make_interval(secs => $1) "
            "FOR UPDATE",
            stale_seconds,
        )
        for row in stale:
            pending = await conn.fetchval(
                "SELECT 1 FROM validation_requests WHERE state = 'pending' AND space_id = $1 "
                "AND branch_id = $2 AND user_id IS NOT DISTINCT FROM $3",
                row["space_id"],
                row["branch_id"],
                row["user_id"],
            )
            if pending:
                await conn.execute("DELETE FROM validation_requests WHERE id = $1", row["id"])
            else:
                await conn.execute(
                    "UPDATE validation_requests SET state = 'pending', started_at = NULL, "
                    "heartbeat_at = NULL, done = 0, total = 0 WHERE id = $1",
                    row["id"],
                )
    return len(stale)


@dataclass(frozen=True)
class QueueState:
    running: Request | None
    pending: list[Request] = field(default_factory=list)

    def position(self, key: ViewKey) -> int | None:
        """La place de la vue dans l'attente (1 : la prochaine) ; 0 si sa passe
        tourne ; None si elle n'est pas dans la file."""
        if self.running is not None and self.running.key == key:
            for index, req in enumerate(self.pending, start=1):
                if req.key == key:
                    return index
            return 0
        for index, req in enumerate(self.pending, start=1):
            if req.key == key:
                return index
        return None

    def payload(self, keys: dict[str, ViewKey] | None = None) -> dict[str, object]:
        out: dict[str, object] = {
            "running": self.running.payload() if self.running else None,
            "pending": len(self.pending),
        }
        for name, key in (keys or {}).items():
            out[name] = {"position": self.position(key), "queued": self.queued(key)}
        return out

    def queued(self, key: ViewKey) -> bool:
        return any(req.key == key for req in self.pending)


async def queue_state(conn: asyncpg.Connection) -> QueueState:
    rows = await conn.fetch(
        f"SELECT {_COLUMNS} FROM validation_requests ORDER BY id"  # noqa: S608
    )
    requests = [_request(r) for r in rows]
    running = next((r for r in requests if r.state == "running"), None)
    return QueueState(running, [r for r in requests if r.state == "pending"])


async def views_to_validate(conn: asyncpg.Connection) -> list[ViewKey]:
    """Les vues qui ont des fichiers `a_valider` (déclencheur périodique)."""
    rows = await conn.fetch(
        """
        SELECT DISTINCT v.space_id, v.branch_id, v.user_id
        FROM doc_index_view v
        WHERE EXISTS (
            SELECT 1 FROM doc_index_file f WHERE f.view_id = v.id AND f.status = 'a_valider'
        )
        ORDER BY v.space_id, v.branch_id
        """
    )
    return [ViewKey(r["space_id"], r["branch_id"], r["user_id"]) for r in rows]


def view_of(space_id: uuid.UUID, branch_id: uuid.UUID, user_id: uuid.UUID | None) -> ViewKey:
    return ViewKey(space_id, branch_id, user_id)
