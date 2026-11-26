"""Primitives d'écriture de curation (epic « Primitives MCP d'écriture », ticket
« Curation par l'agent ») : l'agent écrit dans le brouillon de la personne, par
le même service que l'écran ; jamais pour une clé d'API ; borné par les droits
de la personne sur l'espace."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.agent_profiles.service import load_agent_primitives
from ossie_studio.mcp_surface.caller import McpCaller
from tests.mcp_helpers import call_tool, mcp_client, tool_names
from tests.space_fixture import ApiSpace, forge_edit, make_api_space

AGENT = "agent-ecriture"
KEY = "cle-ecriture"
WRITES = {"retain_asset", "remove_asset", "enrich_asset", "refresh_assets"}

CUSTOMER = {
    "title": "Customer",
    "type": "object",
    "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
}
ORDER = {
    "title": "Order",
    "type": "object",
    "properties": {"id": {"type": "integer"}, "total": {"type": "number"}},
}


def _manifest() -> str:
    from ossie_studio.sources.manifest import SourceManifest, dump_manifest

    return dump_manifest(
        SourceManifest(
            slug="ventes",
            label="Ventes",
            type="git",
            connection="gh",
            parameters={"repository": "o/r", "branch": "main", "parsers": ["csharp"]},
        )
    )


async def _profile(app: FastAPI, name: str, primitives: list[str], all_read: bool) -> uuid.UUID:
    async with app.state.pool.acquire() as conn:
        value = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives, all_read) VALUES ($1, $2, $3) "
            "RETURNING id",
            name,
            primitives,
            all_read,
        )
    assert isinstance(value, uuid.UUID)
    return value


def _wire(app: FastAPI, callers: dict[str, McpCaller]) -> None:
    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return callers.get(token)

    app.state.mcp_resolvers.append(resolver)


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    """Source « ventes » publiée avec deux objets bruts, aucun asset. Un agent
    connecté pour l'administrateur, et une clé, avec un profil « Curation »
    (toutes les lectures + les quatre écritures)."""
    api_space = await make_api_space(client, app, tmp_path)
    forge_edit(
        api_space,
        {
            "sources/ventes/manifest.yaml": _manifest(),
            "sources/ventes/raw/customer.schema.yaml": json.dumps(CUSTOMER),
            "sources/ventes/raw/order.schema.yaml": json.dumps(ORDER),
        },
    )
    profile = await _profile(app, "Curation", sorted(WRITES), all_read=True)
    async with app.state.pool.acquire() as conn:
        admin_id = await conn.fetchval("SELECT id FROM users WHERE is_bootstrap")
    _wire(
        app,
        {
            AGENT: McpCaller(
                kind="oauth",
                ref="c",
                agent_profile_id=profile,
                user_id=admin_id,
                client_id="claude",
            ),
            KEY: McpCaller(kind="key", ref="k", agent_profile_id=profile),
        },
    )
    return api_space


def _ids(space: ApiSpace, **extra: Any) -> dict[str, Any]:
    return {"space_id": space.space_id, "branch_id": space.branch_id, "source": "ventes", **extra}


async def _ok(app: FastAPI, token: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await call_tool(app, token, name, args)
    assert not result.is_error, result.content[0].text
    data: dict[str, Any] = result.structured_content
    return data


async def _refusal(app: FastAPI, token: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await call_tool(app, token, name, args)
    assert result.is_error
    data: dict[str, Any] = json.loads(result.content[0].text)
    return data


# --- Critère 1 : le parcours complet, dans le brouillon -------------------------------


async def test_l_agent_cure_dans_le_brouillon_sans_rien_publier(
    app: FastAPI, space: ApiSpace
) -> None:
    order = _ids(space, path="order.schema.yaml")

    retained = await _ok(app, AGENT, "retain_asset", order)
    assert retained["changed"] is True
    assert retained["changed_paths"] == ["sources/ventes/assets/order.schema.yaml"]
    assert retained["asset"]["unpublished"] is True
    assert {f["name"] for f in retained["asset"]["fields"] if f["retained"]} == {"id", "total"}

    partial = await _ok(
        app, AGENT, "retain_asset", _ids(space, path="customer.schema.yaml", fields=["id"])
    )
    assert [f["name"] for f in partial["asset"]["fields"] if f["retained"]] == ["id"]

    enriched = await _ok(
        app,
        AGENT,
        "enrich_asset",
        {**order, "object": {"business_name": "Commande"}, "fields": {"total": {"tags": ["€"]}}},
    )
    assert enriched["asset"]["object"]["business_name"] == "Commande"
    total = next(f for f in enriched["asset"]["fields"] if f["name"] == "total")
    assert total["tags"] == ["€"]

    # Le brut change côté publié : le rafraîchissement reprend la structure, garde l'enrichissement.
    forge_edit(
        space,
        {
            "sources/ventes/raw/order.schema.yaml": json.dumps(
                {**ORDER, "properties": {**ORDER["properties"], "total": {"type": "string"}}}
            )
        },
    )
    refreshed = await _ok(app, AGENT, "refresh_assets", _ids(space))
    assert refreshed["refreshed"] == ["order.schema.yaml"]

    # Rien n'est publié : le publié n'a aucun asset ; le brouillon en a deux.
    published = await _ok(app, AGENT, "list_assets", _ids(space))
    assert published["assets"] == []
    draft = await _ok(
        app, AGENT, "get_my_draft", {"space_id": space.space_id, "branch_id": space.branch_id}
    )
    assert {o["path"] for o in draft["objects"]} == {
        "sources/ventes/assets/order.schema.yaml",
        # Travail humain de l'asset, à côté du schéma (ticket 7a284e55).
        "sources/ventes/assets/order.extend.yaml",
        "sources/ventes/assets/customer.schema.yaml",
    }

    removed = await _ok(app, AGENT, "remove_asset", order)
    assert removed["changed"] is True and removed["asset"] is None
    names = [
        a["path"]
        for a in (await _ok(app, AGENT, "list_assets", _ids(space, branch="draft")))["assets"]
    ]
    assert names == ["customer.schema.yaml"]


# --- Critère 2 : jamais pour une clé d'API --------------------------------------------


async def test_une_cle_ne_voit_ni_n_appelle_aucune_ecriture(app: FastAPI, space: ApiSpace) -> None:
    assert not WRITES & set(await tool_names(app, KEY))
    refused = await _refusal(app, KEY, "retain_asset", _ids(space, path="order.schema.yaml"))
    assert refused["error"] == "primitive_forbidden"


# --- Critère 3 : borné par les droits de la personne sur l'espace ---------------------


async def test_ecriture_bornee_par_les_droits_sur_l_espace(app: FastAPI, space: ApiSpace) -> None:
    async with app.state.pool.acquire() as conn:
        user_id = await conn.fetchval(
            "INSERT INTO users (email, role) VALUES ('ana@example.org', 'user') RETURNING id"
        )
        base = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) "
            "VALUES ('base-ana', '{spaces:read,curation:read}') RETURNING id"
        )
        await conn.execute("UPDATE users SET profile_id = $2 WHERE id = $1", user_id, base)
        other = await conn.fetchval(
            "INSERT INTO git_spaces (name, slug, connection_id, repo_url, last_probe_status) "
            "SELECT 'autre', 'autre', connection_id, 'file:///x', 'valid' FROM git_spaces "
            "WHERE id = $1 RETURNING id",
            uuid.UUID(space.space_id),
        )
        team = await conn.fetchval(
            "INSERT INTO teams (name, slug, origin) VALUES ('t', 't', 'manual') RETURNING id"
        )
        await conn.execute(
            "INSERT INTO user_teams (user_id, team_id, origin) VALUES ($1, $2, 'manual')",
            user_id,
            team,
        )
        writer = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) VALUES ('curateur', '{curation:write}') "
            "RETURNING id"
        )
        await conn.execute(
            "INSERT INTO space_team_profiles (space_id, team_id, profile_id) VALUES ($1, $2, $3)",
            other,
            team,
            writer,
        )
        profile = await conn.fetchval("SELECT id FROM agent_profiles WHERE name = 'Curation'")
    _wire(app, {"ana": McpCaller(kind="oauth", ref="a", agent_profile_id=profile, user_id=user_id)})

    # curation:write sur un AUTRE espace : outil visible, refusé sur celui-ci.
    assert "retain_asset" in await tool_names(app, "ana")
    refused = await _refusal(app, "ana", "retain_asset", _ids(space, path="order.schema.yaml"))
    assert refused["error"] == "primitive_forbidden"

    # Donné sur cet espace : permis ; retiré : refusé dès l'appel suivant.
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "UPDATE profiles SET capabilities = '{spaces:read,curation:read,curation:write}' "
            "WHERE id = $1",
            base,
        )
    assert (await _ok(app, "ana", "retain_asset", _ids(space, path="order.schema.yaml")))["changed"]
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "UPDATE profiles SET capabilities = '{spaces:read,curation:read}' WHERE id = $1", base
        )
    refused = await _refusal(app, "ana", "remove_asset", _ids(space, path="order.schema.yaml"))
    assert refused["error"] == "primitive_forbidden"


# --- Critère 4 : fermé par défaut -----------------------------------------------------


async def test_aucun_profil_existant_ne_recoit_l_ecriture(app: FastAPI, space: ApiSpace) -> None:
    async with app.state.pool.acquire() as conn:
        lecture = await conn.fetchval("SELECT id FROM agent_profiles WHERE slug = 'lecture'")
        all_reads = await _profile(app, "Toutes lectures", [], all_read=True)
        assert not WRITES & await load_agent_primitives(conn, lecture)
        assert not WRITES & await load_agent_primitives(conn, all_reads)
        # Aucun profil livré en plus de « Lecture ».
        assert await conn.fetchval("SELECT count(*) FROM agent_profiles WHERE is_builtin") == 1


# --- Critère 5 : rejeu ----------------------------------------------------------------


async def test_rejeu_sans_effet_ni_erreur(app: FastAPI, space: ApiSpace) -> None:
    order = _ids(space, path="order.schema.yaml")
    assert (await _ok(app, AGENT, "retain_asset", order))["changed"] is True
    again = await _ok(app, AGENT, "retain_asset", order)
    assert again["changed"] is False and again["changed_paths"] == []
    assert (await _ok(app, AGENT, "remove_asset", order))["changed"] is True
    twice = await _ok(app, AGENT, "remove_asset", order)
    assert twice["changed"] is False and twice["asset"] is None
    nothing = await _ok(app, AGENT, "refresh_assets", _ids(space))
    assert nothing["changed"] is False and nothing["refreshed"] == []


# --- Critère 6 : cas limites, refus nommés sans écriture ------------------------------


async def test_cas_limites_refus_nommes(app: FastAPI, space: ApiSpace) -> None:
    absent = await _refusal(app, AGENT, "retain_asset", _ids(space, path="absent.schema.yaml"))
    assert absent["error"] == "asset_origin_missing"
    unknown = await _refusal(
        app, AGENT, "retain_asset", _ids(space, path="order.schema.yaml", fields=["id", "fantome"])
    )
    assert unknown == {"error": "field_not_found", "fields": ["fantome"]}
    not_retained = await _refusal(
        app,
        AGENT,
        "enrich_asset",
        {**_ids(space, path="order.schema.yaml"), "object": {"concept": "x"}},
    )
    assert not_retained["error"] == "asset_not_retained"
    await _ok(app, AGENT, "retain_asset", _ids(space, path="order.schema.yaml"))
    bad_key = await _refusal(
        app,
        AGENT,
        "enrich_asset",
        {**_ids(space, path="order.schema.yaml"), "object": {"couleur": "x"}},
    )
    assert bad_key["error"] == "invalid_arguments"
    bad_field = await _refusal(
        app,
        AGENT,
        "enrich_asset",
        {**_ids(space, path="order.schema.yaml"), "fields": {"fantome": {"concept": "x"}}},
    )
    assert bad_field == {"error": "field_not_found", "fields": ["fantome"]}
    no_source = await _refusal(
        app, AGENT, "retain_asset", {**_ids(space, path="order.schema.yaml"), "source": "inconnue"}
    )
    assert no_source["error"] == "source_not_found"
    bad_path = await _refusal(app, AGENT, "remove_asset", _ids(space, path="../manifest.yaml"))
    assert bad_path["error"] == "curation_path_invalid"
    # Aucune de ces tentatives n'a écrit : seul order.schema.yaml est dans le brouillon.
    draft = await _ok(
        app, AGENT, "get_my_draft", {"space_id": space.space_id, "branch_id": space.branch_id}
    )
    assert [o["path"] for o in draft["objects"]] == ["sources/ventes/assets/order.schema.yaml"]


# --- Critère 7 : l'écran et l'agent écrivent en même temps ----------------------------


async def test_ecran_et_agent_en_meme_temps_rien_n_est_perdu(
    app: FastAPI, client: AsyncClient, space: ApiSpace
) -> None:
    screen = client.post(
        f"{space.base}/sources/ventes/curation/retain", json={"path": "customer.schema.yaml"}
    )
    agent = call_tool(app, AGENT, "retain_asset", _ids(space, path="order.schema.yaml"))
    screen_response, agent_result = await asyncio.gather(screen, agent)
    assert screen_response.status_code == 200, screen_response.text
    assert not agent_result.is_error
    draft = await _ok(
        app, AGENT, "get_my_draft", {"space_id": space.space_id, "branch_id": space.branch_id}
    )
    assert {o["path"] for o in draft["objects"]} == {
        "sources/ventes/assets/customer.schema.yaml",
        "sources/ventes/assets/order.schema.yaml",
    }


# --- Critère 8 : destructif annoncé au client -----------------------------------------


async def test_annotations_envoyees_au_client(app: FastAPI, space: ApiSpace) -> None:
    async with mcp_client(app, AGENT) as client:
        tools = {t.name: t.annotations for t in (await client.list_tools()).tools}
    assert tools["remove_asset"].destructive_hint is True
    for name in ("retain_asset", "enrich_asset", "refresh_assets"):
        assert tools[name].read_only_hint is False and tools[name].destructive_hint is False
    assert tools["retain_asset"].idempotent_hint is True
    assert tools["list_assets"].read_only_hint is True


# --- Critère 9 : journal --------------------------------------------------------------


async def test_ecriture_journalisee_sans_le_contenu(
    app: FastAPI, space: ApiSpace, capsys: pytest.CaptureFixture[str]
) -> None:
    await _ok(app, AGENT, "retain_asset", _ids(space, path="order.schema.yaml"))
    capsys.readouterr()
    await _ok(
        app,
        AGENT,
        "enrich_asset",
        {
            **_ids(space, path="order.schema.yaml"),
            "object": {"description": "Valeur-confidentielle"},
        },
    )
    out = capsys.readouterr().out
    entries = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    call = next(e for e in entries if e.get("event") == "mcp_call")
    assert call["primitive"] == "enrich_asset" and call["outcome"] == "ok"
    # Seul le `.extend.yaml` change : le schéma de l'asset reste standard.
    assert call["changed_paths"] == ["sources/ventes/assets/order.extend.yaml"]
    assert call["client_id"] == "claude" and call["user_id"]
    write = next(e for e in entries if e.get("event") == "draft_write")
    assert "via l'agent claude" in write["intent"]
    assert "Valeur-confidentielle" not in out


# --- L'écran passe par le même service, sans changement de comportement ---------------


async def test_routes_de_l_ecran_inchangees(
    app: FastAPI, client: AsyncClient, space: ApiSpace
) -> None:
    base = f"{space.base}/sources/ventes/curation"
    retained = await client.post(f"{base}/retain", json={"path": "order.schema.yaml"})
    assert retained.status_code == 200 and retained.json()["commit"].startswith("draft:")
    # L'écran ignore un champ inconnu (comportement historique, inchangé).
    partial = await client.post(
        f"{base}/retain", json={"path": "customer.schema.yaml", "fields": ["id", "x"]}
    )
    assert partial.status_code == 200
    enriched = await client.put(
        f"{base}/enrich", json={"path": "order.schema.yaml", "obj": {"business_name": "Commande"}}
    )
    assert enriched.status_code == 200 and enriched.json()["commit"]
    refreshed = await client.post(f"{base}/refresh", json={})
    assert refreshed.json() == {"ok": True, "commit": None, "refreshed": []}
    removed = await client.post(f"{base}/remove", json={"path": "order.schema.yaml"})
    assert removed.json()["commit"]
    again = await client.post(f"{base}/remove", json={"path": "order.schema.yaml"})
    assert again.json() == {"ok": True, "commit": None}
    missing = await client.post(f"{base}/retain", json={"path": "absent.schema.yaml"})
    assert missing.status_code == 404 and missing.json()["error"] == "asset_origin_missing"
