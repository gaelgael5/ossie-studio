from __future__ import annotations

import json

import httpx
import pytest

from ossie_studio.forges.azure import AzureDevOpsStrategy
from ossie_studio.forges.base import ForgeError
from ossie_studio.forges.github import GitHubStrategy
from ossie_studio.forges.gitlab import GitLabStrategy


def _client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


# --- GitHub ---------------------------------------------------------------


def _github_handler(request: httpx.Request) -> httpx.Response:
    if request.headers.get("Authorization") != "Bearer good":
        return httpx.Response(401)
    if request.url.path == "/user":
        return httpx.Response(200, json={"login": "octocat"})
    if request.url.path == "/user/repos":
        page = int(request.url.params.get("page", "1"))
        items = (
            [{"full_name": f"octocat/repo{i}", "html_url": f"https://x/{i}"} for i in range(100)]
            if page == 1
            else [{"full_name": "octocat/last", "html_url": "https://x/last"}]
        )
        return httpx.Response(200, json=items)
    return httpx.Response(404)


async def test_github_verify_rend_identite() -> None:
    async with _client(_github_handler) as client:
        identity = await GitHubStrategy().verify(client, "https://github.com", "good")
    assert identity.username == "octocat"


async def test_github_jeton_invalide_refuse() -> None:
    async with _client(_github_handler) as client:
        with pytest.raises(ForgeError) as exc:
            await GitHubStrategy().verify(client, "https://github.com", "bad")
    assert exc.value.code == "connection_token_invalid"


async def test_github_liste_gere_la_pagination() -> None:
    async with _client(_github_handler) as client:
        repos = await GitHubStrategy().list_repositories(client, "https://github.com", "good")
    assert len(repos) == 101  # 100 (page 1) + 1 (page 2)


async def test_github_url_injoignable() -> None:
    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async with _client(boom) as client:
        with pytest.raises(ForgeError) as exc:
            await GitHubStrategy().verify(client, "https://ghe.interne", "good")
    assert exc.value.code == "connection_url_unreachable"


# --- GitLab ---------------------------------------------------------------


def _gitlab_handler(request: httpx.Request) -> httpx.Response:
    if request.headers.get("PRIVATE-TOKEN") != "good":
        return httpx.Response(401)
    if request.url.path == "/api/v4/user":
        return httpx.Response(200, json={"username": "gluser"})
    if request.url.path == "/api/v4/projects":
        page = int(request.url.params.get("page", "1"))
        items = (
            [{"path_with_namespace": f"g/p{i}", "web_url": f"https://g/{i}"} for i in range(100)]
            if page == 1
            else []
        )
        return httpx.Response(200, json=items)
    return httpx.Response(404)


async def test_gitlab_verify_rend_identite() -> None:
    async with _client(_gitlab_handler) as client:
        identity = await GitLabStrategy().verify(client, "https://gitlab.com", "good")
    assert identity.username == "gluser"


async def test_gitlab_liste_paginee() -> None:
    async with _client(_gitlab_handler) as client:
        repos = await GitLabStrategy().list_repositories(client, "https://gitlab.com", "good")
    assert len(repos) == 100


# --- Azure DevOps ---------------------------------------------------------


def _azure_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if "/orga-absente/" in path:
        return httpx.Response(404)
    if request.headers.get("Authorization", "").startswith("Basic ") is False:
        return httpx.Response(401)
    if path.endswith("/_apis/connectionData"):
        return httpx.Response(200, json={"authenticatedUser": {"providerDisplayName": "Az User"}})
    if path.endswith("/_apis/projects"):
        return httpx.Response(200, json={"value": [{"name": "Proj"}]})
    if path.endswith("/_apis/git/repositories"):
        return httpx.Response(200, json={"value": [{"name": "repo1", "webUrl": "https://az/r1"}]})
    return httpx.Response(404)


async def test_azure_verify_rend_identite() -> None:
    async with _client(_azure_handler) as client:
        identity = await AzureDevOpsStrategy().verify(client, "https://dev.azure.com/orga", "good")
    assert identity.username == "Az User"


async def test_azure_organisation_inexistante() -> None:
    async with _client(_azure_handler) as client:
        with pytest.raises(ForgeError) as exc:
            await AzureDevOpsStrategy().verify(client, "https://dev.azure.com/orga-absente", "good")
    assert exc.value.code == "connection_azure_org_not_found"


async def test_azure_liste_projets_puis_depots() -> None:
    async with _client(_azure_handler) as client:
        repos = await AzureDevOpsStrategy().list_repositories(
            client, "https://dev.azure.com/orga", "good"
        )
    assert [r.full_name for r in repos] == ["Proj/repo1"]


def test_erreurs_de_forge_ne_portent_jamais_le_jeton() -> None:
    # Le message d'une ForgeError est le code seul, jamais une valeur sensible.
    err = ForgeError("connection_token_invalid")
    assert json.dumps(str(err)) == '"connection_token_invalid"'


# --- Droit d'écriture d'un jeton personnel sur un dépôt -------------------------


def test_repo_path_depuis_l_url_de_clone() -> None:
    from ossie_studio.forges.base import repo_path

    assert repo_path("https://github.com", "https://github.com/octocat/r.git") == "octocat/r"
    assert repo_path("https://gitlab.x.org/", "https://gitlab.x.org/g/sous/p") == "g/sous/p"


async def test_github_depot_invisible_pas_d_ecriture() -> None:
    async with _client(_github_handler) as client:  # /repos/... → 404
        check = await GitHubStrategy().verify_write(client, "https://github.com", "o/r", "good")
    assert (check.login, check.can_write) == ("octocat", False)


def _gitlab_access_handler(level: int) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v4/user":
            return httpx.Response(200, json={"username": "gl-user"})
        if request.url.path.startswith("/api/v4/projects/"):
            assert request.url.raw_path.endswith(b"/projects/g%2Fp")
            return httpx.Response(
                200,
                json={
                    "permissions": {"project_access": None, "group_access": {"access_level": level}}
                },
            )
        return httpx.Response(404)

    return handler


@pytest.mark.parametrize(("level", "expected"), [(30, True), (40, True), (20, False)])
async def test_gitlab_niveau_d_acces_developer(level: int, expected: bool) -> None:
    async with _client(_gitlab_access_handler(level)) as client:
        check = await GitLabStrategy().verify_write(client, "https://gitlab.x.org", "g/p", "t")
    assert (check.login, check.can_write) == ("gl-user", expected)


async def test_azure_droit_non_verifiable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"authenticatedUser": {"providerDisplayName": "Alice Azure"}}
        )

    async with _client(handler) as client:
        check = await AzureDevOpsStrategy().verify_write(
            client, "https://dev.azure.com/o", "p/r", "t"
        )
    assert (check.login, check.can_write) == ("Alice Azure", None)


async def test_azure_connection_data_en_preversion() -> None:
    # Azure refuse connectionData en version non « preview » (400) : la
    # vérification du jeton doit demander une préversion.
    def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.params.get("api-version", "").endswith("-preview.1"):
            return httpx.Response(400, json={"message": "version under preview"})
        return httpx.Response(200, json={"authenticatedUser": {"providerDisplayName": "Gaël"}})

    async with _client(handler) as client:
        identity = await AzureDevOpsStrategy().verify(client, "https://dev.azure.com/o", "t")
    assert identity.username == "Gaël"


async def test_azure_redirection_vers_la_connexion_jeton_refuse() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://login"}, text="<html>moved</html>")

    async with _client(handler) as client:
        with pytest.raises(ForgeError) as exc:
            await AzureDevOpsStrategy().verify(client, "https://dev.azure.com/o", "mauvais")
    assert exc.value.code == "connection_token_invalid"


async def test_gitlab_portee_insuffisante_distinguee_du_jeton_invalide() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("PRIVATE-TOKEN") == "sans-portee":
            return httpx.Response(403, json={"error": "insufficient_scope"})
        return httpx.Response(401, json={"message": "401 Unauthorized"})

    async with _client(handler) as client:
        with pytest.raises(ForgeError) as scope:
            await GitLabStrategy().verify(client, "https://gitlab.com", "sans-portee")
        with pytest.raises(ForgeError) as invalid:
            await GitLabStrategy().verify(client, "https://gitlab.com", "expire")
    assert scope.value.code == "connection_token_scope_insufficient"
    assert invalid.value.code == "connection_token_invalid"
