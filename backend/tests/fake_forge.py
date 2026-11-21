from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

GOOD_TOKEN = "ghp_jeton-valide-discriminant"
GITHUB_LOGIN = "octocat"
# Jeton valide d'un autre compte, sans droit d'écriture sur les dépôts.
READONLY_TOKEN = "ghp_jeton-lecture-seule"
READONLY_LOGIN = "lecteur"


def _authorized(request: Request) -> bool:
    return request.headers.get("Authorization") == f"Bearer {GOOD_TOKEN}"


def _login(request: Request) -> str | None:
    header = request.headers.get("Authorization")
    if header == f"Bearer {GOOD_TOKEN}":
        return GITHUB_LOGIN
    if header == f"Bearer {READONLY_TOKEN}":
        return READONLY_LOGIN
    return None


async def _user(request: Request) -> Response:
    login = _login(request)
    if login is None:
        return JSONResponse({"message": "Bad credentials"}, status_code=401)
    return JSONResponse({"login": login})


async def _repo(request: Request) -> Response:
    login = _login(request)
    if login is None:
        return JSONResponse({"message": "Bad credentials"}, status_code=401)
    full_name = f"{request.path_params['owner']}/{request.path_params['repo']}"
    return JSONResponse(
        {"full_name": full_name, "permissions": {"pull": True, "push": login == GITHUB_LOGIN}}
    )


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


async def _branches(request: Request) -> Response:
    if not _authorized(request):
        return JSONResponse({"message": "Bad credentials"}, status_code=401)
    return JSONResponse([{"name": "main"}, {"name": "develop"}])


def fake_github_app() -> Starlette:
    """Faux GitHub pour les tests de l'API des connexions et des sources."""
    return Starlette(
        routes=[
            Route("/user", _user),
            Route("/user/repos", _repos),
            Route("/repos/{owner}/{repo}/branches", _branches),
            Route("/repos/{owner}/{repo}", _repo),
        ]
    )
