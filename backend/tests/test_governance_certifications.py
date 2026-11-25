from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.conftest import create_plain_user, login
from tests.space_fixture import Rebased, make_api_space


@pytest.fixture
async def admin(client: AsyncClient, app: FastAPI, tmp_path: Path) -> Rebased:
    # La gouvernance vit dans le dépôt de l'espace : routes de la branche.
    return Rebased(client, await make_api_space(client, app, tmp_path))


async def test_presets_creent_les_trois_niveaux(admin: Rebased) -> None:
    response = await admin.post("/api/governance/certifications/presets")
    assert response.status_code == 200, response.text
    certs = response.json()["certifications"]
    assert [(c["name"], c["rank"], c["tag"]) for c in certs] == [
        ("Or", 1, "certification/or"),
        ("Argent", 2, "certification/argent"),
        ("Bronze", 3, "certification/bronze"),
    ]
    # Idempotent : un second appel ne duplique pas.
    again = await admin.post("/api/governance/certifications/presets")
    assert len(again.json()["certifications"]) == 3


async def test_creer_et_lister_trie_par_rang(admin: Rebased) -> None:
    await admin.post(
        "/api/governance/certifications",
        json={"name": "Bronze", "rank": 3, "tag": "certification/bronze"},
    )
    await admin.post(
        "/api/governance/certifications",
        json={"name": "Or", "rank": 1, "tag": "Certification/OR", "icon": "🥇"},
    )
    listing = await admin.get("/api/governance/certifications")
    certs = listing.json()["certifications"]
    assert [c["name"] for c in certs] == ["Or", "Bronze"]  # trié par rang
    assert certs[0]["tag"] == "certification/or"  # tag normalisé


async def test_tag_deja_pris_refuse(admin: Rebased) -> None:
    await admin.post(
        "/api/governance/certifications", json={"name": "Or", "rank": 1, "tag": "certification/or"}
    )
    again = await admin.post(
        "/api/governance/certifications",
        json={"name": "Or bis", "rank": 2, "tag": "certification/or"},
    )
    assert again.status_code == 409
    assert again.json()["error"] == "certification_tag_taken"


async def test_validation_champs(admin: Rebased) -> None:
    bad_name = await admin.post(
        "/api/governance/certifications", json={"name": "  ", "rank": 1, "tag": "certification/x"}
    )
    assert "certification_name_required" in bad_name.json()["error_args"]["fields"]
    bad_rank = await admin.post(
        "/api/governance/certifications", json={"name": "X", "rank": 0, "tag": "certification/x"}
    )
    assert "certification_rank_invalid" in bad_rank.json()["error_args"]["fields"]
    bad_tag = await admin.post(
        "/api/governance/certifications", json={"name": "X", "rank": 1, "tag": "Pas Valide"}
    )
    assert "tag_id_invalid" in bad_tag.json()["error_args"]["fields"]


async def test_modifier_et_supprimer(admin: Rebased) -> None:
    created = await admin.post(
        "/api/governance/certifications", json={"name": "Or", "rank": 1, "tag": "certification/or"}
    )
    cert_id = created.json()["id"]
    updated = await admin.put(
        f"/api/governance/certifications/{cert_id}",
        json={"name": "Or", "rank": 1, "tag": "certification/or", "restricted": True,
              "criteria": "# Critères\n- fiable"},
    )
    assert updated.status_code == 200
    assert updated.json()["restricted"] is True
    assert updated.json()["criteria"].startswith("# Critères")

    deleted = await admin.delete(f"/api/governance/certifications/{cert_id}")
    assert deleted.status_code == 204
    assert (await admin.get("/api/governance/certifications")).json()["certifications"] == []


async def test_modifier_inexistant_404(admin: Rebased) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    response = await admin.put(
        f"/api/governance/certifications/{missing}",
        json={"name": "X", "rank": 1, "tag": "certification/x"},
    )
    assert response.status_code == 404
    assert response.json()["error"] == "certification_not_found"


@pytest.fixture
async def user_client(admin: Rebased, app: FastAPI) -> AsyncIterator[Rebased]:
    await create_plain_user(app, "simple@example.org", "mot-de-passe-simple")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as second:
        await login(second, "simple@example.org", "mot-de-passe-simple")
        yield Rebased(second, admin.space)


async def test_non_admin_refuse(user_client: Rebased) -> None:
    assert (await user_client.get("/api/governance/certifications")).status_code == 403
    created = await user_client.post(
        "/api/governance/certifications", json={"name": "X", "rank": 1, "tag": "certification/x"}
    )
    assert created.status_code == 403
