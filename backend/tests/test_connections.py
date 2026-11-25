from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.conftest import (
    bootstrap_admin,
    create_plain_user,
    db_fetchrow,
    db_fetchval,
    login,
    wire_fake_idp,
)
from tests.fake_forge import GITHUB_LOGIN, GOOD_TOKEN, fake_github_app

URL = "https://github.com"


@pytest.fixture
async def admin_client(client: AsyncClient, app: FastAPI) -> AsyncClient:
    # Le faux GitHub est branché via la fabrique de client HTTP sortant.
    wire_fake_idp(app, fake_github_app())
    await bootstrap_admin(client)
    return client


async def _secret(
    client: AsyncClient, slug: str = "gh", category: str = "github", value: str = GOOD_TOKEN
) -> str:
    response = await client.post(
        "/api/secrets",
        json={"slug": slug, "label": f"Jeton {slug}", "category": category, "value": value},
    )
    assert response.status_code == 201, response.text
    return slug


async def _create(
    client: AsyncClient, label: str = "GitHub perso", secret_slug: str | None = None
) -> str:
    if secret_slug is None:
        secret_slug = await _secret(client, slug=f"gh-{uuid.uuid4().hex[:8]}")
    response = await client.post(
        "/api/connections",
        json={"label": label, "forge": "github", "url": URL, "secret_slug": secret_slug},
    )
    assert response.status_code == 201, response.text
    return secret_slug


async def test_ajout_verifie_puis_enregistre_la_connexion(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    slug = await _create(admin_client)
    row = await db_fetchrow(app, "SELECT forge, identity, secret_slug FROM connections")
    assert row is not None
    assert row["forge"] == "github"
    assert row["identity"] == GITHUB_LOGIN
    assert row["secret_slug"] == slug


async def test_reponse_ne_contient_ni_jeton_ni_slug_de_secret(admin_client: AsyncClient) -> None:
    slug = await _secret(admin_client, "gh-reponse")
    response = await admin_client.post(
        "/api/connections",
        json={"label": "GitHub", "forge": "github", "url": URL, "secret_slug": slug},
    )
    body = response.json()
    assert GOOD_TOKEN not in str(body)
    assert "secret_slug" not in body  # la réponse de création n'expose pas le slug
    assert body["identity"] == GITHUB_LOGIN


async def test_jeton_invalide_refuse_mais_garde_le_secret(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    # Un secret créé pour l'occasion reste, même si la connexion est refusée.
    slug = await _secret(admin_client, "gh-mauvais", value="mauvais")
    response = await admin_client.post(
        "/api/connections",
        json={"label": "GitHub", "forge": "github", "url": URL, "secret_slug": slug},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "connection_token_invalid"
    assert await db_fetchval(app, "SELECT count(*) FROM connections") == 0
    assert await db_fetchval(app, "SELECT count(*) FROM secrets WHERE slug = 'gh-mauvais'") == 1


async def test_secret_mauvaise_categorie_refuse(admin_client: AsyncClient, app: FastAPI) -> None:
    # Un secret gitlab ne peut pas servir une connexion github.
    await _secret(admin_client, "gl-x", category="gitlab")
    response = await admin_client.post(
        "/api/connections",
        json={"label": "GitHub", "forge": "github", "url": URL, "secret_slug": "gl-x"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "connection_secret_invalid"
    assert await db_fetchval(app, "SELECT count(*) FROM connections") == 0


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("label", "", "label_required"),
        ("forge", "bitbucket", "forge_invalid"),
        ("url", "pas-une-url", "url_invalid"),
        ("secret_slug", "", "token_required"),
    ],
)
async def test_champ_invalide_refuse(
    admin_client: AsyncClient, field: str, value: str, code: str
) -> None:
    data = {"label": "X", "forge": "github", "url": URL, "secret_slug": "gh-x"}
    data[field] = value
    response = await admin_client.post("/api/connections", json=data)
    assert response.status_code == 400
    assert code in response.json()["error_args"]["fields"]


async def test_doublon_meme_forge_url_identite_refuse(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    slug = await _create(admin_client)
    response = await admin_client.post(
        "/api/connections",
        json={"label": "Doublon", "forge": "github", "url": URL, "secret_slug": slug},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "connection_duplicate"
    assert await db_fetchval(app, "SELECT count(*) FROM connections") == 1


async def test_liste_affiche_identite_etat_et_secret(admin_client: AsyncClient) -> None:
    slug = await _create(admin_client)
    body = (await admin_client.get("/api/connections")).json()
    assert len(body["connections"]) == 1
    c = body["connections"][0]
    assert c["identity"] == GITHUB_LOGIN
    assert c["last_status"] == "valid"
    assert c["secret_slug"] == slug
    assert c["secret_used_by_others"] == 0
    assert GOOD_TOKEN not in str(body)


async def test_tester_remet_a_jour_l_etat(admin_client: AsyncClient, app: FastAPI) -> None:
    await _create(admin_client)
    connection_id = await db_fetchval(app, "SELECT id FROM connections")
    response = await admin_client.post(f"/api/connections/{connection_id}/test")
    assert response.status_code == 200
    assert response.json()["last_status"] == "valid"


async def test_suppression_sans_secret_le_conserve(admin_client: AsyncClient, app: FastAPI) -> None:
    await _create(admin_client, secret_slug=await _secret(admin_client, "gh-garde"))
    connection_id = await db_fetchval(app, "SELECT id FROM connections")
    response = await admin_client.delete(f"/api/connections/{connection_id}")
    assert response.status_code == 204
    assert await db_fetchval(app, "SELECT count(*) FROM connections") == 0
    assert await db_fetchval(app, "SELECT count(*) FROM secrets WHERE slug = 'gh-garde'") == 1


async def test_suppression_avec_secret_le_retire(admin_client: AsyncClient, app: FastAPI) -> None:
    await _create(admin_client, secret_slug=await _secret(admin_client, "gh-jette"))
    connection_id = await db_fetchval(app, "SELECT id FROM connections")
    response = await admin_client.delete(f"/api/connections/{connection_id}?delete_secret=true")
    assert response.status_code == 204
    assert await db_fetchval(app, "SELECT count(*) FROM secrets WHERE slug = 'gh-jette'") == 0


async def test_suppression_secret_partage_conserve(admin_client: AsyncClient, app: FastAPI) -> None:
    slug = await _secret(admin_client, "gh-partage")
    await _create(admin_client, label="A", secret_slug=slug)
    # Une seconde connexion partage le secret (même forge, URL, identité différente).
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO connections (label, slug, forge, url, secret_slug, identity, last_status) "
            "VALUES ('B', 'b-conn', 'github', $1, $2, 'autre', 'valid')",
            URL,
            slug,
        )
    connection_id = await db_fetchval(app, "SELECT id FROM connections WHERE label = 'A'")
    response = await admin_client.delete(f"/api/connections/{connection_id}?delete_secret=true")
    assert response.status_code == 204
    # Encore utilisé par la connexion B → conservé.
    assert await db_fetchval(app, "SELECT count(*) FROM secrets WHERE slug = 'gh-partage'") == 1


async def test_suppression_inexistante_404(admin_client: AsyncClient) -> None:
    response = await admin_client.delete(f"/api/connections/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"] == "connection_not_found"


async def test_jeton_absent_des_logs(
    admin_client: AsyncClient, capfd: pytest.CaptureFixture[str]
) -> None:
    await _create(admin_client)
    logs = capfd.readouterr()
    assert GOOD_TOKEN not in (logs.out + logs.err)


@pytest.fixture
async def user_client(admin_client: AsyncClient, app: FastAPI) -> AsyncIterator[AsyncClient]:
    await create_plain_user(app, "simple@example.org", "mot-de-passe-simple")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as second:
        await login(second, "simple@example.org", "mot-de-passe-simple")
        yield second


async def test_non_admin_refus_403(user_client: AsyncClient) -> None:
    assert (await user_client.get("/api/connections")).status_code == 403
    creation = await user_client.post(
        "/api/connections",
        json={"label": "X", "forge": "github", "url": URL, "secret_slug": "gh"},
    )
    assert creation.status_code == 403


# --- Alignement sur les maquettes : menu, dépôts, « utilisée par » -----------


async def test_menu_des_forges_liste_les_strategies(admin_client: AsyncClient) -> None:
    body = (await admin_client.get("/api/connections/forges")).json()
    specs = {f["forge"]: f for f in body["forges"]}
    assert set(specs) == {"github", "gitlab", "azure_devops"}
    assert specs["github"]["default_url"] == "https://github.com"
    assert specs["github"]["secret_category"] == "github"
    # Azure DevOps : URL d'organisation obligatoire, sans défaut.
    assert specs["azure_devops"]["default_url"] == ""
    assert specs["azure_devops"]["url_required"] is True


async def test_strategie_de_test_apparait_dans_le_menu(
    admin_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ossie_studio.forges import registry
    from ossie_studio.forges.base import ForgeFormSpec

    class _FakeStrategy:
        def form_spec(self) -> ForgeFormSpec:
            return ForgeFormSpec(forge="forge_test", default_url="https://t", url_required=False)

    monkeypatch.setitem(registry._STRATEGIES, "forge_test", _FakeStrategy())  # type: ignore[arg-type]
    body = (await admin_client.get("/api/connections/forges")).json()
    assert "forge_test" in {f["forge"] for f in body["forges"]}


async def test_depots_lisibles_pagines(admin_client: AsyncClient, app: FastAPI) -> None:
    await _create(admin_client)
    connection_id = await db_fetchval(app, "SELECT id FROM connections")
    body = (await admin_client.get(f"/api/connections/{connection_id}/repositories")).json()
    # Le faux GitHub pagine : 100 + 1 → la pagination est gérée de bout en bout.
    assert body["total"] == 101
    assert any(r["full_name"].endswith("/last") for r in body["repositories"])
    # Adresse de clone fournie : c'est elle qu'un espace rattache (choisie dans la liste).
    assert body["repositories"][0]["clone_url"].startswith("https://github.com/")


async def test_payload_utilisee_par(admin_client: AsyncClient) -> None:
    await _create(admin_client)
    c = (await admin_client.get("/api/connections")).json()["connections"][0]
    assert c["used_by_spaces"] == 0
    assert c["used_by_sources"] == 0


# --- Slug de la connexion : saisi (déduit du libellé côté interface) -------------


async def test_slug_fourni_est_enregistre_tel_quel(admin_client: AsyncClient, app: FastAPI) -> None:
    secret_slug = await _secret(admin_client, "gh-slug")
    response = await admin_client.post(
        "/api/connections",
        json={
            "label": "GitHub perso",
            "slug": "forge-maison",
            "forge": "github",
            "url": URL,
            "secret_slug": secret_slug,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["slug"] == "forge-maison"
    assert await db_fetchval(app, "SELECT slug FROM connections") == "forge-maison"


async def test_slug_absent_deduit_du_libelle(admin_client: AsyncClient, app: FastAPI) -> None:
    await _create(admin_client, label="Forge Équipe Data")
    assert await db_fetchval(app, "SELECT slug FROM connections") == "forge-equipe-data"


@pytest.mark.parametrize("slug", ["Majuscules", "avec espace", "-bord", "double--tiret", "a_b"])
async def test_slug_invalide_refuse_avant_toute_verification(
    admin_client: AsyncClient, app: FastAPI, slug: str
) -> None:
    secret_slug = await _secret(admin_client, "gh-invalide")
    response = await admin_client.post(
        "/api/connections",
        json={
            "label": "X",
            "slug": slug,
            "forge": "github",
            "url": URL,
            "secret_slug": secret_slug,
        },
    )
    assert response.status_code == 400
    assert "connection_slug_invalid" in response.json()["error_args"]["fields"]
    assert await db_fetchval(app, "SELECT count(*) FROM connections") == 0


async def test_slug_deja_pris_refuse(admin_client: AsyncClient, app: FastAPI) -> None:
    await _create(admin_client, label="GitHub perso")  # slug déduit : github-perso
    secret_slug = await _secret(admin_client, "gh-autre")
    response = await admin_client.post(
        "/api/connections",
        json={
            "label": "Autre",
            "slug": "github-perso",
            "forge": "github",
            "url": "https://github.example.org",
            "secret_slug": secret_slug,
        },
    )
    assert response.status_code == 409
    assert response.json()["error"] == "connection_slug_taken"
    assert await db_fetchval(app, "SELECT count(*) FROM connections") == 1


async def test_modification_reverifie_et_garde_slug_et_forge(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    await _create(admin_client, label="GitHub perso")
    connection_id = await db_fetchval(app, "SELECT id FROM connections")
    nouveau = await _secret(admin_client, "gh-nouveau")
    await db_execute_sql(app, "UPDATE connections SET last_status = 'error', last_error_code = 'x'")
    response = await admin_client.put(
        f"/api/connections/{connection_id}",
        json={"label": "GitHub pro", "url": URL + "/", "secret_slug": nouveau},
    )
    assert response.status_code == 200, response.text
    row = await db_fetchrow(
        app, "SELECT label, slug, forge, url, secret_slug, last_status FROM connections"
    )
    assert row is not None
    assert (row["label"], row["slug"], row["forge"]) == ("GitHub pro", "github-perso", "github")
    assert (row["url"], row["secret_slug"], row["last_status"]) == (URL, nouveau, "valid")


async def test_modification_refusee_par_la_forge_ne_change_rien(
    admin_client: AsyncClient, app: FastAPI
) -> None:
    ancien = await _create(admin_client, label="GitHub perso")
    connection_id = await db_fetchval(app, "SELECT id FROM connections")
    mauvais = await _secret(admin_client, "gh-mauvais-edit", value="mauvais")
    response = await admin_client.put(
        f"/api/connections/{connection_id}",
        json={"label": "Autre", "url": URL, "secret_slug": mauvais},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "connection_token_invalid"
    row = await db_fetchrow(app, "SELECT label, secret_slug FROM connections")
    assert row is not None
    assert (row["label"], row["secret_slug"]) == ("GitHub perso", ancien)


async def test_modification_connexion_inconnue_404(admin_client: AsyncClient) -> None:
    response = await admin_client.put(
        f"/api/connections/{uuid.uuid4()}",
        json={"label": "X", "url": URL, "secret_slug": "gh"},
    )
    assert response.status_code == 404


async def db_execute_sql(app: FastAPI, sql: str) -> None:
    async with app.state.pool.acquire() as conn:
        await conn.execute(sql)


async def test_audit_creation_modification_suppression(admin_client: AsyncClient) -> None:
    # Ticket cf4434da : chaque écriture d'une connexion laisse une ligne d'audit.
    await _create(admin_client)
    connection_id = (await admin_client.get("/api/connections")).json()["connections"][0]["id"]
    secret = (await admin_client.get("/api/connections")).json()["connections"][0]["secret_slug"]
    r = await admin_client.put(
        f"/api/connections/{connection_id}",
        json={"label": "Renommée", "url": URL, "secret_slug": secret},
    )
    assert r.status_code == 200, r.text
    r = await admin_client.delete(f"/api/connections/{connection_id}?delete_secret=true")
    assert r.status_code == 204
    events = (await admin_client.get("/api/audit")).json()["events"]
    assert [(e["action"], e["object_type"]) for e in events] == [
        ("deleted", "secret"),
        ("deleted", "connection"),
        ("updated", "connection"),
        ("created", "connection"),
        ("created", "secret"),
    ]
    assert events[1]["object_label"] == "Renommée"
    assert GOOD_TOKEN not in (await admin_client.get("/api/audit")).text
