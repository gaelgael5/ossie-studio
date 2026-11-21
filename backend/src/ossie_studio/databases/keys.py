"""Ouverture d'une clé privée Snowflake.

La phrase de passe ne sert qu'à **ouvrir** la clé au moment de la création du
secret ; c'est la clé **ouverte** qui est stockée (chiffrée par la table des
secrets). La phrase de passe n'est conservée nulle part : elle n'existe que le
temps de cet appel. Clé invalide ou mauvaise phrase de passe → refus, rien n'est
créé.
"""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization

from ossie_studio.databases.base import DbEngineError


def open_private_key(pem: bytes, passphrase: str) -> str:
    """Charge une clé privée PEM (éventuellement chiffrée) et la rend ouverte, en
    PKCS#8 non chiffré. Lève DbEngineError('db_key_rejected') si la clé n'en est
    pas une ou si la phrase de passe est fausse."""
    password = passphrase.encode() if passphrase else None
    try:
        key = serialization.load_pem_private_key(pem, password=password)
    except (ValueError, TypeError) as exc:
        # Mauvaise phrase de passe, clé non chiffrée avec phrase fournie, ou
        # contenu qui n'est pas une clé privée.
        raise DbEngineError("db_key_rejected") from exc
    opened = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return opened.decode()
