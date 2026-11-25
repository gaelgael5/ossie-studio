"""Règle libellé → slug sur les profils, connecteurs OIDC et certifications :
slug fourni gardé, déduit sinon, invalide refusé, pris refusé, figé ensuite."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from tests.conftest import bootstrap_admin
from tests.space_fixture import Rebased, make_api_space


async def test_profil_slug_fourni_deduit_invalide_pris(client: AsyncClient) -> None:
    await bootstrap_admin(client)
    given = await client.post("/api/profiles", json={"name": "Lecteur data", "slug": "lecteur"})
    assert given.status_code == 201, given.text
    assert given.json()["slug"] == "lecteur"
    derived = await client.post("/api/profiles", json={"name": "Équipe Qualité"})
    assert derived.json()["slug"] == "equipe-qualite"
    bad = await client.post("/api/profiles", json={"name": "X", "slug": "Pas Bon"})
    assert bad.status_code == 400
    assert "profile_slug_invalid" in bad.json()["error_args"]["fields"]
    taken = await client.post("/api/profiles", json={"name": "Autre", "slug": "lecteur"})
    assert taken.status_code == 409
    assert taken.json()["error"] == "profile_slug_taken"


async def test_profil_slug_fige_et_copie_suffixee(client: AsyncClient) -> None:
    await bootstrap_admin(client)
    created = (await client.post("/api/profiles", json={"name": "Lecteur", "slug": "lect"})).json()
    await client.put(
        f"/api/profiles/{created['id']}",
        json={"name": "Lecteur renommé", "slug": "autre", "capabilities": []},
    )
    copy = (await client.post(f"/api/profiles/{created['id']}/duplicate")).json()
    profiles = {p["id"]: p for p in (await client.get("/api/profiles")).json()["profiles"]}
    assert profiles[created["id"]]["slug"] == "lect"
    assert profiles[copy["id"]]["slug"] == "lect-copie"


async def test_certification_slug(client: AsyncClient, app: FastAPI, tmp_path: Path) -> None:
    gov = Rebased(client, await make_api_space(client, app, tmp_path))
    first = await gov.post(
        "/api/governance/certifications", json={"name": "Or massif", "rank": 1, "tag": "c/or"}
    )
    assert first.json()["slug"] == "or-massif"
    taken = await gov.post(
        "/api/governance/certifications",
        json={"name": "Autre", "rank": 2, "tag": "c/autre", "slug": "or-massif"},
    )
    assert taken.status_code == 409
    assert taken.json()["error"] == "certification_slug_taken"
    updated = await gov.put(
        f"/api/governance/certifications/{first.json()['id']}",
        json={"name": "Or", "rank": 1, "tag": "c/or", "slug": "change"},
    )
    assert updated.json()["slug"] == "or-massif"  # figé


@pytest.mark.parametrize("slug", ["Pas Bon", "-x"])
async def test_certification_slug_invalide(
    client: AsyncClient, app: FastAPI, tmp_path: Path, slug: str
) -> None:
    gov = Rebased(client, await make_api_space(client, app, tmp_path))
    response = await gov.post(
        "/api/governance/certifications",
        json={"name": "X", "rank": 1, "tag": "c/x", "slug": slug},
    )
    assert response.status_code == 400
    assert "certification_slug_invalid" in response.json()["error_args"]["fields"]


async def test_slug_deduit_pris_est_suffixe(client: AsyncClient) -> None:
    # « Équipe » et « Equipe » donnent le même slug déduit : le second est
    # suffixé (l'utilisateur n'a rien saisi, aucune erreur ne lui revient).
    await bootstrap_admin(client)
    first = await client.post("/api/profiles", json={"name": "Équipe"})
    second = await client.post("/api/profiles", json={"name": "Equipe"})
    assert (first.json()["slug"], second.json()["slug"]) == ("equipe", "equipe-2")
