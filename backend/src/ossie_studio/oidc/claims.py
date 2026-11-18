from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VerifiedClaims:
    """Claims d'un id_token déjà validé (signature, iss, aud, exp, nonce).

    `subject` est le `sub` immuable chez l'émetteur ; `email` et `email_verified`
    servent de pont initial, jamais d'ancrage.
    """

    subject: str
    email: str | None
    email_verified: bool
    name: str | None = None
