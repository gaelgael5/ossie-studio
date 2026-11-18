from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.models import User
from ossie_studio.oidc.roles import extract_memberships, memberships_from_claims
from ossie_studio.profiles.service import load_capabilities
from ossie_studio.teams.service import (
    apply_team_mapping,
    db_assign_team,
    db_create_team_manual,
    db_get_or_create_team_by_name,
    db_remove_team_pin,
    slugify,
)
from tests.conftest import bootstrap_admin, create_plain_user, login

# --- Espion de journalisation (déterministe, indépendant de la config structlog)

class _LogSpy:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **kw: object) -> None:
        self.events.append((event, kw))

    def warning(self, event: str, **kw: object) -> None:
        self.events.append((event, kw))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]

    def find(self, event: str) -> dict[str, object]:
        return next(kw for name, kw in self.events if name == event)


# --- Lecture du claim (même point d'adaptation, forme « chaîne » en plus) -----

def test_extract_memberships_trois_formes() -> None:
    assert extract_memberships("equipe-data") == ["equipe-data"]  # chaîne simple
    assert extract_memberships(["equipe-data", "x"]) == ["equipe-data", "x"]  # tableau
    assert extract_memberships({"equipe-data": {}, "x": {}}) == ["equipe-data", "x"]  # objet
    assert extract_memberships("") == []
    assert extract_memberships(42) == []  # toute autre forme → aucune valeur


def test_memberships_from_claims() -> None:
    assert memberships_from_claims({"department": "equipe-data"}, "department") == ["equipe-data"]
    assert memberships_from_claims({"a": {"b": ["e1"]}}, "a.b") == ["e1"]
    assert memberships_from_claims({}, "department") == []  # absent
    assert memberships_from_claims({"department": 42}, "department") == []  # forme inattendue
    assert memberships_from_claims({"department": "x"}, "") == []  # pas de chemin


def test_slugify() -> None:
    assert slugify("Équipe Data") == "equipe-data"
    assert slugify("  R&D / Plateforme ") == "r-d-plateforme"


# --- Helpers DB --------------------------------------------------------------

async def _connector(app: FastAPI) -> uuid.UUID:
    async with app.state.pool.acquire() as conn:
        slug = f"oidc-{uuid.uuid4().hex}"
        await conn.execute(
            "INSERT INTO secrets (slug, label, category, value_encrypted) "
            "VALUES ($1, 'Secret', 'oidc', '\\x00'::bytea)",
            slug,
        )
        value = await conn.fetchval(
            "INSERT INTO oidc_connectors (label, issuer, client_id, client_secret_slug) "
            "VALUES ('IdP', $1, 'cid', $2) RETURNING id",
            f"http://idp.test/{uuid.uuid4()}",
            slug,
        )
    assert isinstance(value, uuid.UUID)
    return value


async def _user(
    app: FastAPI,
    email: str,
    *,
    team_id: uuid.UUID | None = None,
    team_pinned: bool = False,
    profile_id: uuid.UUID | None = None,
) -> uuid.UUID:
    async with app.state.pool.acquire() as conn:
        value = await conn.fetchval(
            "INSERT INTO users (email, role, profile_id, team_id, team_pinned) "
            "VALUES ($1, 'user', $2, $3, $4) RETURNING id",
            email,
            profile_id,
            team_id,
            team_pinned,
        )
    assert isinstance(value, uuid.UUID)
    return value


async def _user_team(app: FastAPI, user_id: uuid.UUID) -> uuid.UUID | None:
    async with app.state.pool.acquire() as conn:
        return await conn.fetchval("SELECT team_id FROM users WHERE id = $1", user_id)


async def _team_name(app: FastAPI, user_id: uuid.UUID) -> str | None:
    async with app.state.pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT t.name FROM teams t JOIN users u ON u.team_id = t.id WHERE u.id = $1", user_id
        )


# --- Attribution à la connexion ---------------------------------------------

async def test_claim_designe_et_cree_equipe(app: FastAPI) -> None:
    # Critère 1 : le claim porte `equipe-data`, l'équipe est créée si absente.
    cid = await _connector(app)
    user = await _user(app, "u1@example.org")
    async with app.state.pool.acquire() as conn:
        await apply_team_mapping(conn, cid, "department", {"department": "equipe-data"}, user)
    async with app.state.pool.acquire() as conn:
        team = await conn.fetchrow(
            "SELECT t.name, t.origin, t.source_connector_id FROM teams t "
            "JOIN users u ON u.team_id = t.id WHERE u.id = $1",
            user,
        )
    assert team is not None
    assert team["name"] == "equipe-data"
    assert team["origin"] == "oidc"  # venue de l'OIDC
    assert team["source_connector_id"] == cid


async def test_plusieurs_valeurs_premiere_gagne_et_journalise(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Critère 3 : une seule équipe, la première valeur l'emporte, cas journalisé.
    spy = _LogSpy()
    monkeypatch.setattr("ossie_studio.teams.service.log", spy)
    cid = await _connector(app)
    user = await _user(app, "u2@example.org")
    async with app.state.pool.acquire() as conn:
        await apply_team_mapping(conn, cid, "groups", {"groups": ["equipe-a", "equipe-b"]}, user)
    assert await _team_name(app, user) == "equipe-a"
    assert "oidc_team_claim_multiple_values" in spy.names()
    assert spy.find("oidc_team_claim_multiple_values")["kept"] == "equipe-a"


async def test_change_equipe_sauf_fixee_a_la_main(app: FastAPI) -> None:
    # Critère 4 : l'équipe suit le jeton, sauf si fixée à la main.
    cid = await _connector(app)
    free = await _user(app, "free@example.org")
    async with app.state.pool.acquire() as conn:
        await apply_team_mapping(conn, cid, "dep", {"dep": "equipe-1"}, free)
        await apply_team_mapping(conn, cid, "dep", {"dep": "equipe-2"}, free)
    assert await _team_name(app, free) == "equipe-2"

    async with app.state.pool.acquire() as conn:
        fixed = await db_get_or_create_team_by_name(
            conn, "equipe-fixe", origin="manual", source_connector_id=None
        )
    pinned = await _user(app, "pin@example.org", team_id=fixed, team_pinned=True)
    async with app.state.pool.acquire() as conn:
        await apply_team_mapping(conn, cid, "dep", {"dep": "equipe-autre"}, pinned)
    assert await _user_team(app, pinned) == fixed  # conservée


async def test_claim_absent_ou_inattendu_conserve(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Critère 5 : claim absent ou de forme inattendue → équipe conservée, averti.
    cid = await _connector(app)
    async with app.state.pool.acquire() as conn:
        depart = await db_get_or_create_team_by_name(
            conn, "equipe-depart", origin="manual", source_connector_id=None
        )
    user = await _user(app, "keep@example.org", team_id=depart)

    async with app.state.pool.acquire() as conn:
        await apply_team_mapping(conn, cid, "department", {}, user)  # absent
    assert await _user_team(app, user) == depart

    spy = _LogSpy()
    monkeypatch.setattr("ossie_studio.oidc.roles.log", spy)
    async with app.state.pool.acquire() as conn:
        await apply_team_mapping(conn, cid, "department", {"department": 42}, user)  # inattendu
    assert await _user_team(app, user) == depart
    assert "oidc_team_claim_unexpected_shape" in spy.names()


async def test_changement_journalise_user_old_new(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Critère 7 : chaque changement dû au claim journalise user, ancienne, nouvelle.
    spy = _LogSpy()
    monkeypatch.setattr("ossie_studio.teams.service.log", spy)
    cid = await _connector(app)
    user = await _user(app, "log@example.org")
    async with app.state.pool.acquire() as conn:
        await apply_team_mapping(conn, cid, "dep", {"dep": "equipe-x"}, user)
    entry = spy.find("oidc_team_applied")
    assert entry["user_id"] == str(user)
    assert entry["old_team"] is None
    assert entry["new_team_name"] == "equipe-x"


async def test_equipe_ne_change_aucun_droit(app: FastAPI) -> None:
    # Critère 6 : changer d'équipe ne change aucune capacité.
    async with app.state.pool.acquire() as conn:
        pid = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) VALUES ('Lecteur OIDC', $1) RETURNING id",
            ["oidc:read"],
        )
    cid = await _connector(app)
    user_id = await _user(app, "rights@example.org", profile_id=pid)
    async with app.state.pool.acquire() as conn:
        user = User.from_row(await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id))
        before = await load_capabilities(conn, user)
        await apply_team_mapping(conn, cid, "dep", {"dep": "equipe-neutre"}, user_id)
        after_user = User.from_row(
            await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        )
        after = await load_capabilities(conn, after_user)
    assert before == after == {"oidc:read"}
    assert after_user.team_id is not None  # l'équipe a bien changé


# --- API ---------------------------------------------------------------------

@pytest.fixture
async def admin(client: AsyncClient) -> AsyncClient:
    await bootstrap_admin(client)
    return client


async def test_api_team_claim_et_liste_des_equipes(admin: AsyncClient, app: FastAPI) -> None:
    # Critères 1 & 9 (données) : chemin du claim réglé, équipes du connecteur listées.
    cid = await _connector(app)
    r = await admin.put(
        f"/api/oidc/connectors/{cid}/team-claim", json={"team_claim": "department"}
    )
    assert r.status_code == 200
    async with app.state.pool.acquire() as conn:
        await db_get_or_create_team_by_name(
            conn, "equipe-data", origin="oidc", source_connector_id=cid
        )
    connectors = (await admin.get("/api/oidc/connectors")).json()["connectors"]
    connector = next(c for c in connectors if c["id"] == str(cid))
    assert connector["team_claim"] == "department"
    assert [t["name"] for t in connector["teams"]] == ["equipe-data"]


async def test_team_claim_reserve_a_oidc_write(client: AsyncClient, app: FastAPI) -> None:
    # Critère 8 : réservé à la capacité « Connecteurs OIDC : modifier ».
    cid = await _connector(app)
    await create_plain_user(app, "limite@example.org", "mot-de-passe-solide")
    await login(client, "limite@example.org", "mot-de-passe-solide")
    r = await client.put(f"/api/oidc/connectors/{cid}/team-claim", json={"team_claim": "x"})
    assert r.status_code == 403


# ===== Ticket « assigner une équipe à la main » ==============================


async def test_assignation_manuelle_fixe_l_equipe(app: FastAPI) -> None:
    # Critère 1 : assigner à la main → cette seule équipe, marquée fixée à la main.
    async with app.state.pool.acquire() as conn:
        t = await db_create_team_manual(conn, "Équipe RH")
        user = await _user(app, "rh@example.org")
        old = await db_assign_team(conn, user, t.id)
        row = await conn.fetchrow(
            "SELECT team_id, team_pinned FROM users WHERE id = $1", user
        )
    assert old is None
    assert row["team_id"] == t.id
    assert row["team_pinned"] is True


async def test_surcharge_resiste_au_claim_puis_rendue(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Critères 2 & 3 : l'équipe fixée résiste au claim ; retirer la marque la rend.
    cid = await _connector(app)
    async with app.state.pool.acquire() as conn:
        manuelle = await db_create_team_manual(conn, "equipe-manuelle")
        user = await _user(app, "surcharge@example.org")
        await db_assign_team(conn, user, manuelle.id)
        # Connexion suivante : le claim désigne une autre équipe → conservée.
        await apply_team_mapping(conn, cid, "dep", {"dep": "equipe-claim"}, user)
    assert await _user_team(app, user) == manuelle.id

    async with app.state.pool.acquire() as conn:
        assert await db_remove_team_pin(conn, user) is True
        # La marque retirée, le claim reprend la main.
        await apply_team_mapping(conn, cid, "dep", {"dep": "equipe-claim"}, user)
    assert await _team_name(app, user) == "equipe-claim"


async def test_creer_equipe_a_la_main(app: FastAPI) -> None:
    # Critère 5 : créer une équipe à la main → rejoint le référentiel (origine manuelle).
    async with app.state.pool.acquire() as conn:
        team = await db_create_team_manual(conn, "Plateforme")
        again = await db_create_team_manual(conn, "Plateforme")  # idempotent sur le nom
    assert team.origin == "manual"
    assert again.id == team.id  # pas de doublon


async def test_api_assigner_filtrer_et_retirer(admin: AsyncClient, app: FastAPI) -> None:
    # Critères 1, 4, 5 via l'API : créer, assigner, filtrer, retirer la marque.
    user = await _user(app, "api-team@example.org")
    created = await admin.post("/api/teams", json={"name": "Data"})
    assert created.status_code == 201
    team_id = created.json()["id"]
    assert created.json()["origin"] == "manual"

    assigned = await admin.put(f"/api/users/{user}/team", json={"team_id": team_id})
    assert assigned.status_code == 200

    # Le payload utilisateur porte l'équipe et la marque.
    listed = (await admin.get("/api/users")).json()["users"]
    me = next(u for u in listed if u["id"] == str(user))
    assert me["team_name"] == "Data"
    assert me["team_pinned"] is True

    # Filtre par équipe.
    filtered = (await admin.get(f"/api/users?team_id={team_id}")).json()
    assert [u["id"] for u in filtered["users"]] == [str(user)]

    # Retirer la marque.
    removed = await admin.delete(f"/api/users/{user}/team-pin")
    assert removed.status_code == 200
    async with app.state.pool.acquire() as conn:
        pinned = await conn.fetchval("SELECT team_pinned FROM users WHERE id = $1", user)
    assert pinned is False


async def test_api_assignation_journalise_auteur(
    admin: AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Critère 6 : assignation journalisée (auteur, utilisateur, ancienne, nouvelle).
    spy = _LogSpy()
    monkeypatch.setattr("ossie_studio.api.users.log", spy)
    user = await _user(app, "journal@example.org")
    team_id = (await admin.post("/api/teams", json={"name": "Finance"})).json()["id"]
    await admin.put(f"/api/users/{user}/team", json={"team_id": team_id})
    entry = spy.find("team_assigned")
    assert entry["user_id"] == str(user)
    assert entry["old_team"] is None
    assert entry["new_team"] == team_id
    assert entry["author"]  # email de l'auteur présent


async def test_api_equipe_reserve_a_users_write(client: AsyncClient, app: FastAPI) -> None:
    # Critère 7 : réservé à la capacité « Utilisateurs : modifier ».
    user = await _user(app, "cible@example.org")
    await create_plain_user(app, "sanspouvoir@example.org", "mot-de-passe-solide")
    await login(client, "sanspouvoir@example.org", "mot-de-passe-solide")
    assert (await client.post("/api/teams", json={"name": "X"})).status_code == 403
    assert (
        await client.put(f"/api/users/{user}/team", json={"team_id": str(uuid.uuid4())})
    ).status_code == 403
    assert (await client.delete(f"/api/users/{user}/team-pin")).status_code == 403
