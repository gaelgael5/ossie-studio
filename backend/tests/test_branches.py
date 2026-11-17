from __future__ import annotations

import subprocess
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.conftest import bootstrap_admin, create_plain_user, login, wire_fake_idp
from tests.fake_forge import GOOD_TOKEN, fake_github_app


def _seed_bare(tmp_path: Path) -> str:
    bare = tmp_path / "repo.git"
    subprocess.run(["git", "init", "-q", "-b", "main", "--bare", str(bare)], check=True)
    work = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    for args in (
        ["-c", "user.email=a@a", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "i"],
        ["remote", "add", "origin", str(bare)],
        ["push", "-q", "origin", "main"],
    ):
        subprocess.run(["git", "-C", str(work), *args], check=True, capture_output=True)
    return f"file://{bare}"


def _ref_sha(url: str, ref: str) -> str | None:
    out = subprocess.run(
        ["git", "ls-remote", url.removeprefix("file://"), ref],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.split("\t")[0] if out.strip() else None


@pytest.fixture
async def space(client: AsyncClient, app: FastAPI, tmp_path: Path) -> tuple[AsyncClient, str, str]:
    wire_fake_idp(app, fake_github_app())
    await bootstrap_admin(client)
    await client.post(
        "/api/connections",
        json={
            "label": "GitHub",
            "forge": "github",
            "url": "https://github.com",
            "token": GOOD_TOKEN,
        },
    )
    connection_id = (await client.get("/api/connections")).json()["connections"][0]["id"]
    url = _seed_bare(tmp_path / "git")
    async with app.state.pool.acquire() as conn:
        space_id = await conn.fetchval(
            "INSERT INTO git_spaces (name, connection_id, repo_url, last_probe_status) "
            "VALUES ($1, $2, $3, 'valid') RETURNING id",
            "modele",
            uuid.UUID(connection_id),
            url,
        )
    return client, str(space_id), url


async def test_creer_base_cree_la_branche(space: tuple[AsyncClient, str, str]) -> None:
    client, space_id, url = space
    response = await client.post(
        f"/api/spaces/{space_id}/branches/base", json={"label": "Principale"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["git_ref"] == "work/principale"
    assert _ref_sha(url, "refs/heads/work/principale") is not None


async def test_creer_base_ne_touche_pas_la_racine(space: tuple[AsyncClient, str, str]) -> None:
    client, space_id, url = space
    before = _ref_sha(url, "refs/heads/main")
    await client.post(f"/api/spaces/{space_id}/branches/base", json={"label": "Principale"})
    assert _ref_sha(url, "refs/heads/main") == before


async def test_rejeu_base_signale_sans_doublon(space: tuple[AsyncClient, str, str]) -> None:
    client, space_id, _ = space
    await client.post(f"/api/spaces/{space_id}/branches/base", json={"label": "Principale"})
    again = await client.post(f"/api/spaces/{space_id}/branches/base", json={"label": "Principale"})
    assert again.status_code == 201
    assert again.json()["already_exists"] is True


async def test_creer_fille_depuis_la_base(space: tuple[AsyncClient, str, str]) -> None:
    client, space_id, url = space
    await client.post(f"/api/spaces/{space_id}/branches/base", json={"label": "Principale"})
    response = await client.post(
        f"/api/spaces/{space_id}/branches/child", json={"label": "Feature A"}
    )
    assert response.status_code == 201
    assert response.json()["git_ref"] == "work/feature-a"
    assert _ref_sha(url, "refs/heads/work/feature-a") is not None


async def test_fille_sans_base_refuse(space: tuple[AsyncClient, str, str]) -> None:
    client, space_id, _ = space
    response = await client.post(
        f"/api/spaces/{space_id}/branches/child", json={"label": "Feature A"}
    )
    assert response.status_code == 409
    assert response.json()["error"] == "space_base_required"


async def test_slug_deja_pris_autre_libelle_refuse(space: tuple[AsyncClient, str, str]) -> None:
    client, space_id, _ = space
    await client.post(f"/api/spaces/{space_id}/branches/base", json={"label": "Principale"})
    await client.post(f"/api/spaces/{space_id}/branches/child", json={"label": "Feature A"})
    collide = await client.post(
        f"/api/spaces/{space_id}/branches/child", json={"label": "feature a"}
    )
    assert collide.status_code == 409
    assert collide.json()["error"] == "branch_slug_taken"


async def test_libelle_non_sluggable_refuse(space: tuple[AsyncClient, str, str]) -> None:
    client, space_id, _ = space
    response = await client.post(f"/api/spaces/{space_id}/branches/base", json={"label": "!!!"})
    assert response.status_code == 400
    assert "label_invalid" in response.json()["error_args"]["fields"]


async def test_branche_regardee_retenue(space: tuple[AsyncClient, str, str]) -> None:
    client, space_id, _ = space
    base = await client.post(f"/api/spaces/{space_id}/branches/base", json={"label": "Principale"})
    branch_id = base.json()["id"]
    put = await client.put(f"/api/spaces/{space_id}/branches/viewed", json={"branch_id": branch_id})
    assert put.status_code == 200
    listing = (await client.get(f"/api/spaces/{space_id}/branches")).json()
    assert listing["viewed_branch_id"] == branch_id


async def test_espace_inexistant_404(space: tuple[AsyncClient, str, str]) -> None:
    client, _space_id, _ = space
    response = await client.post(f"/api/spaces/{uuid.uuid4()}/branches/base", json={"label": "X"})
    assert response.status_code == 404
    assert response.json()["error"] == "space_not_found"


def _writer(text: str) -> Callable[[Path], None]:
    def write(workdir: Path) -> None:
        (workdir / "sources" / "data.txt").write_text(text)

    return write


async def test_lister_puis_publier_les_commits(
    space: tuple[AsyncClient, str, str], app: FastAPI
) -> None:
    client, space_id, url = space
    base = await client.post(f"/api/spaces/{space_id}/branches/base", json={"label": "Principale"})
    branch_id = base.json()["id"]
    # Un commit produit par le mécanisme interne (future descente de sources).
    await app.state.workdir_manager.commit_to_branch(
        uuid.UUID(space_id), "principale", url, "x", None,
        "Alice", "alice@example.org", "ajoute data", _writer("v1"),
    )

    pending = await client.get(f"/api/spaces/{space_id}/branches/{branch_id}/unpublished")
    assert pending.status_code == 200, pending.text
    body = pending.json()
    assert body["count"] == 1
    assert body["commits"][0]["message"] == "ossie: ajoute data"
    assert body["commits"][0]["author"] == "Alice"

    published = await client.post(f"/api/spaces/{space_id}/branches/{branch_id}/publish")
    assert published.status_code == 200, published.text
    assert published.json()["published"] == 1
    assert _ref_sha(url, "refs/heads/work/principale") is not None

    after = await client.get(f"/api/spaces/{space_id}/branches/{branch_id}/unpublished")
    assert after.json()["count"] == 0


async def test_publier_rien_en_attente(space: tuple[AsyncClient, str, str]) -> None:
    client, space_id, _ = space
    base = await client.post(f"/api/spaces/{space_id}/branches/base", json={"label": "Principale"})
    branch_id = base.json()["id"]
    published = await client.post(f"/api/spaces/{space_id}/branches/{branch_id}/publish")
    assert published.status_code == 200
    assert published.json()["published"] == 0


async def test_unpublished_branche_inconnue_404(space: tuple[AsyncClient, str, str]) -> None:
    client, space_id, _ = space
    response = await client.get(
        f"/api/spaces/{space_id}/branches/{uuid.uuid4()}/unpublished"
    )
    assert response.status_code == 404
    assert response.json()["error"] == "branch_not_found"


async def test_enregistrer_et_lister_une_version(space: tuple[AsyncClient, str, str]) -> None:
    client, space_id, url = space
    base = await client.post(f"/api/spaces/{space_id}/branches/base", json={"label": "Principale"})
    branch_id = base.json()["id"]
    created = await client.post(
        f"/api/spaces/{space_id}/branches/{branch_id}/versions", json={"label": "Première"}
    )
    assert created.status_code == 201, created.text
    assert created.json()["number"] == 1
    assert created.json()["already_exists"] is False
    assert _ref_sha(url, "refs/tags/version/1") is not None

    listing = await client.get(f"/api/spaces/{space_id}/branches/{branch_id}/versions")
    assert listing.status_code == 200
    versions = listing.json()["versions"]
    assert len(versions) == 1
    assert versions[0]["number"] == 1
    assert versions[0]["label"] == "Première"


async def test_version_libelle_invalide(space: tuple[AsyncClient, str, str]) -> None:
    client, space_id, _ = space
    base = await client.post(f"/api/spaces/{space_id}/branches/base", json={"label": "Principale"})
    branch_id = base.json()["id"]
    vide = await client.post(
        f"/api/spaces/{space_id}/branches/{branch_id}/versions", json={"label": "  "}
    )
    assert vide.status_code == 400
    assert "label_required" in vide.json()["error_args"]["fields"]
    multi = await client.post(
        f"/api/spaces/{space_id}/branches/{branch_id}/versions", json={"label": "a\nb"}
    )
    assert multi.status_code == 400
    assert "label_single_line" in multi.json()["error_args"]["fields"]


async def test_revenir_a_une_version(
    space: tuple[AsyncClient, str, str], app: FastAPI
) -> None:
    client, space_id, url = space
    base = await client.post(f"/api/spaces/{space_id}/branches/base", json={"label": "Principale"})
    branch_id = base.json()["id"]
    await client.post(
        f"/api/spaces/{space_id}/branches/{branch_id}/versions", json={"label": "Première"}
    )
    # La tête avance et est publiée, sinon « revenir » serait déjà courant.
    await app.state.workdir_manager.commit_to_branch(
        uuid.UUID(space_id), "principale", url, "x", None,
        "Alice", "a@a", "change", _writer("v2"),
    )
    await app.state.workdir_manager.publish(uuid.UUID(space_id), "principale", url, "x", None)

    restore = await client.post(
        f"/api/spaces/{space_id}/branches/{branch_id}/versions/1/restore"
    )
    assert restore.status_code == 200, restore.text
    assert restore.json()["created"] is True
    assert restore.json()["already_current"] is False
    # Le retour est un commit local, à publier.
    pending = await client.get(f"/api/spaces/{space_id}/branches/{branch_id}/unpublished")
    assert pending.json()["count"] == 1


@pytest.fixture
async def user_client(
    space: tuple[AsyncClient, str, str], app: FastAPI
) -> AsyncIterator[AsyncClient]:
    await create_plain_user(app, "simple@example.org", "mot-de-passe-simple")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as second:
        await login(second, "simple@example.org", "mot-de-passe-simple")
        yield second


async def test_non_admin_refus_403(
    user_client: AsyncClient, space: tuple[AsyncClient, str, str]
) -> None:
    _, space_id, _ = space
    assert (await user_client.get(f"/api/spaces/{space_id}/branches")).status_code == 403
    creation = await user_client.post(f"/api/spaces/{space_id}/branches/base", json={"label": "X"})
    assert creation.status_code == 403
    bid = uuid.uuid4()
    listing = await user_client.get(f"/api/spaces/{space_id}/branches/{bid}/versions")
    assert listing.status_code == 403
    tag = await user_client.post(
        f"/api/spaces/{space_id}/branches/{bid}/versions", json={"label": "X"}
    )
    assert tag.status_code == 403
