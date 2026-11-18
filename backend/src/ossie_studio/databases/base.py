"""Contrat « moteur de base de données ».

Comme pour les forges, chaque moteur (PostgreSQL, SQL Server, Snowflake, et
d'autres demain) implémente un même contrat ; la page et le futur extracteur ne
parlent qu'à lui, jamais à un pilote en direct. Ajouter un moteur = une
implémentation de plus et sa description de formulaire, sans toucher aux écrans
ni aux consommateurs.

Lecture seule : un moteur ne lit que des métadonnées (schémas, tables), jamais
les données. Mots de passe et clés vivent dans la table des secrets, désignés
par un slug ; ils ne transitent dans aucun log ni aucune réponse d'API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

# Moteurs couverts par ce lot. La valeur sert aussi de catégorie de secret.
ENGINES = ("postgres", "sqlserver", "snowflake")

# Délai maximal d'une vérification : une base qui ne répond pas ne bloque pas.
VERIFY_TIMEOUT_SECONDS = 10.0


class DbEngineError(Exception):
    """Échec de dialogue avec une base, porté par un code stable. Jamais de mot
    de passe, de clé ou de chaîne de connexion dans le message (anti-fuite).

    Codes : db_host_unreachable, db_auth_failed, db_not_found, db_role_not_found,
    db_warehouse_not_found, db_key_rejected, db_driver_unavailable.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DbIdentity:
    """Résultat d'une vérification : utilisateur effectivement connecté et
    version du serveur."""

    username: str
    server_version: str


@dataclass(frozen=True)
class DbTable:
    schema: str
    name: str


@dataclass(frozen=True)
class DbFormField:
    """Un champ du formulaire d'un moteur. Les libellés et aides sont rendus par
    l'interface, indexés par le nom du champ ; ici on ne porte que la structure."""

    name: str  # host, port, database, username, tls, account, role, warehouse
    kind: str  # "text" | "number" | "select"
    default: str = ""
    required: bool = True
    options: tuple[str, ...] = ()  # valeurs d'une liste (modes TLS)


@dataclass(frozen=True)
class DbEngineFormSpec:
    engine: str
    secret_category: str
    secret_kind: str  # "masked" (mot de passe) | "keyfile" (clé privée)
    target_field: str  # champ qui sert de « cible » affichée (host ou account)
    fields: tuple[DbFormField, ...] = field(default_factory=tuple)


class DatabaseEngine(Protocol):
    async def verify(self, params: dict[str, str], secret_value: str) -> DbIdentity:
        """Ouvre une session en lecture seule et rend l'identité, ou lève
        DbEngineError. Doit respecter VERIFY_TIMEOUT_SECONDS."""
        ...

    async def list_structure(self, params: dict[str, str], secret_value: str) -> list[DbTable]:
        """Liste les tables visibles (schéma + nom), sans lire aucune donnée."""
        ...

    def form_spec(self) -> DbEngineFormSpec:
        """Décrit le formulaire d'ajout de ce moteur."""
        ...
