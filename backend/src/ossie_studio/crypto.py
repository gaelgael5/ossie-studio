from __future__ import annotations

import hmac
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MASTER_KEY_BYTES = 32
_NONCE_BYTES = 12


class DecryptionFailedError(Exception):
    """Déchiffrement refusé : clef maître différente ou donnée altérée."""

    def __init__(self) -> None:
        super().__init__(
            "Déchiffrement impossible : la clef maître ne correspond pas à celle "
            "qui a chiffré cette valeur, ou la donnée a été altérée."
        )


class SecretValue:
    """Type opaque : l'affichage montre des astérisques, seule la méthode
    explicite `reveal()` donne la valeur claire — à appeler au dernier moment,
    au point d'injection."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretValue('******')"

    def __str__(self) -> str:
        return "******"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SecretValue):
            return NotImplemented
        return hmac.compare_digest(self._value.encode(), other._value.encode())

    def __hash__(self) -> int:
        return hash(self._value)


class CryptoService:
    """Chiffrement authentifié (AES-256-GCM) des colonnes sensibles.

    Chaque écriture tire un nonce neuf : rechiffrer la même valeur produit
    toujours un chiffré différent.
    """

    def __init__(self, master_key: bytes) -> None:
        if len(master_key) != MASTER_KEY_BYTES:
            msg = f"La clef maître doit faire {MASTER_KEY_BYTES} octets"
            raise ValueError(msg)
        self._aesgcm = AESGCM(master_key)

    def encrypt(self, value: SecretValue) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        return nonce + self._aesgcm.encrypt(nonce, value.reveal().encode(), None)

    def decrypt(self, blob: bytes) -> SecretValue:
        nonce, ciphertext = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        try:
            plaintext = self._aesgcm.decrypt(nonce, ciphertext, None)
        except InvalidTag as exc:
            raise DecryptionFailedError from exc
        return SecretValue(plaintext.decode())
