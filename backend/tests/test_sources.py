from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.sources.manifest import (
    ManifestError,
    manifest_from_mapping,
    parse_manifest,
)
from ossie_studio.sources.paths import is_valid_source_slug, safe_source_path
from ossie_studio.sources.service import read_sources_tree
from tests.conftest import bootstrap_admin, create_plain_user, login, wire_fake_idp
from tests.fake_forge import GOOD_TOKEN, fake_github_app


# ---------------------------------------------------------------------------
# Unités : garde de chemin, manifeste, lecture d'arbre (sans git)
# ---------------------------------------------------------------------------
def test_slug_valide_et_invalide() -> None:
    assert is_valid_source_slug("api-boutique")
    assert not is_valid_source_slug("API")
    assert not is_valid_source_slug("../evil")
    assert not is_valid_source_slug("a/b")
    assert not is_valid_source_slug("")


def test_safe_source_path_refuse_la_traversee(tmp_path: Path) -> None:
    (tmp_path / "sources").mkdir()
    assert safe_source_path(tmp_path, "ok").name == "ok"
    with pytest.raises(ValueError):
        safe_source_path(tmp_path, "../../etc")


def test_manifest_valide_et_rejets() -> None:
    manifest = manifest_from_mapping(
        {
            "version": 1,
            "slug": "cmd",
            "label": "Commandes",
            "type": "git",
            "connexion": "c1",
            "parametres": {"depot": "org/api", "branche": "main"},
        }
    )
    assert manifest.type == "git"
    # Clé inconnue refusée.
    with pytest.raises(ManifestError) as exc:
        manifest_from_mapping(
            {
                "version": 1,
                "slug": "cmd",
                "label": "Commandes",
                "type": "git",
                "connexion": "c1",
                "parametres": {"depot": "org/api", "branche": "main"},
                "schema": "x",
            }
        )
    assert "unknown_key:schema" in exc.value.fields
    # Git sans dépôt ni branche refusé.
    with pytest.raises(ManifestError) as exc2:
        manifest_from_mapping(
            {
                "version": 1,
                "slug": "c",
                "label": "l",
                "type": "git",
                "connexion": "c1",
                "parametres": {},
            }
        )
    assert "depot_required" in exc2.value.fields
    assert "branche_required" in exc2.value.fields


def test_manifest_roundtrip_yaml() -> None:
    from ossie_studio.sources.manifest import dump_manifest

    manifest = manifest_from_mapping(
        {
            "version": 1,
            "slug": "cmd",
            "label": "Commandes",
            "type": "postgres",
            "connexion": "db1",
            "parametres": {"base": "ventes", "schemas": ["public"]},
        }
    )
    text = dump_manifest(manifest)
    assert parse_manifest(text).parametres["base"] == "ventes"


def test_read_sources_tree_signale_les_erreurs(tmp_path: Path) -> None:
    base = tmp_path / "sources"
    (base / "bonne").mkdir(parents=True)
    (base / "bonne" / "manifest.yaml").write_text(
        "version: 1\nslug: bonne\nlabel: B\ntype: git\nconnexion: c1\n"
        "parametres:\n  depot: org/api\n  branche: main\n",
        encoding="utf-8",
    )
    (base / "sans-manifeste").mkdir()
    entries = {e.slug: e for e in read_sources_tree(tmp_path)}
    assert entries["bonne"].manifest is not None
    assert entries["sans-manifeste"].error == "manifest_missing"


# ---------------------------------------------------------------------------
# Intégration : API git-backed
# ---------------------------------------------------------------------------
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


@pytest.fixture
async def branch(
    client: AsyncClient, app: FastAPI, tmp_path: Path
) -> tuple[AsyncClient, str, str, str]:
    wire_fake_idp(app, fake_github_app())
    await bootstrap_admin(client)
    await client.post(
        "/api/secrets",
        json={"slug": "gh", "label": "Jeton", "category": "github", "value": GOOD_TOKEN},
    )
    await client.post(
        "/api/connections",
        json={
            "label": "GitHub",
            "forge": "github",
            "url": "https://github.com",
            "secret_slug": "gh",
        },
    )
    connection = (await client.get("/api/connections")).json()["connections"][0]
    url = _seed_bare(tmp_path / "git")
    async with app.state.pool.acquire() as conn:
        space_id = await conn.fetchval(
            "INSERT INTO git_spaces (name, connection_id, repo_url, last_probe_status) "
            "VALUES ($1, $2, $3, 'valid') RETURNING id",
            "modele",
            uuid.UUID(connection["id"]),
            url,
        )
    base = await client.post(f"/api/spaces/{space_id}/branches/base", json={"label": "Principale"})
    branch_id = base.json()["id"]
    # La source référence la connexion par son slug (lisible et stable).
    return client, str(space_id), str(branch_id), connection["slug"]


def _git_body(connection_id: str, **over: object) -> dict[str, object]:
    body: dict[str, object] = {
        "slug": "api-boutique",
        "label": "API boutique",
        "type": "git",
        "connexion": connection_id,
        "parametres": {"depot": "octocat/r0", "branche": "main", "repertoires": ["src"]},
    }
    body.update(over)
    return body


async def test_declarer_git_source_ecrit_le_manifeste(
    branch: tuple[AsyncClient, str, str, str], tmp_path: Path
) -> None:
    client, space_id, branch_id, connection_id = branch
    base = f"/api/spaces/{space_id}/branches/{branch_id}/sources"
    r = await client.post(base, json=_git_body(connection_id))
    assert r.status_code == 201, r.text
    listed = (await client.get(base)).json()["sources"]
    assert any(s["slug"] == "api-boutique" and s["ok"] for s in listed)
    # Le manifeste existe dans la copie de travail et ne contient aucun secret.
    manifest = (
        tmp_path / "work" / space_id / "principale" / "sources" / "api-boutique" / "manifest.yaml"
    )
    content = manifest.read_text(encoding="utf-8")
    assert "type: git" in content
    assert GOOD_TOKEN not in content
    assert connection_id in content  # référence la connexion, pas le secret


async def test_slug_deja_pris_refuse(branch: tuple[AsyncClient, str, str, str]) -> None:
    client, space_id, branch_id, connection_id = branch
    base = f"/api/spaces/{space_id}/branches/{branch_id}/sources"
    await client.post(base, json=_git_body(connection_id))
    again = await client.post(base, json=_git_body(connection_id))
    assert again.status_code == 409
    assert again.json()["error"] == "source_slug_taken"


async def test_branche_inexistante_refuse(branch: tuple[AsyncClient, str, str, str]) -> None:
    client, space_id, branch_id, connection_id = branch
    base = f"/api/spaces/{space_id}/branches/{branch_id}/sources"
    r = await client.post(
        base,
        json=_git_body(connection_id, parametres={"depot": "octocat/r0", "branche": "absente"}),
    )
    assert r.status_code == 400
    assert "branche_not_found" in r.json()["error_args"]["fields"]


async def test_connexion_mauvais_genre_refuse(branch: tuple[AsyncClient, str, str, str]) -> None:
    client, space_id, branch_id, _ = branch
    base = f"/api/spaces/{space_id}/branches/{branch_id}/sources"
    r = await client.post(base, json=_git_body(str(uuid.uuid4())))
    assert r.status_code == 400
    assert "connexion_invalid" in r.json()["error_args"]["fields"]


async def test_cle_inconnue_refuse(branch: tuple[AsyncClient, str, str, str]) -> None:
    client, space_id, branch_id, connection_id = branch
    base = f"/api/spaces/{space_id}/branches/{branch_id}/sources"
    r = await client.post(
        base,
        json=_git_body(
            connection_id, parametres={"depot": "octocat/r0", "branche": "main", "zzz": "x"}
        ),
    )
    assert r.status_code == 400
    assert "unknown_key:parametres.zzz" in r.json()["error_args"]["fields"]


async def test_slug_traversant_refuse(branch: tuple[AsyncClient, str, str, str]) -> None:
    client, space_id, branch_id, connection_id = branch
    base = f"/api/spaces/{space_id}/branches/{branch_id}/sources"
    r = await client.post(base, json=_git_body(connection_id, slug="../evil"))
    assert r.status_code == 400
    assert "slug_invalid" in r.json()["error_args"]["fields"]


async def test_supprimer_source(branch: tuple[AsyncClient, str, str, str]) -> None:
    client, space_id, branch_id, connection_id = branch
    base = f"/api/spaces/{space_id}/branches/{branch_id}/sources"
    await client.post(base, json=_git_body(connection_id))
    deleted = await client.delete(f"{base}/api-boutique")
    assert deleted.status_code == 204
    assert (await client.get(base)).json()["sources"] == []


async def test_liste_reservee_a_la_capacite(
    branch: tuple[AsyncClient, str, str, str], app: FastAPI
) -> None:
    _client, space_id, branch_id, _ = branch
    base = f"/api/spaces/{space_id}/branches/{branch_id}/sources"
    await create_plain_user(app, "lambda@example.org", "mot-de-passe-solide")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        await login(other, "lambda@example.org", "mot-de-passe-solide")
        r = await other.get(base)
    assert r.status_code == 403
