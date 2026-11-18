from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.authz.catalog import ALL_CAPABILITIES
from tests.conftest import bootstrap_admin, create_plain_user, login


@pytest.fixture
async def admin(client: AsyncClient) -> AsyncClient:
    await bootstrap_admin(client)
    return client


async def _admin_profile_id(admin: AsyncClient) -> str:
    profiles = (await admin.get("/api/profiles")).json()["profiles"]
    return next(p["id"] for p in profiles if p["is_admin"])


async def test_installation_a_le_profil_admin_avec_toutes_les_capacites(admin: AsyncClient) -> None:
    body = (await admin.get("/api/profiles")).json()
    admin_profile = next(p for p in body["profiles"] if p["is_admin"])
    assert set(admin_profile["capabilities"]) == set(ALL_CAPABILITIES)
    # Le catalogue est exposé pour la grille.
    assert any(d["domain"] == "secrets" for d in body["catalog"])
    # Le premier administrateur a toutes les capacités (via la session).
    session = (await admin.get("/api/session")).json()
    assert "profiles:write" in session["capabilities"]
    assert "secrets:write" in session["capabilities"]


async def test_creer_profil_cases_independantes(admin: AsyncClient) -> None:
    # Publier sans modifier est accepté, et l'inverse aussi (jamais une échelle).
    a = await admin.post(
        "/api/profiles", json={"name": "Publieur", "capabilities": ["spaces:publish"]}
    )
    assert a.status_code == 201, a.text
    assert a.json()["capabilities"] == ["spaces:publish"]
    b = await admin.post(
        "/api/profiles", json={"name": "Brancheur", "capabilities": ["spaces:branch"]}
    )
    assert b.status_code == 201


async def test_profil_sans_capacite_accepte(admin: AsyncClient) -> None:
    response = await admin.post("/api/profiles", json={"name": "Sans accès", "capabilities": []})
    assert response.status_code == 201
    assert response.json()["capabilities"] == []


async def test_nom_deja_pris_refuse(admin: AsyncClient) -> None:
    await admin.post("/api/profiles", json={"name": "Lecteur", "capabilities": ["secrets:read"]})
    again = await admin.post(
        "/api/profiles", json={"name": "Lecteur", "capabilities": ["oidc:read"]}
    )
    assert again.status_code == 409
    assert again.json()["error"] == "profile_name_taken"


async def test_dupliquer(admin: AsyncClient) -> None:
    src = await admin.post(
        "/api/profiles", json={"name": "Base", "capabilities": ["secrets:read", "oidc:read"]}
    )
    copy = await admin.post(f"/api/profiles/{src.json()['id']}/duplicate")
    assert copy.status_code == 201
    assert copy.json()["name"] == "Base (copie)"
    assert set(copy.json()["capabilities"]) == {"secrets:read", "oidc:read"}


async def test_admin_protege(admin: AsyncClient) -> None:
    admin_id = await _admin_profile_id(admin)
    updated = await admin.put(
        f"/api/profiles/{admin_id}", json={"name": "Administrateur", "capabilities": []}
    )
    assert updated.status_code == 409
    assert updated.json()["error"] == "profile_admin_protected"
    deleted = await admin.delete(f"/api/profiles/{admin_id}")
    assert deleted.status_code == 409


async def test_profil_utilise_refuse(admin: AsyncClient, app: FastAPI) -> None:
    pid = (
        await admin.post("/api/profiles", json={"name": "Assigné", "capabilities": []})
    ).json()["id"]
    await create_plain_user(app, "membre@example.org", "mot-de-passe-simple")
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET profile_id = $1 WHERE email = 'membre@example.org'", uuid.UUID(pid)
        )
    # La liste annonce le nombre d'utilisateurs ; la suppression est refusée.
    profiles = (await admin.get("/api/profiles")).json()["profiles"]
    assert next(p for p in profiles if p["id"] == pid)["user_count"] == 1
    deleted = await admin.delete(f"/api/profiles/{pid}")
    assert deleted.status_code == 409
    assert deleted.json()["error"] == "profile_in_use"
    assert deleted.json()["error_args"]["count"] == 1


@pytest.fixture
async def reader(admin: AsyncClient, app: FastAPI) -> AsyncIterator[tuple[AsyncClient, str]]:
    pid = (
        await admin.post(
            "/api/profiles", json={"name": "Lecteur secrets", "capabilities": ["secrets:read"]}
        )
    ).json()["id"]
    await create_plain_user(app, "lecteur@example.org", "mot-de-passe-simple")
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET profile_id = $1 WHERE email = 'lecteur@example.org'", uuid.UUID(pid)
        )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as second:
        await login(second, "lecteur@example.org", "mot-de-passe-simple")
        yield second, pid


async def test_capacite_garde_les_routes_et_retrait_immediat(
    admin: AsyncClient, reader: tuple[AsyncClient, str]
) -> None:
    user_client, pid = reader
    # A la capacité « secrets:read » → GET passe ; pas « secrets:write » → POST 403.
    assert (await user_client.get("/api/secrets")).status_code == 200
    created = await user_client.post(
        "/api/secrets", json={"slug": "x", "label": "X", "category": "github", "value": "v"}
    )
    assert created.status_code == 403
    # Pas la capacité profils non plus.
    assert (await user_client.get("/api/profiles")).status_code == 403
    # Retrait de la capacité → vaut dès la requête suivante, sans reconnexion.
    await admin.put(f"/api/profiles/{pid}", json={"name": "Lecteur secrets", "capabilities": []})
    assert (await user_client.get("/api/secrets")).status_code == 403
