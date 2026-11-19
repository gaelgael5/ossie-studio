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


# ---------------------------------------------------------------------------
# Intégration : sources OpenAPI (dépôt de contrats)
# ---------------------------------------------------------------------------
_CONTRACT_31 = """openapi: 3.1.0
info:
  title: API paiement
  version: 2.0.0
paths:
  /commandes:
    post:
      requestBody:
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Commande'
      responses:
        '200':
          description: ok
components:
  schemas:
    Commande:
      type: object
      properties:
        note:
          type: [string, 'null']
    Ligne:
      type: object
"""

_SWAGGER_2 = 'swagger: "2.0"\ninfo:\n  title: x\n  version: 1\n'

_REF_MISSING = """openapi: 3.1.0
info:
  title: x
  version: 1.0.0
components:
  schemas:
    Commande:
      type: object
      properties:
        remise:
          $ref: '#/components/schemas/Remise'
"""


async def _declare_openapi(client: AsyncClient, base: str, slug: str = "api-paiement") -> None:
    r = await client.post(
        base,
        json={
            "slug": slug,
            "label": "API paiement",
            "type": "openapi",
            "connexion": "",
            "parametres": {},
        },
    )
    assert r.status_code == 201, r.text


async def test_declarer_openapi_sans_connexion(
    branch: tuple[AsyncClient, str, str, str], tmp_path: Path
) -> None:
    client, space_id, branch_id, _ = branch
    base = f"/api/spaces/{space_id}/branches/{branch_id}/sources"
    await _declare_openapi(client, base)
    listed = (await client.get(base)).json()["sources"]
    source = next(s for s in listed if s["slug"] == "api-paiement")
    assert source["type"] == "openapi"
    assert source["connexion"] == {"kind": "none"}
    manifest = (
        tmp_path / "work" / space_id / "principale" / "sources" / "api-paiement" / "manifest.yaml"
    )
    assert "type: openapi" in manifest.read_text(encoding="utf-8")


async def test_analyse_avant_depot_ne_rien_ecrire(
    branch: tuple[AsyncClient, str, str, str], tmp_path: Path
) -> None:
    client, space_id, branch_id, _ = branch
    base = f"/api/spaces/{space_id}/branches/{branch_id}/sources"
    await _declare_openapi(client, base)
    r = await client.post(
        f"{base}/api-paiement/contracts/analyze",
        json={"filename": "paiement.yaml", "content": _CONTRACT_31},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["openapi"] == "3.1.0"
    assert data["nb_schemas"] == 2
    assert data["nb_operations"] == 1
    assert data["version_proposee"] == "v2"
    # Rien n'a été rangé.
    contrats = tmp_path / "work" / space_id / "principale" / "sources" / "api-paiement" / "contrats"
    assert not contrats.exists()


async def test_depot_collecte_telechargement_retrait(
    branch: tuple[AsyncClient, str, str, str], tmp_path: Path
) -> None:
    client, space_id, branch_id, _ = branch
    base = f"/api/spaces/{space_id}/branches/{branch_id}/sources"
    await _declare_openapi(client, base)
    # Dépôt.
    r = await client.post(
        f"{base}/api-paiement/contracts",
        json={
            "filename": "paiement.yaml",
            "content": _CONTRACT_31,
            "version": "v2",
            "replace": False,
        },
    )
    assert r.status_code == 201, r.text
    # Liste : un contrat, pas encore collecté.
    contracts = (await client.get(f"{base}/api-paiement/contracts")).json()["contrats"]
    assert len(contracts) == 1
    assert contracts[0]["version"] == "v2"
    assert contracts[0]["fichier_origine"] == "paiement.yaml"
    assert contracts[0]["collecte"] is False
    # Collecte : les schémas apparaissent, l'état passe à collecté.
    collect = await client.post(f"{base}/api-paiement/collect")
    assert collect.status_code == 200, collect.text
    assert collect.json()["added"] == 2
    raw = tmp_path / "work" / space_id / "principale" / "sources" / "api-paiement" / "raw"
    assert (raw / "v2-commande.json").is_file()
    after = (await client.get(f"{base}/api-paiement/contracts")).json()["contrats"]
    assert after[0]["collecte"] is True
    # Téléchargement : octets déposés.
    dl = await client.get(f"{base}/api-paiement/contracts/v2/download")
    assert dl.status_code == 200
    assert dl.content.decode("utf-8") == _CONTRACT_31
    # Retrait : fichier + schémas partent.
    rm = await client.delete(f"{base}/api-paiement/contracts/v2")
    assert rm.status_code == 204
    assert not (raw / "v2-commande.json").exists()
    assert (await client.get(f"{base}/api-paiement/contracts")).json()["contrats"] == []


async def test_depot_version_existante_demande_confirmation(
    branch: tuple[AsyncClient, str, str, str],
) -> None:
    client, space_id, branch_id, _ = branch
    base = f"/api/spaces/{space_id}/branches/{branch_id}/sources"
    await _declare_openapi(client, base)
    body = {"filename": "p.yaml", "content": _CONTRACT_31, "version": "v2", "replace": False}
    assert (await client.post(f"{base}/api-paiement/contracts", json=body)).status_code == 201
    again = await client.post(f"{base}/api-paiement/contracts", json=body)
    assert again.status_code == 409
    assert again.json()["error"] == "contract_version_taken"
    # Confirmé : remplace.
    body["replace"] = True
    assert (await client.post(f"{base}/api-paiement/contracts", json=body)).status_code == 201


async def test_depot_refuse_swagger_2(branch: tuple[AsyncClient, str, str, str]) -> None:
    client, space_id, branch_id, _ = branch
    base = f"/api/spaces/{space_id}/branches/{branch_id}/sources"
    await _declare_openapi(client, base)
    r = await client.post(
        f"{base}/api-paiement/contracts",
        json={"filename": "s.yaml", "content": _SWAGGER_2, "version": "v1", "replace": False},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "contract_swagger_unsupported"


async def test_depot_refuse_ref_absente(branch: tuple[AsyncClient, str, str, str]) -> None:
    client, space_id, branch_id, _ = branch
    base = f"/api/spaces/{space_id}/branches/{branch_id}/sources"
    await _declare_openapi(client, base)
    r = await client.post(
        f"{base}/api-paiement/contracts",
        json={"filename": "r.yaml", "content": _REF_MISSING, "version": "v1", "replace": False},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "contract_ref_missing"


async def test_depot_refuse_version_invalide(branch: tuple[AsyncClient, str, str, str]) -> None:
    client, space_id, branch_id, _ = branch
    base = f"/api/spaces/{space_id}/branches/{branch_id}/sources"
    await _declare_openapi(client, base)
    r = await client.post(
        f"{base}/api-paiement/contracts",
        json={"filename": "p.yaml", "content": _CONTRACT_31, "version": "V 1!", "replace": False},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "contract_version_invalid"


async def test_depot_refuse_trop_volumineux(
    branch: tuple[AsyncClient, str, str, str], app: FastAPI
) -> None:
    client, space_id, branch_id, _ = branch
    app.state.settings.openapi_max_contract_bytes = 50
    base = f"/api/spaces/{space_id}/branches/{branch_id}/sources"
    await _declare_openapi(client, base)
    r = await client.post(
        f"{base}/api-paiement/contracts",
        json={"filename": "p.yaml", "content": _CONTRACT_31, "version": "v2", "replace": False},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "contract_too_large"


async def test_depot_reserve_a_la_capacite(
    branch: tuple[AsyncClient, str, str, str], app: FastAPI
) -> None:
    client, space_id, branch_id, _ = branch
    base = f"/api/spaces/{space_id}/branches/{branch_id}/sources"
    await _declare_openapi(client, base)
    await create_plain_user(app, "lecteur@example.org", "mot-de-passe-solide")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        await login(other, "lecteur@example.org", "mot-de-passe-solide")
        r = await other.post(
            f"{base}/api-paiement/contracts",
            json={"filename": "p.yaml", "content": _CONTRACT_31, "version": "v2", "replace": False},
        )
    assert r.status_code == 403
