from __future__ import annotations

import httpx

from ossie_studio.forges.base import ForgeError, ForgeFormSpec, ForgeIdentity, ForgeRepo

_PER_PAGE = 100
_GITHUB_COM = "https://github.com"


def _api_base(url: str) -> str:
    # github.com → api.github.com ; une instance GitHub Enterprise → <url>/api/v3.
    normalized = url.rstrip("/")
    if normalized in (_GITHUB_COM, "https://www.github.com"):
        return "https://api.github.com"
    return f"{normalized}/api/v3"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


class GitHubStrategy:
    async def verify(self, client: httpx.AsyncClient, url: str, token: str) -> ForgeIdentity:
        try:
            response = await client.get(f"{_api_base(url)}/user", headers=_headers(token))
        except httpx.HTTPError as exc:
            raise ForgeError("connection_url_unreachable") from exc
        if response.status_code in (401, 403):
            raise ForgeError("connection_token_invalid")
        if response.status_code >= 400:
            raise ForgeError("connection_url_unreachable")
        return ForgeIdentity(username=str(response.json()["login"]))

    async def list_repositories(
        self, client: httpx.AsyncClient, url: str, token: str
    ) -> list[ForgeRepo]:
        repos: list[ForgeRepo] = []
        page = 1
        while True:
            response = await client.get(
                f"{_api_base(url)}/user/repos",
                headers=_headers(token),
                params={
                    "per_page": _PER_PAGE,
                    "page": page,
                    "affiliation": "owner,collaborator,organization_member",
                },
            )
            if response.status_code >= 400:
                raise ForgeError("connection_token_invalid")
            batch = response.json()
            repos.extend(ForgeRepo(full_name=r["full_name"], url=r["html_url"]) for r in batch)
            if len(batch) < _PER_PAGE:
                return repos
            page += 1

    async def list_branches(
        self, client: httpx.AsyncClient, url: str, token: str, repo: str
    ) -> list[str]:
        names: list[str] = []
        page = 1
        while True:
            response = await client.get(
                f"{_api_base(url)}/repos/{repo}/branches",
                headers=_headers(token),
                params={"per_page": _PER_PAGE, "page": page},
            )
            if response.status_code >= 400:
                raise ForgeError("connection_token_invalid")
            batch = response.json()
            names.extend(str(b["name"]) for b in batch)
            if len(batch) < _PER_PAGE:
                return names
            page += 1

    def git_username(self) -> str:
        return "x-access-token"

    def form_spec(self) -> ForgeFormSpec:
        return ForgeFormSpec(forge="github", default_url=_GITHUB_COM, url_required=False)
