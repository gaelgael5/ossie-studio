from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum

from ossie_studio.oidc.claims import VerifiedClaims


class LinkOutcome(Enum):
    """Résultat du rattachement d'une identité fédérée à un compte."""

    LINKED_EXISTING = "linked_existing"  # (issuer, sub) déjà connu, ou pont email vérifié
    CREATED_NEW = "created_new"  # aucun compte : nouvel arrivant au rôle par défaut
    AMBIGUOUS_EMAIL = "ambiguous_email"  # plusieurs comptes portent l'email → 403, l'admin tranche
    REFUSED_UNVERIFIED = "refused_unverified"  # email absent ou non vérifié → fail-closed


@dataclass(frozen=True)
class LinkDecision:
    outcome: LinkOutcome
    # Compte à réutiliser (épinglage du sub) ; None quand il faut en créer un ou refuser.
    user_id: uuid.UUID | None = None


def decide_link(
    pinned_user_id: uuid.UUID | None,
    matched_user_ids: list[uuid.UUID],
    claims: VerifiedClaims,
) -> LinkDecision:
    """Décide, sans aucune I/O, comment rattacher une identité fédérée.

    - `pinned_user_id` : compte déjà ancré sur ce couple (issuer, sub), s'il existe.
    - `matched_user_ids` : comptes portant l'email des claims (pont email).

    L'ordre est volontaire : le couple (issuer, sub) prime toujours sur l'email,
    pour qu'un email modifié chez l'IdP ne crée jamais de doublon.
    """
    if pinned_user_id is not None:
        return LinkDecision(LinkOutcome.LINKED_EXISTING, pinned_user_id)

    # Premier login de ce couple : pont par email, à condition qu'il soit vérifié.
    if not claims.email or not claims.email_verified:
        return LinkDecision(LinkOutcome.REFUSED_UNVERIFIED)

    if len(matched_user_ids) == 1:
        return LinkDecision(LinkOutcome.LINKED_EXISTING, matched_user_ids[0])
    if len(matched_user_ids) > 1:
        return LinkDecision(LinkOutcome.AMBIGUOUS_EMAIL)
    return LinkDecision(LinkOutcome.CREATED_NEW)
