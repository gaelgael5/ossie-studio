"""L'état de validation d'une vue tel que la page de publication et les agents le lisent
(ticket 48618517, epic « Index des documents et validation avant publication »).

Gael : « Quand on arrive sur la page de publication […] tu regardes en asynchrone les
fichiers qui nécessitent d'être revalidés et tu le fais. Puis tu checks que tous les
fichiers présents sur le disque sont bien dans l'index et validés. […] Le bouton
publier ne s'active que quand tout est validé. »

Tout se lit dans l'index, sans attendre de passe :
- chaque fichier de la vue : son document (type, identité), son état dans le
  brouillon (créé, modifié, supprimé, ou hors brouillon), son statut de validation
  (`valid`, `error`, `warning` = valide avec des informations, `to_validate`), s'il a
  été détecté par la dernière comparaison au disque (modifié hors de l'application),
  et ses problèmes ; un problème déjà présent, à l'identique, dans la version publiée
  validée le dit (`published`), mais bloque quand même (règle d'or, ticket d7139b17) ;
- les comptes par filtre, la comparaison disque / index, la dernière passe complète
  (date, déclencheur, échec) et la place de la vue dans la file.

La même lecture sert la page (`GET …/draft/validation/state`), les conditions de
publication (`publish_readiness`) et les primitives `get_publish_readiness` et
`get_validation_log` : à un instant donné, elles disent la même chose.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import asyncpg

from ossie_studio.doc_index import queue
from ossie_studio.doc_index.store import A_VALIDER, EN_ERREUR, ViewKey

FILTERS = ("all", "problems", "errors", "warnings", "to_validate", "mine")
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000

VALID = "valid"
ERROR = "error"
WARNING = "warning"
TO_VALIDATE = "to_validate"
DELETED = "deleted"

# Ordre de la liste : ce qui demande une action d'abord.
_ORDER = {ERROR: 0, WARNING: 1, TO_VALIDATE: 2, DELETED: 3, VALID: 4}


def _problem_key(problem: Mapping[str, Any]) -> str:
    """Ce qui fait qu'un problème est « le même » dans deux vues."""
    return json.dumps(
        [problem.get("rule"), problem.get("code"), problem.get("args") or {}],
        sort_keys=True,
        ensure_ascii=False,
    )


def _json_list(raw: object) -> list[Any]:
    value = json.loads(raw) if isinstance(raw, str) else raw
    return list(value) if isinstance(value, list) else []


@dataclass(frozen=True)
class FileState:
    path: str
    status: str
    draft: str | None
    detected: bool
    keys: tuple[str, ...]
    documents: tuple[dict[str, Any], ...]
    problems: tuple[dict[str, Any], ...]

    @property
    def errors(self) -> bool:
        return any(p["blocking"] for p in self.problems)

    def matches(self, filter_name: str) -> bool:
        if filter_name == "problems":
            return self.status in (ERROR, WARNING)
        if filter_name == "errors":
            return self.status == ERROR
        if filter_name == "warnings":
            return self.status == WARNING
        if filter_name == "to_validate":
            return self.status == TO_VALIDATE
        if filter_name == "mine":
            return self.draft is not None
        return True

    def found(self, text: str) -> bool:
        """La recherche par chemin, clé ou nom (sans casse)."""
        if not text:
            return True
        needle = text.lower()
        names = [str(d.get("name") or "") for d in self.documents]
        return any(needle in value.lower() for value in (self.path, *self.keys, *names))

    def payload(self) -> dict[str, Any]:
        first = self.documents[0] if self.documents else {}
        return {
            "path": self.path,
            "document_type": first.get("document_type"),
            "identity": first.get("identity"),
            "name": first.get("name"),
            "draft": self.draft,
            "status": self.status,
            "detected": self.detected,
            "problems": list(self.problems),
        }


@dataclass(frozen=True)
class QueueView:
    """La passe de la vue dans la file : en cours, en attente (combien de demandes
    avant elle), ou absente."""

    state: str | None = None
    position: int | None = None
    ahead: int = 0
    done: int = 0
    total: int = 0

    def payload(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "position": self.position,
            "ahead": self.ahead,
            "done": self.done,
            "total": self.total,
        }


def queue_view(state: queue.QueueState, key: ViewKey) -> QueueView:
    running = state.running
    if running is not None and running.key == key:
        return QueueView("running", 0, 0, running.done, running.total)
    for index, request in enumerate(state.pending, start=1):
        if request.key == key:
            ahead = index - 1 + (1 if running is not None else 0)
            return QueueView("pending", index, ahead)
    return QueueView()


@dataclass(frozen=True)
class ValidationState:
    key: ViewKey
    compared: bool
    disk_files: int | None
    detected: int
    validated_at: datetime | None
    validated_trigger: str | None
    validation_error: str | None
    queue: QueueView
    files: list[FileState] = field(default_factory=list)

    @property
    def indexed(self) -> list[FileState]:
        return [f for f in self.files if f.status != DELETED]

    def count(self, filter_name: str) -> int:
        return sum(1 for f in self.files if f.matches(filter_name))

    @property
    def counts(self) -> dict[str, int]:
        out = {name: self.count(name) for name in FILTERS}
        out["valid"] = sum(1 for f in self.files if f.status == VALID)
        return out

    @property
    def errors(self) -> int:
        return self.count("errors")

    @property
    def errors_outside_draft(self) -> int:
        return sum(1 for f in self.files if f.status == ERROR and f.draft is None)

    @property
    def to_validate(self) -> int:
        return self.count("to_validate")

    @property
    def pending(self) -> bool:
        """La validation n'est pas à jour : vue jamais comparée au disque, fichiers à
        valider, ou passe de la vue dans la file."""
        return not self.compared or self.to_validate > 0 or self.queue.state is not None

    def reasons(self) -> list[str]:
        """Ce qui bloque la publication côté validation (règle d'or)."""
        out = []
        if self.errors:
            out.append("validation_errors")
        if self.pending:
            out.append("validation_pending")
        elif self.validation_error:
            out.append("validation_failed")
        return out

    def summary(self) -> dict[str, Any]:
        """Ce que les conditions de publication disent de la validation."""
        return {
            "errors": self.errors,
            "errors_outside_draft": self.errors_outside_draft,
            "to_validate": self.to_validate,
            "queue": self.queue.payload(),
        }

    def payload(
        self, filter_name: str = "problems", text: str = "", limit: int = DEFAULT_LIMIT
    ) -> dict[str, Any]:
        chosen = sorted(
            (f for f in self.files if f.matches(filter_name) and f.found(text)),
            key=lambda f: (_ORDER.get(f.status, 9), f.path),
        )
        return {
            "view": "draft" if self.key.user_id is not None else "published",
            "counts": self.counts,
            "errors_outside_draft": self.errors_outside_draft,
            "comparison": {
                "compared": self.compared,
                "disk_files": self.disk_files,
                "index_files": len(self.indexed),
                "detected": self.detected,
            },
            "last_pass": {
                "at": self.validated_at.isoformat() if self.validated_at else None,
                "trigger": self.validated_trigger,
                "error": self.validation_error,
            },
            "queue": self.queue.payload(),
            "files": [f.payload() for f in chosen[:limit]],
            "total": len(chosen),
            "limit": limit,
        }


def _status(status: str, problems: list[dict[str, Any]]) -> str:
    if status == A_VALIDER:
        return TO_VALIDATE
    if status == EN_ERREUR:
        return ERROR
    return WARNING if problems else VALID


async def _published_problems(conn: asyncpg.Connection, key: ViewKey) -> dict[str, set[str]]:
    """Les problèmes de la version publiée, si elle a été validée : chemin → problèmes."""
    rows = await conn.fetch(
        """
        SELECT f.path, f.problems
        FROM doc_index_file f JOIN doc_index_view v ON v.id = f.view_id
        WHERE v.space_id = $1 AND v.branch_id = $2 AND v.user_id IS NULL
          AND v.validated_at IS NOT NULL AND jsonb_array_length(f.problems) > 0
        """,
        key.space_id,
        key.branch_id,
    )
    return {r["path"]: {_problem_key(p) for p in _json_list(r["problems"])} for r in rows}


async def view_state(
    conn: asyncpg.Connection, key: ViewKey, draft: Mapping[str, str] | None = None
) -> ValidationState:
    """L'état de validation de la vue `key` ; `draft` : les objets du brouillon de la
    personne (chemin → `created` / `modified` / `deleted`)."""
    draft = dict(draft or {})
    view = await conn.fetchrow(
        "SELECT id, compared_at, disk_files, detected, validated_at, validated_trigger, "
        "validation_error FROM doc_index_view "
        "WHERE space_id = $1 AND branch_id = $2 AND user_id IS NOT DISTINCT FROM $3",
        key.space_id,
        key.branch_id,
        key.user_id,
    )
    rows = []
    if view is not None:
        rows = await conn.fetch(
            """
            SELECT f.path, f.status, f.problems, c.keys, c.documents
            FROM doc_index_file f JOIN doc_index_content c ON c.id = f.content_id
            WHERE f.view_id = $1
            """,
            view["id"],
        )
    published = await _published_problems(conn, key) if key.user_id is not None else {}
    detected = set(view["detected"] or []) if view is not None else set()
    files = []
    for r in rows:
        path = r["path"]
        problems = [
            {
                "level": p.get("level"),
                "rule": p.get("rule"),
                "message_code": p.get("code"),
                "args": p.get("args") or {},
                "blocking": p.get("level") == "error",
                "published": _problem_key(p) in published.get(path, set()),
            }
            for p in _json_list(r["problems"])
        ]
        files.append(
            FileState(
                path,
                _status(r["status"], problems),
                draft.get(path),
                path in detected,
                tuple(r["keys"]),
                tuple(_json_list(r["documents"])),
                tuple(problems),
            )
        )
    indexed = {f.path for f in files}
    for path, state in draft.items():
        if state == "deleted" and path not in indexed:
            files.append(FileState(path, DELETED, state, False, (), (), ()))
    return ValidationState(
        key,
        view is not None and view["compared_at"] is not None,
        view["disk_files"] if view is not None else None,
        len(detected),
        view["validated_at"] if view is not None else None,
        view["validated_trigger"] if view is not None else None,
        view["validation_error"] if view is not None else None,
        queue_view(await queue.queue_state(conn), key),
        files,
    )
