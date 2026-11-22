from __future__ import annotations

import base64

import httpx

from ossie_studio.forges.base import ForgeError, ForgeFormSpec, ForgeIdentity, ForgeRepo, WriteCheck

_API_VERSION = "7.0"
# connectionData (identité du jeton) n'existe qu'en préversion : sans le
# suffixe « -preview », Azure répond 400 (« version under preview »).
_CONNECTION_DATA_VERSION = "7.1-preview.1"
_TOP = 100
_CONTINUATION_HEADER = "x-ms-continuationtoken"


def _headers(token: str) -> dict[str, str]:
    # Azure DevOps : authentification Basic, PAT en mot de passe, login vide.
    encoded = base64.b64encode(f":{token}".encode()).decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


def _classify(status: int) -> None:
    # 203 et 3xx : Azure renvoie vers sa page de connexion quand le jeton est
    # refusé (ou absent) plutôt qu'un 401.
    if status in (401, 403, 203) or 300 <= status < 400:
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
                params={"api-version": _CONNECTION_DATA_VERSION},
            )
        except httpx.HTTPError as exc:
            raise ForgeError("connection_url_unreachable") from exc
        _classify(response.status_code)
        try:
            user = response.json().get("authenticatedUser", {})
        except ValueError:
            raise ForgeError("connection_token_invalid") from None
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

    async def list_branches(
        self, client: httpx.AsyncClient, url: str, token: str, repo: str
    ) -> list[str]:
        org = url.rstrip("/")
        # full_name d'un dépôt Azure = "projet/depot".
        project, _, repo_name = repo.partition("/")
        if not project or not repo_name:
            raise ForgeError("connection_url_unreachable")
        response = await client.get(
            f"{org}/{project}/_apis/git/repositories/{repo_name}/refs",
            headers=_headers(token),
            params={"filter": "heads", "api-version": _API_VERSION},
        )
        _classify(response.status_code)
        names: list[str] = []
        for ref in response.json().get("value", []):
            name = str(ref.get("name", ""))
            if name.startswith("refs/heads/"):
                names.append(name.removeprefix("refs/heads/"))
        return names

    async def default_branch(
        self, client: httpx.AsyncClient, url: str, token: str, repo: str
    ) -> str | None:
        # Simple confort d'affichage (présélection) : toute erreur → None.
        project, _, repo_name = repo.partition("/")
        if not project or not repo_name:
            return None
        try:
            response = await client.get(
                f"{url.rstrip('/')}/{project}/_apis/git/repositories/{repo_name}",
                headers=_headers(token),
                params={"api-version": _API_VERSION},
            )
        except httpx.HTTPError:
            return None
        if response.status_code >= 400:
            return None
        value = response.json().get("defaultBranch")
        return str(value).removeprefix("refs/heads/") if value else None

    async def verify_write(
        self, client: httpx.AsyncClient, url: str, repo: str, token: str
    ) -> WriteCheck:
        # Le droit de pousser d'un PAT Azure DevOps ne se lit pas simplement par
        # l'API : on rend l'identité et « non vérifiable », jamais un oui deviné.
        identity = await self.verify(client, url, token)
        return WriteCheck(login=identity.username, can_write=None)

    def git_username(self) -> str:
        return "pat"

    def git_clone_url(self, base_url: str, repo: str) -> str:
        # full_name d'un dépôt Azure = "projet/depot" → .../projet/_git/depot
        project, _, name = repo.partition("/")
        return f"{base_url.rstrip('/')}/{project}/_git/{name or project}"

    def form_spec(self) -> ForgeFormSpec:
        # Pas de défaut : l'URL de l'organisation (https://dev.azure.com/<org>)
        # est propre à chaque client, donc obligatoire.
        return ForgeFormSpec(forge="azure_devops", default_url="", url_required=True)
