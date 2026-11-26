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


def _keep(event: AuditEvent, filters: AuditFilters) -> bool:
    """Filtres de la page Historique, comme pour les lignes de la table d'audit."""
    if filters.object_type and event.object_type != filters.object_type:
        return False
    if filters.actor and filters.actor.lower() not in event.actor_label.lower():
        return False
    if filters.since is not None and event.at < filters.since:
        return False
    if filters.until is not None and event.at >= filters.until:
        return False
    return not (filters.before is not None and event.at >= filters.before)


def governance_events(commits: list[PathCommit], filters: AuditFilters) -> list[AuditEvent]:
    """Événements de gouvernance, du plus récent au plus ancien, filtrés comme
    les lignes de la table d'audit."""
    out: list[AuditEvent] = []
    for commit in commits:
        for status, path in commit.files:
            event = _event(commit, status, path)
            if event is not None and _keep(event, filters):
                out.append(event)
    out.sort(key=lambda e: e.at, reverse=True)
    return out


GOVERNANCE_TYPES = tuple(_TYPES.values())

# --- Référentiels (epic « Référentiels ») : eux aussi vivent dans Git ---------------------------

REFERENTIEL_TYPE = "referentiel"
REFERENTIEL_TYPES = (REFERENTIEL_TYPE,)
_REFERENTIEL_MODEL = "modele.schema.yaml"


def referentiel_events(commits: list[PathCommit], filters: AuditFilters) -> list[AuditEvent]:
    """Un événement par référentiel touché par un commit publié : créé ou
    supprimé selon son fichier de modèle, modifié sinon (modèle, valeurs ou
    imports)."""
    out: list[AuditEvent] = []
    for commit in commits:
        actions: dict[str, str] = {}
        for status, path in commit.files:
            parts = PurePosixPath(path).parts
            if len(parts) < 3 or parts[0] != "referentiels":
                continue
            name = parts[1]
            if parts[2] == _REFERENTIEL_MODEL and status in _ACTIONS:
                actions[name] = _ACTIONS[status]
            else:
                actions.setdefault(name, UPDATED)
        for name, action in sorted(actions.items()):
            event = AuditEvent(
                at=datetime.fromisoformat(commit.date),
                actor_label=commit.author,
                actor_id=None,
                action=action,
                object_type=REFERENTIEL_TYPE,
                object_id=name,
                object_label=name,
                detail={"commit": commit.sha[:12], "message": commit.message},
                origin="git",
                ref=commit.sha,
            )
            if _keep(event, filters):
                out.append(event)
    out.sort(key=lambda e: e.at, reverse=True)
    return out


# --- Cartographie (epic « Cartographie — types et objets ») : elle aussi vit dans Git --------

CARTO_TYPE = "carto_type"
CARTO_OBJECT = "carto_object"
CARTOGRAPHIE_TYPES = (CARTO_TYPE, CARTO_OBJECT)
_CARTO_TYPE_SUFFIX = ".schema.yaml"


def _carto_target(path: str) -> tuple[str, str] | None:
    """(type d'objet du journal, identifiant) d'un fichier de la cartographie : un
    type (`cartographie/types/<slug>.schema.yaml`, son slug) ou un objet
    (`cartographie/objets/<type>/<clé en minuscules>.yaml`, sa clé)."""
    parts = PurePosixPath(path).parts
    if len(parts) == 3 and parts[:2] == ("cartographie", "types"):
        if parts[2].endswith(_CARTO_TYPE_SUFFIX):
            return CARTO_TYPE, parts[2][: -len(_CARTO_TYPE_SUFFIX)]
    if len(parts) == 4 and parts[:2] == ("cartographie", "objets"):
        if parts[3].endswith(".yaml"):
            return CARTO_OBJECT, parts[3][: -len(".yaml")].upper()
    return None


def cartographie_events(commits: list[PathCommit], filters: AuditFilters) -> list[AuditEvent]:
    """Un événement par type ou objet de la cartographie touché par un commit
    publié : créé, modifié ou supprimé selon son fichier."""
    out: list[AuditEvent] = []
    for commit in commits:
        for status, path in commit.files:
            found = _carto_target(path)
            if found is None:
                continue
            object_type, object_id = found
            event = AuditEvent(
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
            if _keep(event, filters):
                out.append(event)
    out.sort(key=lambda e: e.at, reverse=True)
    return out
