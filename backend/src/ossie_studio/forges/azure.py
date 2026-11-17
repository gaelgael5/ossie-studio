from __future__ import annotations

import base64

import httpx

from ossie_studio.forges.base import ForgeError, ForgeIdentity, ForgeRepo

_API_VERSION = "7.0"
_TOP = 100
_CONTINUATION_HEADER = "x-ms-continuationtoken"


def _headers(token: str) -> dict[str, str]:
    # Azure DevOps : authentification Basic, PAT en mot de passe, login vide.
    encoded = base64.b64encode(f":{token}".encode()).decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


def _classify(status: int) -> None:
    if status in (401, 403, 203):
        raise ForgeError("connection_token_invalid")
    if status == 404:
        raise ForgeError("connection_azure_org_not_found")
    if status >= 400:
        raise ForgeError("connection_url_unreachable")


class AzureDevOpsStrategy:
    async def verify(self, client: httpx.AsyncClient, url: str, token: str) -> ForgeIdentity:
        org = url.rstrip("/")
        try:
            response = await client.get(
                f"{org}/_apis/connectionData",
                headers=_headers(token),
                params={"api-version": _API_VERSION},
            )
        except httpx.HTTPError as exc:
            raise ForgeError("connection_url_unreachable") from exc
        _classify(response.status_code)
        user = response.json().get("authenticatedUser", {})
        name = user.get("providerDisplayName") or user.get("customDisplayName")
        if not name:
            raise ForgeError("connection_token_invalid")
        return ForgeIdentity(username=str(name))

    async def list_repositories(
        self, client: httpx.AsyncClient, url: str, token: str
    ) -> list[ForgeRepo]:
        org = url.rstrip("/")
        repos: list[ForgeRepo] = []
        for project in await self._list_projects(client, org, token):
            response = await client.get(
                f"{org}/{project}/_apis/git/repositories",
                headers=_headers(token),
                params={"api-version": _API_VERSION},
            )
            _classify(response.status_code)
            for repo in response.json().get("value", []):
                repos.append(ForgeRepo(full_name=f"{project}/{repo['name']}", url=repo["webUrl"]))
        return repos

    async def _list_projects(self, client: httpx.AsyncClient, org: str, token: str) -> list[str]:
        projects: list[str] = []
        continuation: str | None = None
        while True:
            params: dict[str, str | int] = {"api-version": _API_VERSION, "$top": _TOP}
            if continuation:
                params["continuationToken"] = continuation
            response = await client.get(
                f"{org}/_apis/projects", headers=_headers(token), params=params
            )
            _classify(response.status_code)
            projects.extend(p["name"] for p in response.json().get("value", []))
            continuation = response.headers.get(_CONTINUATION_HEADER)
            if not continuation:
                return projects
