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
    db_warehouse_not_found, db_key_rejected, db_login_timeout, db_driver_unavailable.
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


# --- Catalogue normalisé (lecture seule, structure uniquement) ----------------
# Forme commune produite par chaque moteur et transformée en JSON Schema par
# l'extracteur SQL (dialect-agnostique). Chaque moteur résout lui-même ses types
# en fragment JSON Schema (il connaît ses types) ; le reste est générique.


@dataclass(frozen=True)
class CatalogColumn:
    name: str
    schema: dict[str, object]  # fragment JSON Schema du type (type/format/enum/x-db-type…)
    required: bool
    position: int
    default: str | None = None
    description: str = ""


@dataclass(frozen=True)
class CatalogForeignKey:
    columns: tuple[str, ...]
    ref_schema: str
    ref_object: str
    ref_columns: tuple[str, ...]
    enforced: bool = True


@dataclass(frozen=True)
class CatalogObject:
    schema: str
    name: str
    kind: str  # table | view | materialized-view | dynamic-table…
    columns: tuple[CatalogColumn, ...]
    description: str = ""
    primary_key: tuple[str, ...] = ()
    unique_keys: tuple[tuple[str, ...], ...] = ()
    foreign_keys: tuple[CatalogForeignKey, ...] = ()
    depends_on: tuple[str, ...] = ()
    extra: dict[str, object] = field(default_factory=dict)  # clefs x-* au niveau objet


@dataclass
class CatalogRead:
    """Ce qu'un moteur rend : les objets lus, les schémas en échec (droits
    insuffisants…) et le nombre de requêtes émises (coût)."""

    objects: list[CatalogObject] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)  # (schéma, cause)
    queries: int = 0


@dataclass(frozen=True)
class DbFormField:
    """Un champ du formulaire d'un moteur. Les libellés et aides sont rendus par
    l'interface, indexés par le nom du champ ; ici on ne porte que la structure."""

    name: str  # host, port, database, username, tls, account…
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

    async def read_catalog(
        self,
        params: dict[str, str],
        secret_value: str,
        *,
        schemas: list[str] | None,
        exclusions: list[str] | None,
    ) -> CatalogRead:
        """Lit le catalogue (structure seulement) des schémas retenus et rend des
        objets normalisés. Session en lecture seule ; aucune lecture de données.
        `schemas` : schémas inclus (vide/None = tous, hors schémas système) ;
        `exclusions` : schémas à écarter. Lève DbEngineError si la base est
        injoignable ; un schéma inaccessible va dans `errors`, les autres continuent."""
        ...

    def form_spec(self) -> DbEngineFormSpec:
        """Décrit le formulaire d'ajout de ce moteur."""
        ...
