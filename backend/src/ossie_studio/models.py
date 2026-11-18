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

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> User:
        return cls(
            id=row["id"],
            email=row["email"],
            password_hash=row["password_hash"],
            role=row["role"],
            is_bootstrap=row["is_bootstrap"],
            created_at=row["created_at"],
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
