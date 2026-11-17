from __future__ import annotations

import pytest

from ossie_studio.crypto import CryptoService, DecryptionFailedError, SecretValue

KEY_A = bytes.fromhex("aa" * 32)
KEY_B = bytes.fromhex("bb" * 32)


def test_chiffrement_dechiffrement_rend_la_valeur() -> None:
    crypto = CryptoService(KEY_A)
    blob = crypto.encrypt(SecretValue("ghp_jeton-tres-secret"))
    assert crypto.decrypt(blob).reveal() == "ghp_jeton-tres-secret"


def test_valeur_claire_absente_du_chiffre() -> None:
    crypto = CryptoService(KEY_A)
    blob = crypto.encrypt(SecretValue("ghp_jeton-tres-secret"))
    assert b"ghp_jeton-tres-secret" not in blob


def test_rechiffrer_la_meme_valeur_produit_un_chiffre_different() -> None:
    crypto = CryptoService(KEY_A)
    value = SecretValue("meme-valeur")
    assert crypto.encrypt(value) != crypto.encrypt(value)


def test_autre_clef_maitre_dechiffrement_refuse() -> None:
    blob = CryptoService(KEY_A).encrypt(SecretValue("valeur"))
    with pytest.raises(DecryptionFailedError):
        CryptoService(KEY_B).decrypt(blob)


def test_donnee_alteree_dechiffrement_refuse() -> None:
    crypto = CryptoService(KEY_A)
    blob = bytearray(crypto.encrypt(SecretValue("valeur")))
    blob[-1] ^= 0xFF
    with pytest.raises(DecryptionFailedError):
        crypto.decrypt(bytes(blob))


def test_clef_de_mauvaise_taille_refusee() -> None:
    with pytest.raises(ValueError, match="32 octets"):
        CryptoService(b"trop-courte")


def test_type_opaque_masque_la_valeur() -> None:
    value = SecretValue("ultra-secret")
    assert "ultra-secret" not in repr(value)
    assert "ultra-secret" not in str(value)
    assert "ultra-secret" not in f"valeur: {value}"
    assert value.reveal() == "ultra-secret"


def test_type_opaque_comparaison_sans_exposition() -> None:
    assert SecretValue("a") == SecretValue("a")
    assert SecretValue("a") != SecretValue("b")
    assert hash(SecretValue("a")) == hash(SecretValue("a"))
