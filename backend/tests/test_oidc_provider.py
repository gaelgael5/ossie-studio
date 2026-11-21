from __future__ import annotations

import httpx
import pytest

from ossie_studio.oidc.provider import (
    DiscoveryError,
    IdTokenInvalidError,
    ProviderMetadata,
    discover,
    generate_pkce_pair,
    validate_id_token,
)

ISSUER = "http://idp.test/realms/test"


def _client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


async def test_discover_issuer_annonce_incoherent_refuse() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"issuer": "http://autre.test", "token_endpoint": "x"})

    async with _client(handler) as client:
        with pytest.raises(DiscoveryError, match="ne correspond pas"):
            await discover(client, ISSUER)


async def test_discover_endpoint_manquant_refuse() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"issuer": ISSUER, "authorization_endpoint": "x"})

    async with _client(handler) as client:
        with pytest.raises(DiscoveryError, match="token_endpoint"):
            await discover(client, ISSUER)


async def test_discover_injoignable_refuse() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with _client(handler) as client:
        with pytest.raises(DiscoveryError, match="injoignable"):
            await discover(client, ISSUER)


async def test_validate_id_token_absent_refuse() -> None:
    metadata = ProviderMetadata(ISSUER, "a", "t", "j", ["RS256"])
    async with _client(lambda _: httpx.Response(200)) as client:
        with pytest.raises(IdTokenInvalidError, match="sans id_token"):
            await validate_id_token(client, metadata, {}, client_id="c", nonce="n")


async def test_validate_id_token_jwks_injoignable_refuse() -> None:
    metadata = ProviderMetadata(ISSUER, "a", "t", "http://idp.test/jwks", ["RS256"])

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with _client(handler) as client:
        with pytest.raises(IdTokenInvalidError, match="JWKS"):
            await validate_id_token(
                client, metadata, {"id_token": "x.y.z"}, client_id="c", nonce="n"
            )


def test_pkce_pair_challenge_derive_du_verifier() -> None:
    verifier, challenge = generate_pkce_pair()
    assert verifier and challenge
    # Rejouer doit donner un couple différent (verifier aléatoire).
    assert generate_pkce_pair()[0] != verifier
