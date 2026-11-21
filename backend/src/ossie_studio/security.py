from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()

# Haché d'une valeur jetable : vérifié quand l'email est inconnu, pour que le
# temps de réponse ne révèle pas si un compte existe.
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    """Hache un mot de passe avec argon2id — jamais stocké en clair."""
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    # Un compte sans hash (provisionné par OIDC) ne peut pas se connecter en local.
    if password_hash is None:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def waste_time_like_a_verification() -> None:
    """Consomme le même temps qu'une vraie vérification (anti-énumération)."""
    try:
        _hasher.verify(_DUMMY_HASH, "not-the-dummy-password")
    except VerifyMismatchError:
        pass
