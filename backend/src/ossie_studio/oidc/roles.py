"""Lecture des rôles dans un jeton OIDC — point d'adaptation isolé.

La structure du claim diffère d'un IdP à l'autre : un **tableau de chaînes**
(Keycloak : `["ossie-admins", "dev"]`) ou un **objet dont les clés sont les
rôles** (Zitadel : `{"ossie-admins": {...}, "dev": {...}}`). On accepte ces deux
formes ; **toute autre forme ne donne aucun rôle** (et l'appelant journalise un
avertissement), jamais de plantage ni de rôle deviné. Un rôle du jeton ne sert
qu'à CHOISIR un profil : jamais à accorder une capacité.
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


def navigate_claim(claims: object, path: str) -> object:
    """Descend un chemin pointé (`realm_access.roles`) dans les claims ; None si
    un segment manque ou n'est pas un objet."""
    current = claims
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def extract_roles(value: object) -> list[str]:
    """Extrait la liste des rôles des deux formes admises ; [] pour toute autre."""
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, dict):
        return [key for key in value if isinstance(key, str)]
    return []


def roles_from_claims(claims: object, path: str) -> list[str]:
    if not path.strip():
        return []
    value = navigate_claim(claims, path)
    if value is None:
        return []
    roles = extract_roles(value)
    if not roles and not isinstance(value, (list, dict)):
        # Forme inattendue : aucun rôle, et on le signale (pas de plantage).
        log.warning("oidc_roles_claim_unexpected_shape", path=path)
    return roles
