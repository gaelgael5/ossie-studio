from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

# Les trois forges couvertes par ce lot. La valeur sert aussi de catégorie de
# secret pour le jeton (cf. secretstore.categories).
FORGES = ("github", "azure_devops", "gitlab")


@dataclass(frozen=True)
class ForgeIdentity:
    """Identité sous laquelle une connexion est authentifiée chez la forge."""

    username: str


@dataclass(frozen=True)
class WriteCheck:
    """Résultat du test d'un jeton personnel sur un dépôt : à quel compte il
    appartient, et s'il peut pousser. `can_write` None = non vérifiable sur
    cette forge (affiché tel quel, jamais deviné)."""

    login: str
    can_write: bool | None


def repo_path(base_url: str, repo_url: str) -> str:
    """Chemin d'un dépôt (`owner/repo`, `groupe/sous/projet`) à partir de son URL
    de clone et de l'URL de la forge."""
    path = repo_url.strip().rstrip("/")
    base = base_url.strip().rstrip("/")
    if path.startswith(base):
        path = path[len(base) :]
    path = path.strip("/")
    return path.removesuffix(".git")


@dataclass(frozen=True)
class ForgeRepo:
    full_name: str
    url: str


@dataclass(frozen=True)
class ForgeFormSpec:
    """Description du formulaire d'ajout d'une connexion, fournie par la forge :
    de quoi construire sa popup dédiée sans que la page la connaisse. Les textes
    (nom, précision, portées du jeton) sont rendus par l'interface, indexés par
    la clé de forge ; ici on ne porte que la structure."""

    forge: str
    default_url: str  # pré-rempli (ex. https://github.com) ; "" si pas de défaut
    url_required: bool  # True : saisie obligatoire sans défaut (organisation Azure)
    # La catégorie de secret du jeton vaut la clé de forge.


class ForgeError(Exception):
    """Échec de dialogue avec une forge, porté par un code stable.

    Le code est l'un de : connection_token_invalid, connection_url_unreachable,
    connection_azure_org_not_found. Jamais de jeton ni de corps de réponse de
    la forge dans le message (anti-fuite).
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ForgeStrategy(Protocol):
    """Contrat que toute forge implémente. Les écrans et le futur extracteur ne
    parlent qu'à ce contrat, jamais à l'API d'une forge en direct — c'est ce qui
    permet d'ajouter une forge, ou un mode OAuth, sans les toucher."""

    async def verify(self, client: httpx.AsyncClient, url: str, token: str) -> ForgeIdentity:
        """Rend l'identité du compte connecté, ou lève ForgeError."""
        ...

    async def list_repositories(
        self, client: httpx.AsyncClient, url: str, token: str
    ) -> list[ForgeRepo]:
        """Liste tous les dépôts lisibles (pagination gérée de bout en bout)."""
        ...

    async def list_branches(
        self, client: httpx.AsyncClient, url: str, token: str, repo: str
    ) -> list[str]:
        """Liste les noms des branches d'un dépôt (identifié par son full_name).
        Sert à proposer la « branche suivie » d'une source Git."""
        ...

    async def verify_write(
        self, client: httpx.AsyncClient, url: str, repo: str, token: str
    ) -> WriteCheck:
        """Identité du jeton et droit d'écriture sur le dépôt `repo` (chemin), ou
        ForgeError si le jeton est refusé ou la forge injoignable."""
        ...

    def git_username(self) -> str:
        """Identifiant conventionnel passé à git avec le jeton en mot de passe."""
        ...

    def git_clone_url(self, base_url: str, repo: str) -> str:
        """URL de clone d'un dépôt (identifié par son full_name) sous cette forge."""
        ...

    def form_spec(self) -> ForgeFormSpec:
        """Décrit le formulaire d'ajout de cette forge (URL par défaut, etc.)."""
        ...
