from __future__ import annotations

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.conftest import bootstrap_admin, create_plain_user, login


async def test_liste_plugins_et_reglage_parametre(client: AsyncClient) -> None:
    await bootstrap_admin(client)
    plugins = (await client.get("/api/parsing/plugins")).json()["plugins"]
    markdown = next(p for p in plugins if p["id"] == "markdown")
    assert markdown["cible"] == "raw"
    assert "**/*.md" in markdown["globs"]
    param = next(p for p in markdown["parametres"] if p["slug"] == "taille_max_ko")
    assert param["value"] == param["default"]

    put = await client.put(
        "/api/parsing/plugins/markdown/parameters/taille_max_ko", json={"value": "256"}
    )
    assert put.status_code == 200, put.text
    again = (await client.get("/api/parsing/plugins")).json()["plugins"]
    markdown2 = next(p for p in again if p["id"] == "markdown")
    value = next(p for p in markdown2["parametres"] if p["slug"] == "taille_max_ko")["value"]
    assert value == "256"


async def test_parametre_entier_valide(client: AsyncClient) -> None:
    await bootstrap_admin(client)
    bad = await client.put(
        "/api/parsing/plugins/markdown/parameters/taille_max_ko", json={"value": "beaucoup"}
    )
    assert bad.status_code == 400
    assert "value_int" in bad.json()["error_args"]["fields"]


async def test_plugin_ou_parametre_inconnu(client: AsyncClient) -> None:
    await bootstrap_admin(client)
    assert (
        await client.put("/api/parsing/plugins/zzz/parameters/x", json={"value": "1"})
    ).status_code == 404
    assert (
        await client.put(
            "/api/parsing/plugins/markdown/parameters/zzz", json={"value": "1"}
        )
    ).status_code == 404


async def test_reglage_reserve_a_la_capacite(client: AsyncClient, app: FastAPI) -> None:
    await bootstrap_admin(client)
    await create_plain_user(app, "lambda@example.org", "mot-de-passe-solide")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        await login(other, "lambda@example.org", "mot-de-passe-solide")
        r = await other.put(
            "/api/parsing/plugins/markdown/parameters/taille_max_ko", json={"value": "100"}
        )
    assert r.status_code == 403
