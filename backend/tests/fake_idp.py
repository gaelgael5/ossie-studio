from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode, urlparse

from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route


@dataclass
class NextUser:
    sub: str
    email: str | None
    email_verified: bool


@dataclass
class FakeIdP:
    """IdP OIDC minimal pour les tests : découverte, JWKS, autorisation, jeton.

    `set_next_user` fixe l'identité que la prochaine autorisation renverra, de
    sorte qu'un test pilote entièrement qui se connecte.
    """

    issuer: str = "http://idp.test/realms/test"
    key: RSAKey = field(default_factory=lambda: RSAKey.generate_key(2048, parameters={"kid": "k1"}))
    next_user: NextUser | None = None
    # code -> claims à émettre dans l'id_token
    _pending: dict[str, dict[str, object]] = field(default_factory=dict)
    sign_issuer: str | None = None  # surcharge l'iss signé (pour tester un iss incohérent)

    def set_next_user(self, sub: str, email: str | None, email_verified: bool) -> None:
        self.next_user = NextUser(sub, email, email_verified)

    def _discovery(self, _: Request) -> Response:
        base = "http://idp.test"
        return JSONResponse(
            {
                "issuer": self.issuer,
                "authorization_endpoint": f"{base}/authorize",
                "token_endpoint": f"{base}/token",
                "jwks_uri": f"{base}/jwks",
                "id_token_signing_alg_values_supported": ["RS256"],
            }
        )

    def _jwks(self, _: Request) -> Response:
        return JSONResponse(KeySet([self.key]).as_dict(private=False))

    async def _authorize(self, request: Request) -> Response:
        params = request.query_params
        assert self.next_user is not None, "set_next_user doit être appelé avant /authorize"
        code = uuid.uuid4().hex
        self._pending[code] = {
            "sub": self.next_user.sub,
            "email": self.next_user.email,
            "email_verified": self.next_user.email_verified,
            "nonce": params.get("nonce", ""),
            "aud": params.get("client_id", ""),
        }
        location = (
            params["redirect_uri"]
            + "?"
            + urlencode({"code": code, "state": params.get("state", "")})
        )
        return RedirectResponse(location, status_code=303)

    async def _token(self, request: Request) -> Response:
        form = await request.form()
        code = str(form.get("code", ""))
        data = self._pending.pop(code, None)
        if data is None:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        now = int(time.time())
        claims: dict[str, object] = {
            "iss": self.sign_issuer or self.issuer,
            "sub": data["sub"],
            "aud": data["aud"],
            "exp": now + 300,
            "iat": now,
            "nonce": data["nonce"],
        }
        if data["email"] is not None:
            claims["email"] = data["email"]
            claims["email_verified"] = data["email_verified"]
        id_token = jwt.encode({"alg": "RS256", "kid": self.key.kid}, claims, self.key)
        return JSONResponse(
            {"access_token": "fake-access", "token_type": "Bearer", "id_token": id_token}
        )

    def app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/realms/test/.well-known/openid-configuration", self._discovery),
                Route("/jwks", self._jwks),
                Route("/authorize", self._authorize),
                Route("/token", self._token, methods=["POST"]),
            ]
        )


def split_query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
