from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.users.service import search_key
from tests.conftest import bootstrap_admin, create_plain_user, login


@pytest.fixture
async def admin(client: AsyncClient) -> AsyncClient:
    await bootstrap_admin(client)
    return client


async def _insert_user(app: FastAPI, email: str, display_name: str = "") -> uuid.UUID:
    async with app.state.pool.acquire() as conn:
        user_id = await conn.fetchval(
            "INSERT INTO users (email, role, display_name, search_key) "
            "VALUES ($1, 'user', $2, $3) RETURNING id",
            email,
            display_name,
            search_key(display_name, email),
        )
    assert isinstance(user_id, uuid.UUID)
    return user_id


async def _admin_user_id(app: FastAPI) -> uuid.UUID:
    async with app.state.pool.acquire() as conn:
        value = await conn.fetchval("SELECT id FROM users WHERE role = 'admin'")
    assert isinstance(value, uuid.UUID)
    return value


async def _profile(admin: AsyncClient, name: str, caps: list[str]) -> str:
    created = await admin.post("/api/profiles", json={"name": name, "capabilities": caps})
    return created.json()["id"]


async def test_recherche_insensible_casse_et_accents(admin: AsyncClient, app: FastAPI) -> None:
    await _insert_user(app, "jose@example.org", "José Pérez")
    await _insert_user(app, "autre@example.org", "Quelqu'un d'autre")
    # Par le nom, sans accents ni casse.
    by_name = await admin.get("/api/users", params={"q": "perez"})
    assert by_name.status_code == 200
    emails = [u["email"] for u in by_name.json()["users"]]
    assert "jose@example.org" in emails
    assert "autre@example.org" not in emails
    # Par l'email partiel.
    by_email = await admin.get("/api/users", params={"q": "JOSE@"})
    assert [u["email"] for u in by_email.json()["users"]] == ["jose@example.org"]


async def test_aucun_resultat(admin: AsyncClient) -> None:
    response = await admin.get("/api/users", params={"q": "introuvable-xyz"})
    assert response.status_code == 200
    assert response.json()["users"] == []
    assert response.json()["total"] == 0


async def test_filtre_par_profil(admin: AsyncClient, app: FastAPI) -> None:
    pid = await _profile(admin, "Lecteur", ["secrets:read"])
    user_id = await _insert_user(app, "lecteur@example.org", "Lecteur Un")
    await admin.put(f"/api/users/{user_id}/profile", json={"profile_id": pid})
    filtered = await admin.get("/api/users", params={"profile_id": pid})
    assert [u["email"] for u in filtered.json()["users"]] == ["lecteur@example.org"]


async def test_assignation_effective_a_la_requete_suivante(
    admin: AsyncClient, app: FastAPI
) -> None:
    pid = await _profile(admin, "Lecteur secrets", ["secrets:read"])
    await create_plain_user(app, "membre@example.org", "mot-de-passe-simple")
    async with app.state.pool.acquire() as conn:
        user_id = await conn.fetchval("SELECT id FROM users WHERE email = 'membre@example.org'")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as member:
        await login(member, "membre@example.org", "mot-de-passe-simple")
        # Avant assignation : pas de capacité.
        assert (await member.get("/api/secrets")).status_code == 403
        await admin.put(f"/api/users/{user_id}/profile", json={"profile_id": pid})
        # Après, sans reconnexion : la capacité vaut.
        assert (await member.get("/api/secrets")).status_code == 200


async def test_anti_verrouillage_dernier_admin(admin: AsyncClient, app: FastAPI) -> None:
    pid = await _profile(admin, "Standard", ["secrets:read"])
    admin_id = await _admin_user_id(app)
    response = await admin.put(f"/api/users/{admin_id}/profile", json={"profile_id": pid})
    assert response.status_code == 409
    assert response.json()["error"] == "admin_lockout"


@pytest.fixture
async def user_client(admin: AsyncClient, app: FastAPI) -> AsyncIterator[AsyncClient]:
    await create_plain_user(app, "simple@example.org", "mot-de-passe-simple")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as second:
        await login(second, "simple@example.org", "mot-de-passe-simple")
        yield second


async def test_non_admin_refuse(user_client: AsyncClient) -> None:
    assert (await user_client.get("/api/users")).status_code == 403
