from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.oidc.mapping import (
    apply_role_mapping,
    db_create_mapping,
    resolve_mapped_profile,
)
from ossie_studio.oidc.roles import extract_roles, navigate_claim, roles_from_claims
from tests.conftest import bootstrap_admin

# --- Lecture du claim (point d'adaptation isolé) -----------------------------

def test_extract_roles_des_deux_formes() -> None:
    assert extract_roles(["ossie-admins", "dev"]) == ["ossie-admins", "dev"]  # Keycloak
    assert extract_roles({"ossie-admins": {}, "dev": {}}) == ["ossie-admins", "dev"]  # Zitadel
    assert extract_roles("pas une liste") == []  # autre forme → aucun rôle
    assert extract_roles(42) == []


def test_navigate_claim_pointe() -> None:
    claims = {"realm_access": {"roles": ["a"]}}
    assert navigate_claim(claims, "realm_access.roles") == ["a"]
    assert navigate_claim(claims, "realm_access.absent") is None
    assert navigate_claim({}, "x") is None


def test_roles_from_claims() -> None:
    assert roles_from_claims({"groups": ["g1"]}, "groups") == ["g1"]
    assert roles_from_claims({}, "groups") == []
    assert roles_from_claims({"groups": "mauvaise forme"}, "groups") == []


# --- Helpers DB --------------------------------------------------------------

async def _connector(app: FastAPI) -> uuid.UUID:
    async with app.state.pool.acquire() as conn:
        value = await conn.fetchval(
            "INSERT INTO oidc_connectors (label, issuer, client_id, client_secret_encrypted) "
            "VALUES ('IdP', $1, 'cid', '\\x00'::bytea) RETURNING id",
            f"http://idp.test/{uuid.uuid4()}",
        )
    assert isinstance(value, uuid.UUID)
    return value


async def _profile(app: FastAPI, name: str, is_admin: bool = False) -> uuid.UUID:
    async with app.state.pool.acquire() as conn:
        if is_admin:
            value = await conn.fetchval("SELECT id FROM profiles WHERE is_admin")
        else:
            value = await conn.fetchval(
                "INSERT INTO profiles (name) VALUES ($1) RETURNING id", name
            )
    assert isinstance(value, uuid.UUID)
    return value


async def _user(app: FastAPI, email: str, profile_id: uuid.UUID, pinned: bool = False) -> uuid.UUID:
    async with app.state.pool.acquire() as conn:
        value = await conn.fetchval(
            "INSERT INTO users (email, role, profile_id, profile_pinned) "
            "VALUES ($1, 'user', $2, $3) RETURNING id",
            email,
            profile_id,
            pinned,
        )
    assert isinstance(value, uuid.UUID)
    return value


async def _user_profile(app: FastAPI, user_id: uuid.UUID) -> uuid.UUID | None:
    async with app.state.pool.acquire() as conn:
        return await conn.fetchval("SELECT profile_id FROM users WHERE id = $1", user_id)


# --- Résolution et application ----------------------------------------------

async def test_ordre_premiere_correspondance_gagne(app: FastAPI) -> None:
    cid = await _connector(app)
    p1 = await _profile(app, "P1")
    p2 = await _profile(app, "P2")
    async with app.state.pool.acquire() as conn:
        await db_create_mapping(conn, cid, "a", p1)
        await db_create_mapping(conn, cid, "b", p2)
        assert await resolve_mapped_profile(conn, cid, ["b", "a"]) == p1  # a est en premier
        assert await resolve_mapped_profile(conn, cid, ["b"]) == p2
        assert await resolve_mapped_profile(conn, cid, ["autre"]) is None


async def test_application_et_profil_fixe(app: FastAPI) -> None:
    cid = await _connector(app)
    default = await _profile(app, "Défaut")
    target = await _profile(app, "Cible")
    async with app.state.pool.acquire() as conn:
        await db_create_mapping(conn, cid, "dev", target)
    # Non fixé : la correspondance s'applique.
    user = await _user(app, "libre@example.org", default, pinned=False)
    async with app.state.pool.acquire() as conn:
        await apply_role_mapping(conn, cid, ["dev"], user)
    assert await _user_profile(app, user) == target
    # Fixé à la main : la correspondance ne change rien.
    pinned_user = await _user(app, "fixe@example.org", default, pinned=True)
    async with app.state.pool.acquire() as conn:
        await apply_role_mapping(conn, cid, ["dev"], pinned_user)
    assert await _user_profile(app, pinned_user) == default


async def test_anti_verrouillage_dernier_admin(app: FastAPI) -> None:
    cid = await _connector(app)
    admin_profile = await _profile(app, "", is_admin=True)
    other = await _profile(app, "Sans")
    async with app.state.pool.acquire() as conn:
        await db_create_mapping(conn, cid, "retro", other)
    # Seul administrateur, non fixé : la correspondance ne doit pas le rétrograder.
    sole_admin = await _user(app, "admin@example.org", admin_profile, pinned=False)
    async with app.state.pool.acquire() as conn:
        await apply_role_mapping(conn, cid, ["retro"], sole_admin)
    assert await _user_profile(app, sole_admin) == admin_profile


# --- API ---------------------------------------------------------------------

@pytest.fixture
async def admin(client: AsyncClient) -> AsyncClient:
    await bootstrap_admin(client)
    return client


async def test_api_claim_et_correspondances(admin: AsyncClient, app: FastAPI) -> None:
    cid = await _connector(app)
    pid = (await admin.post("/api/profiles", json={"name": "Admins", "capabilities": []})).json()[
        "id"
    ]
    claim = await admin.put(
        f"/api/oidc/connectors/{cid}/roles-claim", json={"roles_claim": "realm_access.roles"}
    )
    assert claim.status_code == 200
    added = await admin.post(
        f"/api/oidc/connectors/{cid}/mappings",
        json={"role_value": "ossie-admins", "profile_id": pid},
    )
    assert added.status_code == 201
    dup = await admin.post(
        f"/api/oidc/connectors/{cid}/mappings",
        json={"role_value": "ossie-admins", "profile_id": pid},
    )
    assert dup.status_code == 409
    assert dup.json()["error"] == "mapping_duplicate"
    # La liste reflète le claim et les correspondances.
    connectors = (await admin.get("/api/oidc/connectors")).json()["connectors"]
    connector = next(c for c in connectors if c["id"] == str(cid))
    assert connector["roles_claim"] == "realm_access.roles"
    assert [m["role_value"] for m in connector["mappings"]] == ["ossie-admins"]
    # Supprimer un profil visé par une correspondance est refusé.
    refused = await admin.delete(f"/api/profiles/{pid}")
    assert refused.status_code == 409
    assert refused.json()["error"] == "profile_in_mapping"
    # Supprimer la correspondance puis le profil redevient possible.
    assert (
        await admin.delete(f"/api/oidc/connectors/{cid}/mappings/{added.json()['id']}")
    ).status_code == 204
    assert (await admin.delete(f"/api/profiles/{pid}")).status_code == 204


async def test_api_retirer_la_marque(admin: AsyncClient, app: FastAPI) -> None:
    default = await _profile(app, "Défaut2")
    user = await _user(app, "fixe2@example.org", default, pinned=True)
    removed = await admin.delete(f"/api/users/{user}/pin")
    assert removed.status_code == 200
    async with app.state.pool.acquire() as conn:
        pinned = await conn.fetchval("SELECT profile_pinned FROM users WHERE id = $1", user)
    assert pinned is False
