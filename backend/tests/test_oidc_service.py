from __future__ import annotations

import uuid

from fastapi import FastAPI
from sqlalchemy import select

from ossie_studio.models import ROLE_ADMIN, ROLE_USER, User, UserIdentity
from ossie_studio.oidc.claims import VerifiedClaims
from ossie_studio.oidc.linking import LinkOutcome
from ossie_studio.oidc.service import resolve_federated_login

ISSUER = "http://idp.test/realms/test"


async def _insert_user(app: FastAPI, email: str, role: str = ROLE_USER) -> uuid.UUID:
    async with app.state.session_factory() as session:
        user = User(email=email, password_hash="hash", role=role)
        session.add(user)
        await session.commit()
        return user.id


async def test_nouvel_arrivant_provisionne_au_role_par_defaut(app: FastAPI) -> None:
    claims = VerifiedClaims(subject="s1", email="neuf@example.org", email_verified=True)
    async with app.state.session_factory() as session:
        decision = await resolve_federated_login(session, ISSUER, claims)
    assert decision.outcome is LinkOutcome.CREATED_NEW
    async with app.state.session_factory() as session:
        user = (await session.execute(select(User))).scalar_one()
        identity = (await session.execute(select(UserIdentity))).scalar_one()
    assert user.role == ROLE_USER
    assert user.password_hash is None
    assert identity.issuer == ISSUER
    assert identity.subject == "s1"


async def test_admin_local_garde_son_role_en_revenant_par_oidc(app: FastAPI) -> None:
    """Le pont email épingle le couple sur le compte admin existant."""
    admin_id = await _insert_user(app, "gael@example.org", role=ROLE_ADMIN)
    claims = VerifiedClaims(subject="s-admin", email="gael@example.org", email_verified=True)
    async with app.state.session_factory() as session:
        decision = await resolve_federated_login(session, ISSUER, claims)
    assert decision.outcome is LinkOutcome.LINKED_EXISTING
    assert decision.user_id == admin_id
    async with app.state.session_factory() as session:
        identity = (await session.execute(select(UserIdentity))).scalar_one()
    assert identity.user_id == admin_id


async def test_deuxieme_login_email_change_meme_compte_par_couple(app: FastAPI) -> None:
    """Après épinglage, un email modifié chez l'IdP retrouve le même compte."""
    user_id = await _insert_user(app, "gael@example.org")
    first = VerifiedClaims(subject="s9", email="gael@example.org", email_verified=True)
    async with app.state.session_factory() as session:
        await resolve_federated_login(session, ISSUER, first)
    changed = VerifiedClaims(subject="s9", email="gael-new@example.org", email_verified=True)
    async with app.state.session_factory() as session:
        decision = await resolve_federated_login(session, ISSUER, changed)
    assert decision.outcome is LinkOutcome.LINKED_EXISTING
    assert decision.user_id == user_id
    async with app.state.session_factory() as session:
        identities = list((await session.execute(select(UserIdentity))).scalars())
    assert len(identities) == 1  # aucun doublon


async def test_email_non_verifie_refuse_aucun_compte(app: FastAPI) -> None:
    claims = VerifiedClaims(subject="s-bad", email="gael@example.org", email_verified=False)
    async with app.state.session_factory() as session:
        decision = await resolve_federated_login(session, ISSUER, claims)
    assert decision.outcome is LinkOutcome.REFUSED_UNVERIFIED
    async with app.state.session_factory() as session:
        assert list((await session.execute(select(User))).scalars()) == []
