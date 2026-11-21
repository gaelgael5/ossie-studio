from __future__ import annotations

import uuid

from ossie_studio.oidc.claims import VerifiedClaims
from ossie_studio.oidc.linking import LinkOutcome, decide_link

ISSUER = "http://idp.test/realms/test"


def _claims(email: str | None = "gael@example.org", verified: bool = True) -> VerifiedClaims:
    return VerifiedClaims(subject="sub-123", email=email, email_verified=verified)


def test_couple_deja_epingle_rend_le_compte_ancre() -> None:
    pinned = uuid.uuid4()
    decision = decide_link(pinned, [], _claims())
    assert decision.outcome is LinkOutcome.LINKED_EXISTING
    assert decision.user_id == pinned


def test_couple_prime_sur_email_meme_si_email_change() -> None:
    """Email modifié chez l'IdP : le couple (issuer, sub) retrouve le compte,
    aucun pont email n'est tenté."""
    pinned = uuid.uuid4()
    autre = uuid.uuid4()
    decision = decide_link(pinned, [autre], _claims(email="nouvelle@example.org"))
    assert decision.outcome is LinkOutcome.LINKED_EXISTING
    assert decision.user_id == pinned


def test_email_verifie_un_seul_compte_rattache() -> None:
    compte = uuid.uuid4()
    decision = decide_link(None, [compte], _claims())
    assert decision.outcome is LinkOutcome.LINKED_EXISTING
    assert decision.user_id == compte


def test_email_verifie_aucun_compte_cree_nouveau() -> None:
    decision = decide_link(None, [], _claims())
    assert decision.outcome is LinkOutcome.CREATED_NEW
    assert decision.user_id is None


def test_plusieurs_comptes_meme_email_ambigu() -> None:
    decision = decide_link(None, [uuid.uuid4(), uuid.uuid4()], _claims())
    assert decision.outcome is LinkOutcome.AMBIGUOUS_EMAIL
    assert decision.user_id is None


def test_email_non_verifie_refuse() -> None:
    decision = decide_link(None, [], _claims(verified=False))
    assert decision.outcome is LinkOutcome.REFUSED_UNVERIFIED


def test_email_absent_refuse() -> None:
    decision = decide_link(None, [], _claims(email=None))
    assert decision.outcome is LinkOutcome.REFUSED_UNVERIFIED
