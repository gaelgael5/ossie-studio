"""Historique de la gouvernance lu dans Git (ticket docflow cf4434da).

Décision de Gael, 23/11/2026 : la gouvernance (tags gérés, règles de tags,
certifications, schémas et niveaux de classification) vit dans le dépôt de
l'espace ; son historique est celui des **commits publiés** qui touchent
`governance/` — auteur et date portés par Git. Un brouillon non publié
n'apparaît pas. Aucune ligne n'est écrite en base pour elle.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath

from ossie_studio.audit.service import CREATED, DELETED, UPDATED, AuditEvent, AuditFilters
from ossie_studio.spaces.workdir import PathCommit

# Dossier de gouvernance → type d'objet affiché. Un niveau de classification vit
# dans le fichier de son schéma : son changement apparaît comme celui du schéma.
_TYPES = {
    "governance/tags": "governed_tag",
    "governance/tag-rules": "tag_rule",
    "governance/certifications": "certification",
    "governance/classifications": "classification_scheme",
}
_ACTIONS = {"A": CREATED, "D": DELETED}


def _event(commit: PathCommit, status: str, path: str) -> AuditEvent | None:
    file = PurePosixPath(path)
    directory = next((d for d in _TYPES if str(file.parent) == d), None)
    if directory is None:
        return None
    object_type = _TYPES[directory]
    object_id = file.stem
    return AuditEvent(
        at=datetime.fromisoformat(commit.date),
        actor_label=commit.author,
        actor_id=None,
        action=_ACTIONS.get(status, UPDATED),
        object_type=object_type,
        object_id=object_id,
        object_label=object_id,
        detail={"commit": commit.sha[:12], "message": commit.message},
        origin="git",
        ref=commit.sha,
    )


def governance_events(commits: list[PathCommit], filters: AuditFilters) -> list[AuditEvent]:
    """Événements de gouvernance, du plus récent au plus ancien, filtrés comme
    les lignes de la table d'audit."""
    out: list[AuditEvent] = []
    for commit in commits:
        for status, path in commit.files:
            event = _event(commit, status, path)
            if event is None:
                continue
            if filters.object_type and event.object_type != filters.object_type:
                continue
            if filters.actor and filters.actor.lower() not in event.actor_label.lower():
                continue
            if filters.since is not None and event.at < filters.since:
                continue
            if filters.until is not None and event.at >= filters.until:
                continue
            if filters.before is not None and event.at >= filters.before:
                continue
            out.append(event)
    out.sort(key=lambda e: e.at, reverse=True)
    return out


GOVERNANCE_TYPES = tuple(_TYPES.values())
