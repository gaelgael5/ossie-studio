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
class ForgeRepo:
    full_name: str
    url: str


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
