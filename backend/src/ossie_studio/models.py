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
