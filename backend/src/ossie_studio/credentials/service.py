"""Identifiants personnels par forge (jeton propre à chaque utilisateur).

Le jeton est chiffré au repos (même `CryptoService` que les secrets), jamais
renvoyé, jamais journalisé. Toute lecture est **scopée à son propriétaire** :
un autre compte, administrateur compris, ne le voit pas (404)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import asyncpg
import structlog

from ossie_studio.authz.catalog import SPACE_SCOPED
from ossie_studio.crypto import CryptoService, SecretValue

log = structlog.get_logger(__name__)

STATUS_TO_ENTER = "a_saisir"
STATUS_NEVER_TESTED = "jamais_teste"
STATUS_VALID = "valide"
STATUS_REFUSED = "refuse"

# Capacités qui écrivent dans un espace : les recevoir par un rattachement
# d'équipe prépare l'identifiant de la personne (lecture seule → rien).
_SPACE_READS = frozenset({"spaces:read", "sources:read", "curation:read", "governance:read"})
SPACE_WRITES = SPACE_SCOPED - _SPACE_READS


class CredentialNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class PersonalCredential:
    id: uuid.UUID
    user_id: uuid.UUID
    connection_id: uuid.UUID
    status: str
    forge_login: str | None
    error_code: str | None
    checked_at: datetime | None
    has_value: bool

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> PersonalCredential:
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            connection_id=row["connection_id"],
            status=row["status"],
            forge_login=row["forge_login"],
            error_code=row["error_code"],
            checked_at=row["checked_at"],
            has_value=row["value_encrypted"] is not None,
        )


async def ensure_personal_credential(
    conn: asyncpg.Connection, user_id: uuid.UUID, connection_id: uuid.UUID
) -> PersonalCredential:
    """Rend l'identifiant de la personne pour cette connexion, en le créant à
    l'état « à saisir » s'il n'existe pas. Idempotent, sans doublon même en
    concurrence (contrainte d'unicité)."""
    await conn.execute(
        "INSERT INTO personal_credentials (user_id, connection_id) VALUES ($1, $2) "
        "ON CONFLICT (user_id, connection_id) DO NOTHING",
        user_id,
        connection_id,
    )
    row = await conn.fetchrow(
        "SELECT * FROM personal_credentials WHERE user_id = $1 AND connection_id = $2",
        user_id,
        connection_id,
    )
    assert row is not None
    return PersonalCredential.from_row(row)


async def db_get_own_credential(
    conn: asyncpg.Connection, user_id: uuid.UUID, credential_id: uuid.UUID
) -> PersonalCredential:
    row = await conn.fetchrow(
        "SELECT * FROM personal_credentials WHERE id = $1 AND user_id = $2",
        credential_id,
        user_id,
    )
    if row is None:
        raise CredentialNotFoundError
    return PersonalCredential.from_row(row)


async def db_set_credential_value(
    conn: asyncpg.Connection,
    crypto: CryptoService,
    user_id: uuid.UUID,
    credential_id: uuid.UUID,
    value: str,
) -> PersonalCredential:
    """Pose (ou change) le jeton : l'identifiant redevient « jamais testé »."""
    row = await conn.fetchrow(
        "UPDATE personal_credentials SET value_encrypted = $3, status = $4, forge_login = NULL, "
        "error_code = NULL, checked_at = NULL, updated_at = now() "
        "WHERE id = $1 AND user_id = $2 RETURNING *",
        credential_id,
        user_id,
        crypto.encrypt(SecretValue(value)),
        STATUS_NEVER_TESTED,
    )
    if row is None:
        raise CredentialNotFoundError
    return PersonalCredential.from_row(row)


async def db_record_test(
    conn: asyncpg.Connection,
    credential_id: uuid.UUID,
    *,
    status: str,
    forge_login: str | None,
    error_code: str | None,
) -> PersonalCredential:
    row = await conn.fetchrow(
        "UPDATE personal_credentials SET status = $2, forge_login = $3, error_code = $4, "
        "checked_at = now(), updated_at = now() WHERE id = $1 RETURNING *",
        credential_id,
        status,
        forge_login,
        error_code,
    )
    assert row is not None
    return PersonalCredential.from_row(row)


async def reveal_own_token(
    conn: asyncpg.Connection, crypto: CryptoService, user_id: uuid.UUID, credential_id: uuid.UUID
) -> SecretValue | None:
    blob = await conn.fetchval(
        "SELECT value_encrypted FROM personal_credentials WHERE id = $1 AND user_id = $2",
        credential_id,
        user_id,
    )
    return crypto.decrypt(blob) if blob is not None else None


async def resolve_personal_token(
    conn: asyncpg.Connection, crypto: CryptoService, user_id: uuid.UUID, connection_id: uuid.UUID
) -> SecretValue | None:
    """Jeton utilisable pour publier : seulement si l'identifiant est « valide »."""
    blob = await conn.fetchval(
        "SELECT value_encrypted FROM personal_credentials "
        "WHERE user_id = $1 AND connection_id = $2 AND status = $3",
        user_id,
        connection_id,
        STATUS_VALID,
    )
    return crypto.decrypt(blob) if blob is not None else None


async def ensure_credentials_for_space_team(
    conn: asyncpg.Connection, space_id: uuid.UUID, team_id: uuid.UUID
) -> int:
    """Quand une équipe reçoit, sur un espace, un profil qui écrit, chacun de ses
    membres a un identifiant « à saisir » pour la connexion de cet espace.
    Rend le nombre d'identifiants créés."""
    row = await conn.fetchrow(
        "SELECT s.connection_id, p.is_admin, p.capabilities FROM space_team_profiles stp "
        "JOIN git_spaces s ON s.id = stp.space_id JOIN profiles p ON p.id = stp.profile_id "
        "WHERE stp.space_id = $1 AND stp.team_id = $2",
        space_id,
        team_id,
    )
    if row is None:
        return 0
    writes = row["is_admin"] or bool(set(row["capabilities"] or []) & SPACE_WRITES)
    if not writes:
        return 0
    result: str = await conn.execute(
        "INSERT INTO personal_credentials (user_id, connection_id) "
        "SELECT ut.user_id, $2 FROM user_teams ut WHERE ut.team_id = $1 "
        "ON CONFLICT (user_id, connection_id) DO NOTHING",
        team_id,
        row["connection_id"],
    )
    created = int(result.split()[-1])
    log.info("personal_credentials_prepared", space_id=str(space_id), created=created)
    return created
