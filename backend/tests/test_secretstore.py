from __future__ import annotations

from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from ossie_studio.app import create_app
from ossie_studio.crypto import CryptoService, DecryptionFailedError, SecretValue
from ossie_studio.models import Secret
from ossie_studio.secretstore.service import (
    SecretNotFoundError,
    db_create_secret,
    resolve_secret_value,
)
from tests.conftest import OTHER_MASTER_KEY, bootstrap_admin, make_settings

VALEUR = "ghp_un-jeton-github-discriminant"


async def _create_secret_via_db(app: FastAPI, slug: str = "github-ci") -> None:
    crypto: CryptoService = app.state.crypto
    async with app.state.session_factory() as session:
        await db_create_secret(
            session, slug, "Jeton CI", "github", crypto.encrypt(SecretValue(VALEUR))
        )


async def test_resolve_rend_la_valeur_sous_type_opaque(app: FastAPI) -> None:
    await _create_secret_via_db(app)
    async with app.state.session_factory() as session:
        value = await resolve_secret_value(session, app.state.crypto, "github-ci")
    assert str(value) == "******"
    assert value.reveal() == VALEUR


async def test_resolve_slug_inconnu_erreur_explicite(app: FastAPI) -> None:
    async with app.state.session_factory() as session:
        with pytest.raises(SecretNotFoundError, match="inconnu-slug"):
            await resolve_secret_value(session, app.state.crypto, "inconnu-slug")


async def test_base_copiee_autre_clef_erreur_claire_sans_plantage(tmp_path: Path) -> None:
    """La même base lue avec une autre clef maître refuse le déchiffrement."""
    first_app = create_app(make_settings(tmp_path))
    async with LifespanManager(first_app):
        await _create_secret_via_db(first_app)

    second_app = create_app(make_settings(tmp_path, master_key=OTHER_MASTER_KEY))
    async with LifespanManager(second_app):
        async with second_app.state.session_factory() as session:
            with pytest.raises(DecryptionFailedError, match="clef maître"):
                await resolve_secret_value(session, second_app.state.crypto, "github-ci")
        # Le service reste opérationnel : la liste (sans valeur) répond.
        transport = ASGITransport(app=second_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await bootstrap_admin(client)
            assert (await client.get("/secrets")).status_code == 200


async def test_erreur_de_dechiffrement_aucune_valeur_dans_les_logs(
    app: FastAPI, capfd: pytest.CaptureFixture[str]
) -> None:
    """Provoque l'erreur de déchiffrement et inspecte ce qui a été journalisé."""
    await _create_secret_via_db(app)
    async with app.state.session_factory() as session:
        secret = (await session.execute(select(Secret))).scalar_one()
        original_blob = secret.value_encrypted
        tampered = bytearray(original_blob)
        tampered[-1] ^= 0xFF
        secret.value_encrypted = bytes(tampered)
        await session.commit()
    async with app.state.session_factory() as session:
        with pytest.raises(DecryptionFailedError):
            await resolve_secret_value(session, app.state.crypto, "github-ci")
    captured = capfd.readouterr()
    logs = captured.out + captured.err
    assert "secret_decrypt_failed" in logs
    assert VALEUR not in logs
    assert original_blob.hex() not in logs
