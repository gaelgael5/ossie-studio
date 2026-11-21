from __future__ import annotations

from urllib.parse import quote

import httpx

from ossie_studio.forges.base import ForgeError, ForgeFormSpec, ForgeIdentity, ForgeRepo, WriteCheck

_PER_PAGE = 100


def _api(url: str) -> str:
    return f"{url.rstrip('/')}/api/v4"


def _headers(token: str) -> dict[str, str]:
    # GitLab accepte le PAT dans l'en-tête PRIVATE-TOKEN.
    return {"PRIVATE-TOKEN": token}


# Niveau d'accès GitLab à partir duquel on peut pousser (Developer).
_DEVELOPER_ACCESS = 30


class GitLabStrategy:
    async def verify(self, client: httpx.AsyncClient, url: str, token: str) -> ForgeIdentity:
        try:
            response = await client.get(f"{_api(url)}/user", headers=_headers(token))
        except httpx.HTTPError as exc:
            raise ForgeError("connection_url_unreachable") from exc
        if response.status_code == 403:
            # Jeton reconnu mais portée insuffisante (GitLab : « insufficient_scope ») :
            # à distinguer d'un jeton expiré, sinon on cherche au mauvais endroit.
            raise ForgeError("connection_token_scope_insufficient")
        if response.status_code == 401:
            raise ForgeError("connection_token_invalid")
        if response.status_code >= 400:
            raise ForgeError("connection_url_unreachable")
        return ForgeIdentity(username=str(response.json()["username"]))

    async def list_repositories(
        self, client: httpx.AsyncClient, url: str, token: str
    ) -> list[ForgeRepo]:
        repos: list[ForgeRepo] = []
        page = 1
        while True:
            response = await client.get(
                f"{_api(url)}/projects",
                headers=_headers(token),
                params={"membership": "true", "per_page": _PER_PAGE, "page": page},
            )
            if response.status_code >= 400:
                raise ForgeError("connection_token_invalid")
            batch = response.json()
            repos.extend(
                ForgeRepo(full_name=p["path_with_namespace"], url=p["web_url"]) for p in batch
            )
            if len(batch) < _PER_PAGE:
                return repos
            page += 1

    async def list_branches(
        self, client: httpx.AsyncClient, url: str, token: str, repo: str
    ) -> list[str]:
        encoded = quote(repo, safe="")
        names: list[str] = []
        page = 1
        while True:
            response = await client.get(
                f"{_api(url)}/projects/{encoded}/repository/branches",
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

    async def verify_write(
        self, client: httpx.AsyncClient, url: str, repo: str, token: str
    ) -> WriteCheck:
        identity = await self.verify(client, url, token)
        try:
            response = await client.get(
                f"{_api(url)}/projects/{quote(repo, safe='')}", headers=_headers(token)
            )
        except httpx.HTTPError as exc:
            raise ForgeError("connection_url_unreachable") from exc
        if response.status_code == 404:
            return WriteCheck(login=identity.username, can_write=False)
        if response.status_code >= 400:
            raise ForgeError("connection_url_unreachable")
        permissions = response.json().get("permissions") or {}
        levels = [
            (permissions.get(key) or {}).get("access_level") or 0
            for key in ("project_access", "group_access")
        ]
        return WriteCheck(login=identity.username, can_write=max(levels) >= _DEVELOPER_ACCESS)

    def git_username(self) -> str:
        return "oauth2"

    def git_clone_url(self, base_url: str, repo: str) -> str:
        return f"{base_url.rstrip('/')}/{repo}.git"

    def form_spec(self) -> ForgeFormSpec:
        return ForgeFormSpec(forge="gitlab", default_url="https://gitlab.com", url_required=False)
