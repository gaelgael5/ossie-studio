from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class VerifiedClaims:
    """Claims d'un id_token déjà validé (signature, iss, aud, exp, nonce).

    `subject` est le `sub` immuable chez l'émetteur ; `email` et `email_verified`
    servent de pont initial, jamais d'ancrage. `raw` garde l'ensemble des claims
    validés (lecture des rôles via leur chemin).
    """

    subject: str
    email: str | None
    email_verified: bool
    name: str | None = None
    raw: dict[str, object] = field(default_factory=dict)
