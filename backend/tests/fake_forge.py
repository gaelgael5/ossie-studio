from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

GOOD_TOKEN = "ghp_jeton-valide-discriminant"
GITHUB_LOGIN = "octocat"


def _authorized(request: Request) -> bool:
    return request.headers.get("Authorization") == f"Bearer {GOOD_TOKEN}"


async def _user(request: Request) -> Response:
    if not _authorized(request):
        return JSONResponse({"message": "Bad credentials"}, status_code=401)
    return JSONResponse({"login": GITHUB_LOGIN})


async def _repos(request: Request) -> Response:
    if not _authorized(request):
        return JSONResponse({"message": "Bad credentials"}, status_code=401)
    page = int(request.query_params.get("page", "1"))
    items = (
        [{"full_name": f"{GITHUB_LOGIN}/r{i}", "html_url": f"https://x/{i}"} for i in range(100)]
        if page == 1
        else [{"full_name": f"{GITHUB_LOGIN}/last", "html_url": "https://x/last"}]
    )
    return JSONResponse(items)


def fake_github_app() -> Starlette:
    """Faux GitHub pour les tests de l'API des connexions."""
    return Starlette(routes=[Route("/user", _user), Route("/user/repos", _repos)])
