"""Journal d'audit (ticket docflow cf4434da) : brique, lecture, droits.

Les points d'audit des connexions et des connecteurs OIDC sont éprouvés dans
leurs propres fichiers (test_connections.py, test_oidc_connectors.py), avec
leurs doublures de forge et de fournisseur d'identité.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.audit.git_history import governance_events
from ossie_studio.audit.service import (
    AuditFilters,
    SensitiveDetailError,
    actor_label,
    clean_detail,
)
from ossie_studio.models import User
from ossie_studio.spaces.workdir import PathCommit
from tests.conftest import bootstrap_admin, create_plain_user, db_fetchval, login

SECRET_VALUE = "valeur-ultra-confidentielle-42"


@pytest.fixture
async def admin_client(client: AsyncClient) -> AsyncClient:
    await bootstrap_admin(client)
    return client


async def _events(client: AsyncClient, **params: str) -> list[dict]:  # type: ignore[type-arg]
    response = await client.get("/api/audit", params=params)
    assert response.status_code == 200, response.text
    return response.json()["events"]  # type: ignore[no-any-return]


async def test_creer_modifier_supprimer_un_secret_trois_lignes(admin_client: AsyncClient) -> None:
    body = {"slug": "jeton", "label": "Jeton", "category": "github", "value": SECRET_VALUE}
    assert (await admin_client.post("/api/secrets", json=body)).status_code == 201
    r = await admin_client.put("/api/secrets/jeton/label", json={"label": "Jeton forge"})
    assert r.status_code == 200
    r = await admin_client.put("/api/secrets/jeton/value", json={"value": "autre-" + SECRET_VALUE})
    assert r.status_code == 200
    assert (await admin_client.delete("/api/secrets/jeton")).status_code == 204

    events = await _events(admin_client, type="secret")
    assert [(e["action"], e["object_id"]) for e in events] == [
        ("deleted", "jeton"),
        ("updated", "jeton"),
        ("updated", "jeton"),
        ("created", "jeton"),
    ]
    created = events[-1]
    assert created["object_label"] == "Jeton" and created["detail"] == {"category": "github"}
    assert created["actor_id"] and "@" in created["actor_label"]
    assert created["origin"] == "db"
    # Jamais la valeur, nulle part dans la réponse.
    response = await admin_client.get("/api/audit")
    assert SECRET_VALUE not in response.text


async def test_action_refusee_aucune_ligne(admin_client: AsyncClient, app: FastAPI) -> None:
    body = {"slug": "jeton", "label": "Jeton", "category": "github", "value": SECRET_VALUE}
    assert (await admin_client.post("/api/secrets", json=body)).status_code == 201
    # Slug déjà pris : l'action échoue, la transaction est annulée, pas de ligne.
    assert (await admin_client.post("/api/secrets", json=body)).status_code == 409
    # Suppression d'un secret absent : rien n'a changé, pas de ligne.
    assert (await admin_client.delete("/api/secrets/absent")).status_code == 404
    assert await db_fetchval(app, "SELECT count(*) FROM audit_log") == 1


async def test_profil_cree_modifie_supprime(admin_client: AsyncClient) -> None:
    r = await admin_client.post(
        "/api/profiles", json={"name": "Lecteurs", "description": "", "capabilities": []}
    )
    assert r.status_code == 201, r.text
    profile_id = r.json()["id"]
    r = await admin_client.put(
        f"/api/profiles/{profile_id}",
        json={"name": "Lecteurs", "description": "", "capabilities": ["sources:read"]},
    )
    assert r.status_code == 200, r.text
    assert (await admin_client.delete(f"/api/profiles/{profile_id}")).status_code == 204
    events = await _events(admin_client, type="profile")
    assert [e["action"] for e in events] == ["deleted", "updated", "created"]
    assert events[1]["detail"] == {"capabilities": ["sources:read"]}


async def test_filtres_pagination_et_etat_vide(admin_client: AsyncClient) -> None:
    assert await _events(admin_client) == []
    for i in range(3):
        body = {"slug": f"s{i}", "label": f"S{i}", "category": "github", "value": SECRET_VALUE}
        assert (await admin_client.post("/api/secrets", json=body)).status_code == 201
    page = (await admin_client.get("/api/audit", params={"limit": "2"})).json()
    assert [e["object_id"] for e in page["events"]] == ["s2", "s1"]
    assert page["next"]
    rest = (
        await admin_client.get("/api/audit", params={"limit": "2", "before": page["next"]})
    ).json()
    assert [e["object_id"] for e in rest["events"]] == ["s0"] and rest["next"] is None
    assert await _events(admin_client, actor="personne-inconnue") == []
    future = datetime(2999, 1, 1, tzinfo=UTC).isoformat()
    assert await _events(admin_client, since=future) == []


async def test_lignes_immuables(app: FastAPI, admin_client: AsyncClient) -> None:
    body = {"slug": "jeton", "label": "Jeton", "category": "github", "value": SECRET_VALUE}
    assert (await admin_client.post("/api/secrets", json=body)).status_code == 201
    async with app.state.pool.acquire() as conn:
        with pytest.raises(asyncpg.RaiseError):
            await conn.execute("UPDATE audit_log SET actor_label = 'x'")
        with pytest.raises(asyncpg.RaiseError):
            await conn.execute("DELETE FROM audit_log")


async def test_empreinte_figee_survit_au_compte(app: FastAPI, admin_client: AsyncClient) -> None:
    # Pas de clé étrangère vers users : la ligne garde son empreinte lisible.
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO audit_log (actor_id, actor_label, action, object_type, object_id)"
            " VALUES ($1, 'Ancien <ancien@example.org>', 'created', 'team', 't1')",
            uuid.uuid4(),
        )
    [event] = await _events(admin_client, type="team")
    assert event["actor_label"] == "Ancien <ancien@example.org>"


@pytest.fixture
async def user_client(admin_client: AsyncClient, app: FastAPI) -> AsyncIterator[AsyncClient]:
    await create_plain_user(app, "simple@example.org", "mot-de-passe-simple")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as second:
        await login(second, "simple@example.org", "mot-de-passe-simple")
        yield second


async def test_sans_audit_read_403(user_client: AsyncClient) -> None:
    assert (await user_client.get("/api/audit")).status_code == 403


async def test_aucune_route_d_ecriture(admin_client: AsyncClient) -> None:
    for method in ("post", "put", "delete", "patch"):
        response = await getattr(admin_client, method)("/api/audit")
        assert response.status_code == 405


# --- unités ---------------------------------------------------------------------


def test_detail_refuse_les_cles_sensibles() -> None:
    assert clean_detail({"category": "github", "count": 2, "tags": ["a"]}) == {
        "category": "github",
        "count": 2,
        "tags": ["a"],
    }
    for key in ("value", "secret_slug", "token", "password", "private_key", "Passphrase"):
        with pytest.raises(SensitiveDetailError):
            clean_detail({key: "x"})
    with pytest.raises(SensitiveDetailError):
        clean_detail({"nested": {"a": 1}})


def test_empreinte_de_l_auteur() -> None:
    user = User(
        id=uuid.uuid4(),
        email="camille@example.org",
        password_hash=None,
        role="admin",
        is_bootstrap=False,
        created_at=datetime.now(UTC),
        display_name="Camille D.",
    )
    assert actor_label(user) == "Camille D. <camille@example.org>"
    assert actor_label(None) == "system"


def test_evenements_de_gouvernance_depuis_git() -> None:
    commits = [
        PathCommit(
            "b" * 40,
            "Camille",
            "2026-11-23T10:00:00+00:00",
            "gouvernance : certification",
            (("A", "governance/certifications/or.yaml"), ("M", "governance/tags/rgpd.yaml")),
        ),
        PathCommit(
            "a" * 40,
            "Système",
            "2026-11-22T09:00:00+00:00",
            "amorçage",
            (("A", "governance/classifications/classification.yaml"), ("A", "README.md")),
        ),
    ]
    events = governance_events(commits, AuditFilters())
    assert [(e.action, e.object_type, e.object_id) for e in events] == [
        ("created", "certification", "or"),
        ("updated", "governed_tag", "rgpd"),
        ("created", "classification_scheme", "classification"),
    ]
    assert events[0].origin == "git" and events[0].detail["commit"] == "b" * 12
    only = governance_events(commits, AuditFilters(object_type="governed_tag"))
    assert [e.object_id for e in only] == ["rgpd"]
