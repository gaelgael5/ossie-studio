from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.authz.catalog import ALL_CAPABILITIES
from ossie_studio.models import User
from ossie_studio.oidc.roles import extract_memberships, memberships_from_claims
from ossie_studio.profiles.service import load_capabilities
from ossie_studio.teams.service import (
    apply_team_mapping,
    db_add_user_team,
    db_create_team_manual,
    db_get_or_create_team_by_name,
    db_remove_user_team,
    db_set_team_profile,
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


async def test_toutes_les_valeurs_du_claim_deviennent_equipes(app: FastAPI) -> None:
    # Critère 1 : chaque valeur du claim = une équipe OIDC (créée si absente).
    cid = await _connector(app)
    user = await _user(app, "u2@example.org")
    async with app.state.pool.acquire() as conn:
        await apply_team_mapping(conn, cid, "groups", {"groups": ["equipe-a", "equipe-b"]}, user)
    assert await _memberships(app, user) == [("equipe-a", "oidc"), ("equipe-b", "oidc")]


async def test_sync_ajoute_et_retire_les_oidc(app: FastAPI) -> None:
    # Critère 2 : claim [A,B] puis [A] → B (OIDC) retiré, A conservé.
    cid = await _connector(app)
    user = await _user(app, "sync@example.org")
    async with app.state.pool.acquire() as conn:
        await apply_team_mapping(conn, cid, "g", {"g": ["equipe-a", "equipe-b"]}, user)
        await apply_team_mapping(conn, cid, "g", {"g": ["equipe-a"]}, user)
    assert await _memberships(app, user) == [("equipe-a", "oidc")]


async def test_oidc_coexiste_avec_les_manuels(app: FastAPI) -> None:
    # Critère 3 : un rattachement manuel absent du claim est conservé ; l'OIDC gère
    # ses propres rattachements à côté.
    cid = await _connector(app)
    async with app.state.pool.acquire() as conn:
        fixed = await db_create_team_manual(conn, "equipe-fixe")
    user = await _user(app, "mix@example.org")
    await _seed_membership(app, user, fixed.id, "manual")
    async with app.state.pool.acquire() as conn:
        await apply_team_mapping(conn, cid, "dep", {"dep": "equipe-autre"}, user)
    assert await _memberships(app, user) == [("equipe-autre", "oidc"), ("equipe-fixe", "manual")]
    # Un nouveau claim retire l'OIDC mais garde le manuel.
    async with app.state.pool.acquire() as conn:
        await apply_team_mapping(conn, cid, "dep", {"dep": "equipe-encore"}, user)
    assert await _memberships(app, user) == [("equipe-encore", "oidc"), ("equipe-fixe", "manual")]


async def test_claim_membre_du_claim_ne_degrade_pas_le_manuel(app: FastAPI) -> None:
    # Un rattachement manuel à une équipe AUSSI dans le claim reste manuel.
    cid = await _connector(app)
    async with app.state.pool.acquire() as conn:
        shared = await db_get_or_create_team_by_name(
            conn, "equipe-partagee", origin="oidc", source_connector_id=cid
        )
    user = await _user(app, "share@example.org")
    await _seed_membership(app, user, shared, "manual")
    async with app.state.pool.acquire() as conn:
        await apply_team_mapping(conn, cid, "dep", {"dep": "equipe-partagee"}, user)
    assert await _memberships(app, user) == [("equipe-partagee", "manual")]


async def test_sync_idempotent(app: FastAPI) -> None:
    # Critère 5 : même claim deux fois → aucun changement.
    cid = await _connector(app)
    user = await _user(app, "idem@example.org")
    async with app.state.pool.acquire() as conn:
        await apply_team_mapping(conn, cid, "g", {"g": ["equipe-a", "equipe-b"]}, user)
        first = await _memberships(app, user)
        await apply_team_mapping(conn, cid, "g", {"g": ["equipe-a", "equipe-b"]}, user)
    assert await _memberships(app, user) == first == [("equipe-a", "oidc"), ("equipe-b", "oidc")]


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


async def test_synchro_journalisee(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    # La synchro OIDC journalise la personne et le nombre d'équipes.
    spy = _LogSpy()
    monkeypatch.setattr("ossie_studio.teams.service.log", spy)
    cid = await _connector(app)
    user = await _user(app, "log@example.org")
    async with app.state.pool.acquire() as conn:
        await apply_team_mapping(conn, cid, "g", {"g": ["equipe-x", "equipe-y"]}, user)
    entry = spy.find("oidc_teams_synced")
    assert entry["user_id"] == str(user) and entry["count"] == 2


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


async def test_manuel_conserve_puis_retrait_laisse_l_oidc(app: FastAPI) -> None:
    # Le rattachement manuel est conservé et l'OIDC gère les siens à côté ; une fois
    # le manuel retiré, il ne reste que l'OIDC du claim.
    cid = await _connector(app)
    async with app.state.pool.acquire() as conn:
        manuelle = await db_create_team_manual(conn, "equipe-manuelle")
        user = await _user(app, "surcharge@example.org")
        await db_add_user_team(conn, user, manuelle.id)
        await apply_team_mapping(conn, cid, "dep", {"dep": "equipe-claim"}, user)
    assert await _memberships(app, user) == [
        ("equipe-claim", "oidc"), ("equipe-manuelle", "manual")
    ]

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


# ===== Une équipe porte un profil ; capacités = union ========================


async def _profile(
    app: FastAPI, name: str, caps: list[str], *, is_admin: bool = False
) -> uuid.UUID:
    async with app.state.pool.acquire() as conn:
        value = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities, is_admin) VALUES ($1, $2, $3) RETURNING id",
            name, caps, is_admin,
        )
    assert isinstance(value, uuid.UUID)
    return value


async def _caps(app: FastAPI, user_id: uuid.UUID) -> set[str]:
    async with app.state.pool.acquire() as conn:
        user = User.from_row(await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id))
        return await load_capabilities(conn, user)


async def test_equipe_porte_profil_donne_capacites(app: FastAPI) -> None:
    # Critère 1 : membre d'une équipe au profil « Curation » → capacités en plus ;
    # l'équipe perd le profil → la personne les perd.
    perso = await _profile(app, "Perso", ["oidc:read"])
    curation = await _profile(app, "Curation", ["curation:read", "curation:write"])
    async with app.state.pool.acquire() as conn:
        team = await db_create_team_manual(conn, "Équipe Curation")
        await db_set_team_profile(conn, team.id, curation)
    user = await _user(app, "membre@example.org", profile_id=perso)
    await _seed_membership(app, user, team.id, "manual")
    assert await _caps(app, user) == {"oidc:read", "curation:read", "curation:write"}
    # L'équipe perd le profil → plus que le profil perso.
    async with app.state.pool.acquire() as conn:
        await db_set_team_profile(conn, team.id, None)
    assert await _caps(app, user) == {"oidc:read"}


async def test_union_de_deux_equipes(app: FastAPI) -> None:
    # Critère 2 : deux équipes aux profils différents → union des capacités.
    pa = await _profile(app, "PA", ["sources:read"])
    pb = await _profile(app, "PB", ["secrets:read"])
    async with app.state.pool.acquire() as conn:
        a = await db_create_team_manual(conn, "A")
        b = await db_create_team_manual(conn, "B")
        await db_set_team_profile(conn, a.id, pa)
        await db_set_team_profile(conn, b.id, pb)
    user = await _user(app, "deux@example.org")  # pas de profil perso
    await _seed_membership(app, user, a.id, "manual")
    await _seed_membership(app, user, b.id, "oidc")
    assert await _caps(app, user) == {"sources:read", "secrets:read"}


async def test_equipe_admin_rend_admin(app: FastAPI) -> None:
    # Critère 3 : une équipe au profil Administrateur rend ses membres admins.
    async with app.state.pool.acquire() as conn:
        admin_profile = await conn.fetchval("SELECT id FROM profiles WHERE is_admin")
        team = await db_create_team_manual(conn, "Admins")
        await db_set_team_profile(conn, team.id, admin_profile)
    user = await _user(app, "chef@example.org")  # aucun profil perso
    await _seed_membership(app, user, team.id, "manual")
    assert await _caps(app, user) == set(ALL_CAPABILITIES)


async def test_equipe_sans_profil_n_ajoute_rien(app: FastAPI) -> None:
    # Critère 4 : une équipe sans profil n'ajoute aucune capacité.
    perso = await _profile(app, "Seul", ["oidc:read"])
    async with app.state.pool.acquire() as conn:
        team = await db_create_team_manual(conn, "Sans profil")
    user = await _user(app, "neutre@example.org", profile_id=perso)
    await _seed_membership(app, user, team.id, "manual")
    assert await _caps(app, user) == {"oidc:read"}


async def test_suppression_profil_laisse_equipe_sans_profil(app: FastAPI) -> None:
    # Critère 6 : supprimer un profil associé à une équipe → équipe sans profil,
    # aucune capacité fantôme (ON DELETE SET NULL).
    pid = await _profile(app, "Éphémère", ["curation:read"])
    async with app.state.pool.acquire() as conn:
        team = await db_create_team_manual(conn, "Dépendante")
        await db_set_team_profile(conn, team.id, pid)
    user = await _user(app, "fantome@example.org")
    await _seed_membership(app, user, team.id, "manual")
    assert await _caps(app, user) == {"curation:read"}
    async with app.state.pool.acquire() as conn:
        await conn.execute("DELETE FROM profiles WHERE id = $1", pid)
        left = await conn.fetchval("SELECT profile_id FROM teams WHERE id = $1", team.id)
    assert left is None  # référence mise à NULL, pas cassée
    assert await _caps(app, user) == set()  # plus aucune capacité


async def test_set_team_profile_idempotent(app: FastAPI) -> None:
    # Critère 7 : rejouer l'association ne change rien et ne duplique rien.
    pid = await _profile(app, "Idem", ["oidc:read"])
    async with app.state.pool.acquire() as conn:
        team = await db_create_team_manual(conn, "Idempotente")
        await db_set_team_profile(conn, team.id, pid)
        await db_set_team_profile(conn, team.id, pid)
        current = await conn.fetchval("SELECT profile_id FROM teams WHERE id = $1", team.id)
    assert current == pid


async def test_api_set_profil_reserve_a_profiles_write(
    admin: AsyncClient, app: FastAPI
) -> None:
    # Critère 5 : associer un profil à une équipe exige le droit FORT (profiles:write),
    # pas la simple gestion des utilisateurs.
    # (`admin` partage la session de `client` : on agit d'abord en admin, puis on
    # ouvre la session restreinte sur le même client.)
    team_id = (await admin.post("/api/teams", json={"name": "Équipe X"})).json()["id"]
    pid = str(await _profile(app, "Lecteur", ["oidc:read"]))

    # L'administrateur (profiles:write compris) peut associer le profil.
    assert (
        await admin.put(f"/api/teams/{team_id}/profile", json={"profile_id": pid})
    ).status_code == 200

    # Un profil qui gère les utilisateurs mais PAS les profils est refusé.
    only_users = await _profile(app, "Gestion users", ["users:read", "users:write"])
    await create_plain_user(app, "gest@example.org", "mot-de-passe-solide")
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET profile_id = $2 WHERE email = $1", "gest@example.org", only_users
        )
    await login(admin, "gest@example.org", "mot-de-passe-solide")
    assert (
        await admin.put(f"/api/teams/{team_id}/profile", json={"profile_id": pid})
    ).status_code == 403


async def test_api_renommer_et_supprimer_equipe(admin: AsyncClient, app: FastAPI) -> None:
    a = (await admin.post("/api/teams", json={"name": "Un"})).json()["id"]
    (await admin.post("/api/teams", json={"name": "Deux"})).json()
    # Renommer vers un nom pris → refusé (409).
    assert (await admin.put(f"/api/teams/{a}", json={"name": "Deux"})).status_code == 409
    # Renommer vers un nom libre → ok.
    assert (await admin.put(f"/api/teams/{a}", json={"name": "Trois"})).status_code == 200
    # Supprimer.
    assert (await admin.delete(f"/api/teams/{a}")).status_code == 200
    names = [t["name"] for t in (await admin.get("/api/teams")).json()["teams"]]
    assert "Trois" not in names and "Deux" in names


async def test_creation_manuelle_avec_slug_et_renommage_qui_le_garde(
    client: AsyncClient, app: FastAPI
) -> None:
    await bootstrap_admin(client)
    created = await client.post("/api/teams", json={"name": "Équipe Data", "slug": "data"})
    assert created.status_code == 201, created.text
    assert created.json()["slug"] == "data"
    team_id = created.json()["id"]
    renamed = await client.put(f"/api/teams/{team_id}", json={"name": "Data & IA"})
    assert renamed.status_code in (200, 204), renamed.text
    listing = (await client.get("/api/teams")).json()["teams"]
    team = next(t for t in listing if t["id"] == team_id)
    assert (team["name"], team["slug"]) == ("Data & IA", "data")


async def test_slug_d_equipe_invalide_ou_pris(client: AsyncClient, app: FastAPI) -> None:
    await bootstrap_admin(client)
    bad = await client.post("/api/teams", json={"name": "X", "slug": "Pas Bon"})
    assert bad.status_code == 400
    assert "team_slug_invalid" in bad.json()["error_args"]["fields"]
    await client.post("/api/teams", json={"name": "Une", "slug": "pris"})
    taken = await client.post("/api/teams", json={"name": "Autre", "slug": "pris"})
    assert taken.status_code == 409
    assert taken.json()["error"] == "team_slug_taken"
