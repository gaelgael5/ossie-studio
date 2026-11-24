from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from authlib.oidc.core import CodeIDToken
from joserfc import jwt
from joserfc.jwk import KeySet

from ossie_studio.oidc.claims import VerifiedClaims

_DISCOVERY_SUFFIX = "/.well-known/openid-configuration"


class DiscoveryError(Exception):
    """La découverte OIDC de l'issuer a échoué ou est incohérente."""


class TokenExchangeError(Exception):
    """L'échange du code d'autorisation a échoué."""


class IdTokenInvalidError(Exception):
    """L'id_token est absent ou n'a pas passé la validation."""


@dataclass(frozen=True)
class ProviderMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    id_token_signing_alg_values_supported: list[str]


def generate_pkce_pair() -> tuple[str, str]:
    """Rend (code_verifier, code_challenge) pour PKCE S256."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


async def discover(client: httpx.AsyncClient, issuer: str) -> ProviderMetadata:
    """Interroge la découverte OIDC. L'issuer annoncé doit être exactement
    celui demandé, sinon la validation d'`iss` échouerait plus tard."""
    url = issuer.rstrip("/") + _DISCOVERY_SUFFIX
    try:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise DiscoveryError(f"Issuer injoignable ou invalide : {issuer}") from exc
    if data.get("issuer") != issuer:
        raise DiscoveryError(
            "L'issuer annoncé par la découverte ne correspond pas à l'issuer saisi."
        )
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not data.get(key):
            raise DiscoveryError(f"La découverte OIDC ne déclare pas {key}.")
    return ProviderMetadata(
        issuer=data["issuer"],
        authorization_endpoint=data["authorization_endpoint"],
        token_endpoint=data["token_endpoint"],
        jwks_uri=data["jwks_uri"],
        id_token_signing_alg_values_supported=data.get(
            "id_token_signing_alg_values_supported", ["RS256"]
        ),
    )


def build_authorization_url(
    metadata: ProviderMetadata,
    client_id: str,
    redirect_uri: str,
    scopes: str,
    state: str,
    nonce: str,
    code_challenge: str,
    login_hint: str = "",
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{metadata.authorization_endpoint}?{urlencode(params)}"


async def exchange_code(
    client: httpx.AsyncClient,
    metadata: ProviderMetadata,
    *,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    code_verifier: str,
) -> dict[str, object]:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
        "code_verifier": code_verifier,
    }
    try:
        response = await client.post(metadata.token_endpoint, data=data)
        response.raise_for_status()
        payload: dict[str, object] = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise TokenExchangeError("Échange du code d'autorisation refusé.") from exc
    return payload


async def validate_id_token(
    client: httpx.AsyncClient,
    metadata: ProviderMetadata,
    token_response: dict[str, object],
    *,
    client_id: str,
    nonce: str,
) -> VerifiedClaims:
    """Valide signature (JWKS), iss, aud, exp et nonce, puis extrait les claims."""
    id_token = token_response.get("id_token")
    if not isinstance(id_token, str):
        raise IdTokenInvalidError("Réponse de jeton sans id_token.")
    try:
        jwks_response = await client.get(metadata.jwks_uri)
        jwks_response.raise_for_status()
        key_set = KeySet.import_key_set(jwks_response.json())
    except (httpx.HTTPError, ValueError) as exc:
        raise IdTokenInvalidError("JWKS de l'émetteur injoignable.") from exc

    try:
        decoded = jwt.decode(
            id_token,
            key_set,
            algorithms=metadata.id_token_signing_alg_values_supported,
        )
    except Exception as exc:  # joserfc lève divers types selon la cause de rejet
        raise IdTokenInvalidError("Signature de l'id_token invalide.") from exc

    claims_params = {"nonce": nonce, "client_id": client_id}
    access_token = token_response.get("access_token")
    if isinstance(access_token, str):
        claims_params["access_token"] = access_token
    claims = CodeIDToken(
        decoded.claims, decoded.header, {"iss": {"values": [metadata.issuer]}}, claims_params
    )
    try:
        claims.validate()
    except Exception as exc:
        raise IdTokenInvalidError("Claims de l'id_token invalides (iss/aud/exp/nonce).") from exc

    return _extract_claims(decoded.claims)


def _extract_claims(raw: dict[str, object]) -> VerifiedClaims:
    email = raw.get("email")
    name = raw.get("name") or raw.get("preferred_username")
    return VerifiedClaims(
        subject=str(raw["sub"]),
        email=email if isinstance(email, str) and email else None,
        email_verified=raw.get("email_verified") is True,
        name=name if isinstance(name, str) and name.strip() else None,
        raw=dict(raw),
    )
