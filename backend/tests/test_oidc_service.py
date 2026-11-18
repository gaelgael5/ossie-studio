from __future__ import annotations

import uuid

from fastapi import FastAPI

from ossie_studio.models import ROLE_ADMIN, ROLE_USER
from ossie_studio.oidc.claims import VerifiedClaims
from ossie_studio.oidc.linking import LinkOutcome
from ossie_studio.oidc.service import resolve_federated_login
from tests.conftest import db_fetch, db_fetchrow, db_fetchval

ISSUER = "http://idp.test/realms/test"


async def _insert_user(app: FastAPI, email: str, role: str = ROLE_USER) -> uuid.UUID:
    pool = app.state.pool
    async with pool.acquire() as conn:
        user_id = await conn.fetchval(
            "INSERT INTO users (email, password_hash, role) VALUES ($1, $2, $3) RETURNING id",
            email,
            "hash",
            role,
        )
    assert isinstance(user_id, uuid.UUID)
    return user_id


async def _resolve(app: FastAPI, claims: VerifiedClaims) -> object:
    pool = app.state.pool
    async with pool.acquire() as conn:
        return await resolve_federated_login(conn, ISSUER, claims)


async def test_nouvel_arrivant_provisionne_au_role_par_defaut(app: FastAPI) -> None:
    claims = VerifiedClaims(subject="s1", email="neuf@example.org", email_verified=True)
    decision = await _resolve(app, claims)
    assert decision.outcome is LinkOutcome.CREATED_NEW  # type: ignore[attr-defined]
    user = await db_fetchrow(app, "SELECT role, password_hash, profile_id FROM users")
    identity = await db_fetchrow(app, "SELECT issuer, subject FROM user_identities")
    assert user is not None and identity is not None
    assert user["role"] == ROLE_USER
    assert user["password_hash"] is None
    assert identity["issuer"] == ISSUER
    assert identity["subject"] == "s1"
    # Le nouvel arrivant reçoit le profil par défaut (« Sans accès »).
    default_id = await db_fetchval(app, "SELECT id FROM profiles WHERE is_default")
    assert user["profile_id"] == default_id


async def test_admin_local_garde_son_role_en_revenant_par_oidc(app: FastAPI) -> None:
    """Le pont email épingle le couple sur le compte admin existant."""
    admin_id = await _insert_user(app, "gael@example.org", role=ROLE_ADMIN)
    claims = VerifiedClaims(subject="s-admin", email="gael@example.org", email_verified=True)
    decision = await _resolve(app, claims)
    assert decision.outcome is LinkOutcome.LINKED_EXISTING  # type: ignore[attr-defined]
    assert decision.user_id == admin_id  # type: ignore[attr-defined]
    owner = await db_fetchval(app, "SELECT user_id FROM user_identities")
    assert owner == admin_id


async def test_deuxieme_login_email_change_meme_compte_par_couple(app: FastAPI) -> None:
    """Après épinglage, un email modifié chez l'IdP retrouve le même compte."""
    user_id = await _insert_user(app, "gael@example.org")
    await _resolve(app, VerifiedClaims(subject="s9", email="gael@example.org", email_verified=True))
    decision = await _resolve(
        app, VerifiedClaims(subject="s9", email="gael-new@example.org", email_verified=True)
    )
    assert decision.outcome is LinkOutcome.LINKED_EXISTING  # type: ignore[attr-defined]
    assert decision.user_id == user_id  # type: ignore[attr-defined]
    identities = await db_fetch(app, "SELECT id FROM user_identities")
    assert len(identities) == 1  # aucun doublon


async def test_email_non_verifie_refuse_aucun_compte(app: FastAPI) -> None:
    claims = VerifiedClaims(subject="s-bad", email="gael@example.org", email_verified=False)
    decision = await _resolve(app, claims)
    assert decision.outcome is LinkOutcome.REFUSED_UNVERIFIED  # type: ignore[attr-defined]
    count = await db_fetchval(app, "SELECT count(*) FROM users")
    assert count == 0
