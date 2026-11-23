from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.errors import ERROR_CODES, ApiError
from ossie_studio.parsers.conventions import load_schema

_FRONTEND_CODES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "errorCodes.json"


def test_api_error_refuse_un_code_inconnu() -> None:
    with pytest.raises(ValueError, match="inconnu"):
        ApiError("pas_un_code")


def test_codes_backend_et_frontend_ne_divergent_pas() -> None:
    """La liste des codes est partagée : le JSON du front doit refléter le backend."""
    frontend_codes = load_schema(_FRONTEND_CODES.read_text(encoding="utf-8"))
    assert sorted(frontend_codes) == sorted(ERROR_CODES)


async def test_handler_emet_code_et_args_sans_message_redige() -> None:
    app = FastAPI()
    from ossie_studio.errors import api_error_handler

    app.add_exception_handler(ApiError, api_error_handler)

    @app.get("/boom")
    async def boom() -> None:
        raise ApiError("oidc_issuer_unreachable", status_code=400, issuer="https://x")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")
    assert response.status_code == 400
    body = response.json()
    assert body == {"error": "oidc_issuer_unreachable", "error_args": {"issuer": "https://x"}}
