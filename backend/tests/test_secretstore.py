from __future__ import annotations

from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.app import create_app
from ossie_studio.crypto import CryptoService, DecryptionFailedError, SecretValue
from ossie_studio.secretstore.service import (
    SecretNotFoundError,
    db_create_secret,
    resolve_secret_value,
)
from tests.conftest import (
    OTHER_MASTER_KEY,
    bootstrap_admin,
    db_fetchrow,
    db_fetchval,
    make_settings,
)

VALEUR = "ghp_un-jeton-github-discriminant"


async def _create_secret_via_db(app: FastAPI, slug: str = "github-ci") -> None:
    crypto: CryptoService = app.state.crypto
    pool = app.state.pool
    async with pool.acquire() as conn:
        await db_create_secret(
            conn, slug, "Jeton CI", "github", crypto.encrypt(SecretValue(VALEUR))
        )


async def test_resolve_rend_la_valeur_sous_type_opaque(app: FastAPI) -> None:
    await _create_secret_via_db(app)
    pool = app.state.pool
    async with pool.acquire() as conn:
        value = await resolve_secret_value(conn, app.state.crypto, "github-ci")
    assert str(value) == "******"
    assert value.reveal() == VALEUR


async def test_resolve_slug_inconnu_erreur_explicite(app: FastAPI) -> None:
    pool = app.state.pool
    async with pool.acquire() as conn:
        with pytest.raises(SecretNotFoundError, match="inconnu-slug"):
            await resolve_secret_value(conn, app.state.crypto, "inconnu-slug")


async def test_base_copiee_autre_clef_erreur_claire_sans_plantage(
    tmp_path: Path, test_database: str
) -> None:
    """La même base lue avec une autre clef maître refuse le déchiffrement."""
    first_app = create_app(make_settings(tmp_path, test_database))
    async with LifespanManager(first_app):
        await _create_secret_via_db(first_app)

    second_settings = make_settings(tmp_path / "b", test_database, master_key=OTHER_MASTER_KEY)
    second_app = create_app(second_settings)
    async with LifespanManager(second_app):
        pool = second_app.state.pool
        async with pool.acquire() as conn:
            with pytest.raises(DecryptionFailedError, match="clef maître"):
                await resolve_secret_value(conn, second_app.state.crypto, "github-ci")
        transport = ASGITransport(app=second_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await bootstrap_admin(client)
            assert (await client.get("/api/secrets")).status_code == 200


async def test_erreur_de_dechiffrement_aucune_valeur_dans_les_logs(
    app: FastAPI, capfd: pytest.CaptureFixture[str]
) -> None:
    """Provoque l'erreur de déchiffrement et inspecte ce qui a été journalisé."""
    await _create_secret_via_db(app)
    original = await db_fetchval(app, "SELECT value_encrypted FROM secrets")
    assert isinstance(original, bytes)
    tampered = bytearray(original)
    tampered[-1] ^= 0xFF
    pool = app.state.pool
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE secrets SET value_encrypted = $1 WHERE slug = $2", bytes(tampered), "github-ci"
        )
        with pytest.raises(DecryptionFailedError):
            await resolve_secret_value(conn, app.state.crypto, "github-ci")
    captured = capfd.readouterr()
    logs = captured.out + captured.err
    assert "secret_decrypt_failed" in logs
    assert VALEUR not in logs
    assert original.hex() not in logs
    _ = await db_fetchrow(app, "SELECT 1")  # garde la connexion saine
