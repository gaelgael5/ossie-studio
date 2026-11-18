from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import asyncpg

ROLE_ADMIN = "admin"
ROLE_USER = "user"


@dataclass(frozen=True)
class User:
    id: uuid.UUID
    email: str
    password_hash: str | None
    role: str
    is_bootstrap: bool
    created_at: datetime
    profile_id: uuid.UUID | None = None
    display_name: str = ""
    last_login_at: datetime | None = None
    team_id: uuid.UUID | None = None
    team_pinned: bool = False

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> User:
        return cls(
            id=row["id"],
            email=row["email"],
            password_hash=row["password_hash"],
            role=row["role"],
            is_bootstrap=row["is_bootstrap"],
            created_at=row["created_at"],
            profile_id=row["profile_id"] if "profile_id" in row else None,
            display_name=row["display_name"] if "display_name" in row else "",
            last_login_at=row["last_login_at"] if "last_login_at" in row else None,
            team_id=row["team_id"] if "team_id" in row else None,
            team_pinned=row["team_pinned"] if "team_pinned" in row else False,
        )


@dataclass(frozen=True)
class Profile:
    id: uuid.UUID
    name: str
    description: str
    capabilities: list[str]
    is_admin: bool
    is_default: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> Profile:
        return cls(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            capabilities=list(row["capabilities"] or []),
            is_admin=row["is_admin"],
            is_default=row["is_default"] if "is_default" in row else False,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(frozen=True)
class Secret:
    id: uuid.UUID
    slug: str
    label: str
    category: str
    value_encrypted: bytes
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> Secret:
        return cls(
            id=row["id"],
            slug=row["slug"],
            label=row["label"],
            category=row["category"],
            value_encrypted=row["value_encrypted"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(frozen=True)
class OidcConnector:
    id: uuid.UUID
    label: str
    issuer: str
    client_id: str
    client_secret_encrypted: bytes
    scopes: str
    created_at: datetime
    roles_claim: str = ""
    team_claim: str = ""

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> OidcConnector:
        return cls(
            id=row["id"],
            label=row["label"],
            issuer=row["issuer"],
            client_id=row["client_id"],
            client_secret_encrypted=row["client_secret_encrypted"],
            scopes=row["scopes"],
            created_at=row["created_at"],
            roles_claim=row["roles_claim"] if "roles_claim" in row else "",
            team_claim=row["team_claim"] if "team_claim" in row else "",
        )


@dataclass(frozen=True)
class OidcRoleMapping:
    id: uuid.UUID
    connector_id: uuid.UUID
    position: int
    role_value: str
    profile_id: uuid.UUID

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> OidcRoleMapping:
        return cls(
            id=row["id"],
            connector_id=row["connector_id"],
            position=row["position"],
            role_value=row["role_value"],
            profile_id=row["profile_id"],
        )


@dataclass(frozen=True)
class Team:
    """Équipe du référentiel local. AFFICHAGE SEULEMENT : ne porte aucun droit."""

    id: uuid.UUID
    name: str
    slug: str
    origin: str  # 'oidc' (venue de l'annuaire) | 'manual' (créée à la main)
    source_connector_id: uuid.UUID | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> Team:
        return cls(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            origin=row["origin"],
            source_connector_id=row["source_connector_id"],
            created_at=row["created_at"],
        )


@dataclass(frozen=True)
class Connection:
    id: uuid.UUID
    label: str
    forge: str
    url: str
    secret_slug: str
    identity: str
    last_status: str
    last_error_code: str | None
    last_checked_at: datetime | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> Connection:
        return cls(
            id=row["id"],
            label=row["label"],
            forge=row["forge"],
            url=row["url"],
            secret_slug=row["secret_slug"],
            identity=row["identity"],
            last_status=row["last_status"],
            last_error_code=row["last_error_code"],
            last_checked_at=row["last_checked_at"],
            created_at=row["created_at"],
        )


@dataclass(frozen=True)
class GitSpace:
    id: uuid.UUID
    name: str
    connection_id: uuid.UUID
    repo_url: str
    last_probe_status: str | None
    last_probe_at: datetime | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> GitSpace:
        return cls(
            id=row["id"],
            name=row["name"],
            connection_id=row["connection_id"],
            repo_url=row["repo_url"],
            last_probe_status=row["last_probe_status"],
            last_probe_at=row["last_probe_at"],
            created_at=row["created_at"],
        )


@dataclass(frozen=True)
class GovernedTag:
    id: str
    owner: str
    description: str
    links: dict[str, str]
    properties: dict[str, object]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> GovernedTag:
        return cls(
            id=row["id"],
            owner=row["owner"],
            description=row["description"],
            links=dict(row["links"] or {}),
            properties=dict(row["properties"] or {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(frozen=True)
class Certification:
    id: uuid.UUID
    name: str
    rank: int
    description: str
    criteria: str
    tag: str
    icon: str
    restricted: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> Certification:
        return cls(
            id=row["id"],
            name=row["name"],
            rank=row["rank"],
            description=row["description"],
            criteria=row["criteria"],
            tag=row["tag"],
            icon=row["icon"],
            restricted=row["restricted"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(frozen=True)
class ClassificationLevel:
    id: uuid.UUID
    scheme_id: str
    name: str
    label: str
    short_name: str
    rank: int
    description: str
    criteria: str
    color: str
    icon: str
    position: int

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> ClassificationLevel:
        return cls(
            id=row["id"],
            scheme_id=row["scheme_id"],
            name=row["name"],
            label=row["label"],
            short_name=row["short_name"],
            rank=row["rank"],
            description=row["description"],
            criteria=row["criteria"],
            color=row["color"],
            icon=row["icon"],
            position=row["position"],
        )


@dataclass(frozen=True)
class ClassificationScheme:
    id: str
    name: str
    description: str
    badge_prefix: str
    multi_value: bool
    is_primary: bool
    display_order: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> ClassificationScheme:
        return cls(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            badge_prefix=row["badge_prefix"],
            multi_value=row["multi_value"],
            is_primary=row["is_primary"],
            display_order=row["display_order"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(frozen=True)
class SpaceBranch:
    id: uuid.UUID
    space_id: uuid.UUID
    label: str
    slug: str
    kind: str
    git_ref: str
    created_at: datetime

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> SpaceBranch:
        return cls(
            id=row["id"],
            space_id=row["space_id"],
            label=row["label"],
            slug=row["slug"],
            kind=row["kind"],
            git_ref=row["git_ref"],
            created_at=row["created_at"],
        )
