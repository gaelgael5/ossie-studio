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
    db_add_user_team,
    db_create_team_manual,
    db_get_or_create_team_by_name,
    db_remove_user_team,
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


async def _user(app: FastAPI, email: str, *, profile_id: uuid.UUID | None = None) -> uuid.UUID:
    async with app.state.pool.acquire() as conn:
        value = await conn.fetchval(
            "INSERT INTO users (email, role, profile_id) VALUES ($1, 'user', $2) RETURNING id",
            email,
            profile_id,
        )
    assert isinstance(value, uuid.UUID)
    return value


async def _seed_membership(
    app: FastAPI, user_id: uuid.UUID, team_id: uuid.UUID, origin: str
) -> None:
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO user_teams (user_id, team_id, origin) VALUES ($1, $2, $3)",
            user_id, team_id, origin,
        )


async def _memberships(app: FastAPI, user_id: uuid.UUID) -> list[tuple[str, str]]:
    """(nom d'équipe, origine du rattachement), trié par nom."""
    async with app.state.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT t.name, ut.origin FROM user_teams ut JOIN teams t ON t.id = ut.team_id "
            "WHERE ut.user_id = $1 ORDER BY t.name",
            user_id,
        )
    return [(r["name"], r["origin"]) for r in rows]


async def _team_ids(app: FastAPI, user_id: uuid.UUID) -> set[uuid.UUID]:
    async with app.state.pool.acquire() as conn:
        rows = await conn.fetch("SELECT team_id FROM user_teams WHERE user_id = $1", user_id)
    return {r["team_id"] for r in rows}


# --- Attribution OIDC à la connexion (une seule équipe OIDC, ce ticket) -------

async def test_claim_designe_et_cree_equipe(app: FastAPI) -> None:
    # Le claim porte `equipe-data`, l'équipe est créée (origine OIDC), rattachée.
    cid = await _connector(app)
    user = await _user(app, "u1@example.org")
    async with app.state.pool.acquire() as conn:
        await apply_team_mapping(conn, cid, "department", {"department": "equipe-data"}, user)
        team = await conn.fetchrow(
            "SELECT t.name, t.origin, t.source_connector_id, ut.origin AS membership "
            "FROM teams t JOIN user_teams ut ON ut.team_id = t.id WHERE ut.user_id = $1",
            user,
        )
    assert team is not None
    assert team["name"] == "equipe-data"
    assert team["origin"] == "oidc" and team["source_connector_id"] == cid
    assert team["membership"] == "oidc"  # rattachement venu de l'OIDC


async def test_plusieurs_valeurs_premiere_gagne_et_journalise(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ce ticket garde « une seule équipe OIDC, la première l'emporte » (cas journalisé).
    spy = _LogSpy()
    monkeypatch.setattr("ossie_studio.teams.service.log", spy)
    cid = await _connector(app)
    user = await _user(app, "u2@example.org")
    async with app.state.pool.acquire() as conn:
        await apply_team_mapping(conn, cid, "groups", {"groups": ["equipe-a", "equipe-b"]}, user)
    assert await _memberships(app, user) == [("equipe-a", "oidc")]
    assert spy.find("oidc_team_claim_multiple_values")["kept"] == "equipe-a"


async def test_equipe_oidc_suit_le_claim_une_seule(app: FastAPI) -> None:
    # L'équipe OIDC suit le jeton : une seconde valeur remplace la première.
    cid = await _connector(app)
    free = await _user(app, "free@example.org")
    async with app.state.pool.acquire() as conn:
        await apply_team_mapping(conn, cid, "dep", {"dep": "equipe-1"}, free)
        await apply_team_mapping(conn, cid, "dep", {"dep": "equipe-2"}, free)
    assert await _memberships(app, free) == [("equipe-2", "oidc")]


async def test_rattachement_manuel_bloque_l_oidc(app: FastAPI) -> None:
    # Une personne avec un rattachement MANUEL n'est pas touchée par l'OIDC.
    cid = await _connector(app)
    async with app.state.pool.acquire() as conn:
        fixed = await db_create_team_manual(conn, "equipe-fixe")
    pinned = await _user(app, "pin@example.org")
    await _seed_membership(app, pinned, fixed.id, "manual")
    async with app.state.pool.acquire() as conn:
        await apply_team_mapping(conn, cid, "dep", {"dep": "equipe-autre"}, pinned)
    assert await _team_ids(app, pinned) == {fixed.id}  # conservé, rien d'OIDC ajouté


async def test_claim_absent_ou_inattendu_conserve(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Claim absent ou de forme inattendue → rattachements conservés, averti.
    cid = await _connector(app)
    async with app.state.pool.acquire() as conn:
        depart = await db_get_or_create_team_by_name(
            conn, "equipe-depart", origin="oidc", source_connector_id=cid
        )
    user = await _user(app, "keep@example.org")
    await _seed_membership(app, user, depart, "oidc")

    async with app.state.pool.acquire() as conn:
        await apply_team_mapping(conn, cid, "department", {}, user)  # absent
    assert await _team_ids(app, user) == {depart}

    spy = _LogSpy()
    monkeypatch.setattr("ossie_studio.oidc.roles.log", spy)
    async with app.state.pool.acquire() as conn:
        await apply_team_mapping(conn, cid, "department", {"department": 42}, user)  # inattendu
    assert await _team_ids(app, user) == {depart}
    assert "oidc_team_claim_unexpected_shape" in spy.names()


async def test_changement_journalise_user_et_equipe(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Un rattachement OIDC posé journalise la personne et l'équipe.
    spy = _LogSpy()
    monkeypatch.setattr("ossie_studio.teams.service.log", spy)
    cid = await _connector(app)
    user = await _user(app, "log@example.org")
    async with app.state.pool.acquire() as conn:
        await apply_team_mapping(conn, cid, "dep", {"dep": "equipe-x"}, user)
    entry = spy.find("oidc_team_applied")
    assert entry["user_id"] == str(user)
    assert entry["new_team_name"] == "equipe-x"


async def test_equipe_ne_change_aucun_droit(app: FastAPI) -> None:
    # Ce ticket : le rattachement d'équipe ne change aucune capacité (l'effet des
    # droits par équipe est un ticket suivant).
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
        after = await load_capabilities(conn, user)
    assert before == after == {"oidc:read"}
    assert await _team_ids(app, user_id)  # un rattachement a bien été posé


# --- API connecteur (claim) --------------------------------------------------

@pytest.fixture
async def admin(client: AsyncClient) -> AsyncClient:
    await bootstrap_admin(client)
    return client


async def test_api_team_claim_et_liste_des_equipes(admin: AsyncClient, app: FastAPI) -> None:
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
    cid = await _connector(app)
    await create_plain_user(app, "limite@example.org", "mot-de-passe-solide")
    await login(client, "limite@example.org", "mot-de-passe-solide")
    r = await client.put(f"/api/oidc/connectors/{cid}/team-claim", json={"team_claim": "x"})
    assert r.status_code == 403


# ===== Appartenance multiple (ce ticket) =====================================


async def test_ajout_manuel_pose_un_rattachement(app: FastAPI) -> None:
    # Ajouter une équipe à la main pose un rattachement d'origine manuelle.
    async with app.state.pool.acquire() as conn:
        t = await db_create_team_manual(conn, "Équipe RH")
        user = await _user(app, "rh@example.org")
        await db_add_user_team(conn, user, t.id)
    assert await _memberships(app, user) == [("Équipe RH", "manual")]


async def test_plusieurs_equipes_a_la_main(app: FastAPI) -> None:
    # Critères 1 & 2 : une personne dans plusieurs équipes ; retirer l'une laisse l'autre.
    async with app.state.pool.acquire() as conn:
        a = await db_create_team_manual(conn, "Alpha")
        b = await db_create_team_manual(conn, "Beta")
        user = await _user(app, "multi@example.org")
        await db_add_user_team(conn, user, a.id)
        await db_add_user_team(conn, user, b.id)
    assert await _team_ids(app, user) == {a.id, b.id}
    async with app.state.pool.acquire() as conn:
        assert await db_remove_user_team(conn, user, a.id) is True
    assert await _team_ids(app, user) == {b.id}  # Beta intacte


async def test_manuel_resiste_au_claim_puis_retrait_rend_la_main(app: FastAPI) -> None:
    # Le rattachement manuel résiste au claim ; une fois retiré, l'OIDC reprend.
    cid = await _connector(app)
    async with app.state.pool.acquire() as conn:
        manuelle = await db_create_team_manual(conn, "equipe-manuelle")
        user = await _user(app, "surcharge@example.org")
        await db_add_user_team(conn, user, manuelle.id)
        await apply_team_mapping(conn, cid, "dep", {"dep": "equipe-claim"}, user)
    assert await _team_ids(app, user) == {manuelle.id}  # claim bloqué par le manuel

    async with app.state.pool.acquire() as conn:
        assert await db_remove_user_team(conn, user, manuelle.id) is True
        await apply_team_mapping(conn, cid, "dep", {"dep": "equipe-claim"}, user)
    assert await _memberships(app, user) == [("equipe-claim", "oidc")]


async def test_creer_equipe_a_la_main(app: FastAPI) -> None:
    async with app.state.pool.acquire() as conn:
        team = await db_create_team_manual(conn, "Plateforme")
        again = await db_create_team_manual(conn, "Plateforme")  # idempotent sur le nom
    assert team.origin == "manual"
    assert again.id == team.id  # pas de doublon


async def test_api_ajouter_filtrer_et_retirer(admin: AsyncClient, app: FastAPI) -> None:
    # Via l'API : créer, ajouter (plusieurs), filtrer, retirer — sans toucher les autres.
    user = await _user(app, "api-team@example.org")
    data = (await admin.post("/api/teams", json={"name": "Data"})).json()["id"]
    ops = (await admin.post("/api/teams", json={"name": "Ops"})).json()["id"]

    assert (await admin.post(f"/api/users/{user}/teams", json={"team_id": data})).status_code == 200
    assert (await admin.post(f"/api/users/{user}/teams", json={"team_id": ops})).status_code == 200

    me = next(
        u for u in (await admin.get("/api/users")).json()["users"] if u["id"] == str(user)
    )
    assert sorted(t["name"] for t in me["teams"]) == ["Data", "Ops"]
    assert all(t["origin"] == "manual" for t in me["teams"])

    # Filtre par équipe : la personne membre de Data ressort.
    filtered = (await admin.get(f"/api/users?team_id={data}")).json()
    assert [u["id"] for u in filtered["users"]] == [str(user)]

    # Retirer Data : Ops reste.
    assert (await admin.delete(f"/api/users/{user}/teams/{data}")).status_code == 200
    me = next(
        u for u in (await admin.get("/api/users")).json()["users"] if u["id"] == str(user)
    )
    assert [t["name"] for t in me["teams"]] == ["Ops"]


async def test_api_ajout_journalise_auteur(
    admin: AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = _LogSpy()
    monkeypatch.setattr("ossie_studio.api.users.log", spy)
    user = await _user(app, "journal@example.org")
    team_id = (await admin.post("/api/teams", json={"name": "Finance"})).json()["id"]
    await admin.post(f"/api/users/{user}/teams", json={"team_id": team_id})
    entry = spy.find("team_added")
    assert entry["user_id"] == str(user) and entry["team"] == team_id
    assert entry["author"]  # email de l'auteur présent


async def test_api_equipe_reserve_a_users_write(client: AsyncClient, app: FastAPI) -> None:
    user = await _user(app, "cible@example.org")
    await create_plain_user(app, "sanspouvoir@example.org", "mot-de-passe-solide")
    await login(client, "sanspouvoir@example.org", "mot-de-passe-solide")
    assert (await client.post("/api/teams", json={"name": "X"})).status_code == 403
    assert (
        await client.post(f"/api/users/{user}/teams", json={"team_id": str(uuid.uuid4())})
    ).status_code == 403
    assert (
        await client.delete(f"/api/users/{user}/teams/{uuid.uuid4()}")
    ).status_code == 403
