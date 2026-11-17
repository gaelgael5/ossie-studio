from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.spaces.git_probe import ProbeResult
from tests.conftest import (
    bootstrap_admin,
    create_plain_user,
    db_fetchval,
    login,
    wire_fake_idp,
    wire_git_probe,
)
from tests.fake_forge import GOOD_TOKEN, fake_github_app

REPO = "https://github.com/acme/app"


def _ok_probe() -> ProbeResult:
    return ProbeResult(ok=True, verify_branch="ossie-verify/ab", verify_tag="ossie-verify-ab")


def _ko_probe() -> ProbeResult:
    return ProbeResult(
        ok=False,
        verify_branch="ossie-verify/ab",
        verify_tag="ossie-verify-ab",
        failed_right="push_commit",
        cause="write_denied",
    )


async def _make_connection(client: AsyncClient) -> str:
    response = await client.post(
        "/api/connections",
        json={
            "label": "GitHub",
            "forge": "github",
            "url": "https://github.com",
            "token": GOOD_TOKEN,
        },
    )
    assert response.status_code == 201, response.text
    body = (await client.get("/api/connections")).json()
    return body["connections"][0]["id"]


@pytest.fixture
async def admin_client(client: AsyncClient, app: FastAPI) -> AsyncClient:
    wire_fake_idp(app, fake_github_app())
    await bootstrap_admin(client)
    return client


async def test_creation_apres_cinq_droits_enregistre_l_espace(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    connection_id = await _make_connection(admin_client)
    wire_git_probe(app, _ok_probe())
    response = await admin_client.post(
        "/api/spaces", json={"name": "modele", "connection_id": connection_id, "repo_url": REPO}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "modele"
    assert body["probe"]["ok"] is True
    assert await db_fetchval(app, "SELECT count(*) FROM git_spaces") == 1


async def test_un_droit_en_echec_refuse_et_nomme_le_droit(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    connection_id = await _make_connection(admin_client)
    wire_git_probe(app, _ko_probe())
    response = await admin_client.post(
        "/api/spaces", json={"name": "modele", "connection_id": connection_id, "repo_url": REPO}
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "space_probe_failed"
    assert body["error_args"]["right"] == "push_commit"
    assert body["error_args"]["cause"] == "write_denied"
    # Rien n'est enregistré.
    assert await db_fetchval(app, "SELECT count(*) FROM git_spaces") == 0


async def test_nom_deja_pris_refuse(admin_client: AsyncClient, app: FastAPI) -> None:
    connection_id = await _make_connection(admin_client)
    wire_git_probe(app, _ok_probe())
    await admin_client.post(
        "/api/spaces", json={"name": "modele", "connection_id": connection_id, "repo_url": REPO}
    )
    response = await admin_client.post(
        "/api/spaces",
        json={"name": "modele", "connection_id": connection_id, "repo_url": REPO + "-2"},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "space_name_taken"


async def test_meme_depot_refuse_en_nommant_l_espace(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    connection_id = await _make_connection(admin_client)
    wire_git_probe(app, _ok_probe())
    await admin_client.post(
        "/api/spaces", json={"name": "premier", "connection_id": connection_id, "repo_url": REPO}
    )
    response = await admin_client.post(
        "/api/spaces", json={"name": "second", "connection_id": connection_id, "repo_url": REPO}
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "space_repo_taken"
    assert body["error_args"]["space"] == "premier"


async def test_reprouver_met_a_jour_l_etat(admin_client: AsyncClient, app: FastAPI) -> None:
    connection_id = await _make_connection(admin_client)
    wire_git_probe(app, _ok_probe())
    await admin_client.post(
        "/api/spaces", json={"name": "modele", "connection_id": connection_id, "repo_url": REPO}
    )
    space_id = await db_fetchval(app, "SELECT id FROM git_spaces")
    wire_git_probe(app, _ko_probe())
    response = await admin_client.post(f"/api/spaces/{space_id}/probe")
    assert response.status_code == 200
    assert response.json()["ok"] is False
    status = await db_fetchval(app, "SELECT last_probe_status FROM git_spaces")
    assert status == "error"


async def test_suppression_espace_ne_touche_pas_au_depot(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    connection_id = await _make_connection(admin_client)
    wire_git_probe(app, _ok_probe())
    await admin_client.post(
        "/api/spaces", json={"name": "modele", "connection_id": connection_id, "repo_url": REPO}
    )
    space_id = await db_fetchval(app, "SELECT id FROM git_spaces")
    response = await admin_client.delete(f"/api/spaces/{space_id}")
    assert response.status_code == 204
    assert await db_fetchval(app, "SELECT count(*) FROM git_spaces") == 0


async def test_supprimer_connexion_utilisee_refuse_avec_les_espaces(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    connection_id = await _make_connection(admin_client)
    wire_git_probe(app, _ok_probe())
    await admin_client.post(
        "/api/spaces", json={"name": "modele", "connection_id": connection_id, "repo_url": REPO}
    )
    response = await admin_client.delete(f"/api/connections/{connection_id}")
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "connection_in_use"
    assert body["error_args"]["spaces"] == ["modele"]


async def test_champ_invalide_refuse(admin_client: AsyncClient) -> None:
    response = await admin_client.post(
        "/api/spaces", json={"name": "", "connection_id": "pas-un-uuid", "repo_url": "x"}
    )
    assert response.status_code == 400
    fields = response.json()["error_args"]["fields"]
    assert "space_name_required" in fields
    assert "connection_required" in fields
    assert "repo_url_invalid" in fields


@pytest.fixture
async def user_client(admin_client: AsyncClient, app: FastAPI) -> AsyncIterator[AsyncClient]:
    await create_plain_user(app, "simple@example.org", "mot-de-passe-simple")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as second:
        await login(second, "simple@example.org", "mot-de-passe-simple")
        yield second


async def test_non_admin_refus_403(user_client: AsyncClient) -> None:
    assert (await user_client.get("/api/spaces")).status_code == 403
    creation = await user_client.post(
        "/api/spaces",
        json={"name": "x", "connection_id": str(uuid.uuid4()), "repo_url": REPO},
    )
    assert creation.status_code == 403
