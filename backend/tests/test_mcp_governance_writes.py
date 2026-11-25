"""Primitives d'écriture de la gouvernance (epic « Primitives MCP d'écriture »,
ticket « Gouvernance par l'agent ») : tags, schémas de classification et leurs
niveaux, certifications. Même service que l'écran, dans le brouillon de la
personne ; modifications partielles ; jamais pour une clé d'API ; borné par les
droits de la personne sur l'espace."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.mcp_surface.caller import McpCaller
from ossie_studio.mcp_surface.catalog import BY_ID
from tests.mcp_helpers import call_tool, mcp_client, tool_names
from tests.space_fixture import ApiSpace, make_api_space

AGENT = "agent-gouvernance"
KEY = "cle-gouvernance"
WRITES = {
    "create_tag",
    "update_tag",
    "delete_tag",
    "create_classification",
    "update_classification",
    "delete_classification",
    "create_level",
    "update_level",
    "delete_level",
    "reorder_levels",
    "create_certification",
    "update_certification",
    "delete_certification",
}


async def _profile(app: FastAPI, name: str, primitives: list[str]) -> uuid.UUID:
    async with app.state.pool.acquire() as conn:
        value = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives, all_read) VALUES ($1, $2, true) "
            "RETURNING id",
            name,
            primitives,
        )
    assert isinstance(value, uuid.UUID)
    return value


def _wire(app: FastAPI, callers: dict[str, McpCaller]) -> None:
    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return callers.get(token)

    app.state.mcp_resolvers.append(resolver)


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    """Un espace sans gouvernance publiée. Un agent connecté pour
    l'administrateur, et une clé, avec un profil « Gouvernance » (toutes les
    lectures + les écritures de gouvernance)."""
    api_space = await make_api_space(client, app, tmp_path)
    profile = await _profile(app, "Gouvernance", sorted(WRITES))
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
    return {"space_id": space.space_id, "branch_id": space.branch_id, **extra}


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


async def _draft_paths(app: FastAPI, space: ApiSpace) -> list[str]:
    draft = await _ok(app, AGENT, "get_my_draft", _ids(space))
    return sorted(o["path"] for o in draft["objects"])


# --- Critère 1 : chaque effet est dans le brouillon, pas côté publié -------------------


async def test_tags_dans_le_brouillon_modifications_partielles(
    app: FastAPI, space: ApiSpace
) -> None:
    created = await _ok(
        app,
        AGENT,
        "create_tag",
        _ids(
            space,
            tag_id="Gouvernance/PII",
            owner="dpo@example.org",
            description="Données personnelles",
            links={"wiki": "https://wiki.example.org/pii"},
        ),
    )
    assert created["changed"] is True
    assert created["changed_paths"] == ["governance/tags/gouvernance/pii.yaml"]
    assert created["tag"]["id"] == "gouvernance/pii"

    # Seule la description change : le responsable et les liens restent.
    updated = await _ok(
        app, AGENT, "update_tag", _ids(space, tag_id="gouvernance/pii", description="PII")
    )
    assert updated["tag"] == {
        "id": "gouvernance/pii",
        "owner": "dpo@example.org",
        "description": "PII",
        "links": {"wiki": "https://wiki.example.org/pii"},
        "properties": {},
    }
    # Les liens donnés remplacent la table entière.
    relinked = await _ok(app, AGENT, "update_tag", _ids(space, tag_id="gouvernance/pii", links={}))
    assert relinked["tag"]["links"] == {}
    assert relinked["tag"]["description"] == "PII"

    draft = await _ok(app, AGENT, "list_tags", _ids(space, view="draft"))
    assert [t["id"] for t in draft["tags"]] == ["gouvernance/pii"]
    assert draft["unpublished_changes"]
    published = await _ok(app, AGENT, "list_tags", _ids(space, view="published"))
    assert published["tags"] == []

    deleted = await _ok(app, AGENT, "delete_tag", _ids(space, tag_id="gouvernance/pii"))
    assert deleted["deleted"] == "gouvernance/pii"
    assert (await _ok(app, AGENT, "list_tags", _ids(space, view="draft")))["tags"] == []


async def test_classifications_et_niveaux_dans_le_brouillon(app: FastAPI, space: ApiSpace) -> None:
    created = await _ok(
        app,
        AGENT,
        "create_classification",
        _ids(
            space,
            name="Sensibilité",
            multi_value=True,
            levels=[
                {"name": "Public", "short_name": "PUB"},
                {"name": "Interne", "short_name": "INT"},
            ],
        ),
    )
    scheme = created["classification"]
    assert scheme["id"] == "sensibilite"
    assert created["changed_paths"] == ["governance/classifications/sensibilite.yaml"]
    assert [lv["name"] for lv in scheme["levels"]] == ["Public", "Interne"]
    ids = _ids(space, scheme_id="sensibilite")

    secret = (
        await _ok(app, AGENT, "create_level", {**ids, "name": "Secret", "short_name": "SEC"})
    )["level"]
    # Niveau : seul le libellé change, le nom et le nom court restent.
    relabeled = await _ok(
        app, AGENT, "update_level", {**ids, "level_id": secret["id"], "label": "Très sensible"}
    )
    assert relabeled["level"]["name"] == "Secret"
    assert relabeled["level"]["short_name"] == "SEC"
    assert relabeled["level"]["label"] == "Très sensible"

    level_ids = [lv["id"] for lv in scheme["levels"]] + [secret["id"]]
    reordered = await _ok(app, AGENT, "reorder_levels", {**ids, "order": level_ids[::-1]})
    assert [lv["name"] for lv in reordered["classification"]["levels"]] == [
        "Secret",
        "Interne",
        "Public",
    ]

    # Schéma : seule la description change, multi-valeurs reste vrai.
    described = await _ok(app, AGENT, "update_classification", {**ids, "description": "Niveau"})
    assert described["classification"]["multi_value"] is True
    assert described["classification"]["name"] == "Sensibilité"
    # La validation porte sur l'objet complet : principal + multi-valeurs refusé.
    refused = await _refusal(app, AGENT, "update_classification", {**ids, "is_primary": True})
    assert refused == {"error": "invalid_request", "fields": ["scheme_primary_multivalue"]}

    await _ok(app, AGENT, "delete_level", {**ids, "level_id": secret["id"]})
    draft = await _ok(app, AGENT, "list_classifications", _ids(space, view="draft"))
    assert [lv["name"] for lv in draft["schemes"][0]["levels"]] == ["Interne", "Public"]
    assert draft["unpublished_changes"]
    published = await _ok(app, AGENT, "list_classifications", _ids(space, view="published"))
    assert published["schemes"] == []

    deleted = await _ok(app, AGENT, "delete_classification", ids)
    assert deleted["deleted"] == "sensibilite"
    assert (await _ok(app, AGENT, "list_classifications", _ids(space)))["schemes"] == []


async def test_certifications_dans_le_brouillon(app: FastAPI, space: ApiSpace) -> None:
    assert (await _ok(app, AGENT, "list_certifications", _ids(space)))["certifications"] == []
    created = await _ok(
        app,
        AGENT,
        "create_certification",
        _ids(space, name="Or", tag="Certification/Or", description="Qualité la plus élevée"),
    )
    cert = created["certification"]
    assert cert["slug"] == "or"
    assert cert["tag"] == "certification/or"
    assert created["changed_paths"] == [f"governance/certifications/{cert['id']}.yaml"]

    # Seul le rang change ; le nom, la description et le slug restent.
    updated = await _ok(
        app, AGENT, "update_certification", _ids(space, certification_id=cert["id"], rank=2)
    )
    assert updated["certification"] == {**cert, "rank": 2}

    draft = await _ok(app, AGENT, "list_certifications", _ids(space, view="draft"))
    assert [c["name"] for c in draft["certifications"]] == ["Or"]
    assert draft["unpublished_changes"]
    published = await _ok(app, AGENT, "list_certifications", _ids(space, view="published"))
    assert published["certifications"] == []

    deleted = await _ok(
        app, AGENT, "delete_certification", _ids(space, certification_id=cert["id"])
    )
    assert deleted["deleted"] == cert["id"]
    assert (await _ok(app, AGENT, "list_certifications", _ids(space)))["certifications"] == []


# --- Critères 2 et 3 : refus nommés de l'écran, sans écriture --------------------------


async def test_refus_nommes_sans_ecriture(app: FastAPI, space: ApiSpace) -> None:
    await _ok(app, AGENT, "create_tag", _ids(space, tag_id="gouvernance/pii"))
    scheme = (
        await _ok(
            app,
            AGENT,
            "create_classification",
            _ids(space, name="Sensibilité", levels=[{"name": "Public", "short_name": "PUB"}]),
        )
    )["classification"]
    public = scheme["levels"][0]["id"]
    internal = (
        await _ok(
            app,
            AGENT,
            "create_level",
            _ids(space, scheme_id="sensibilite", name="Interne", short_name="INT"),
        )
    )["level"]["id"]
    cert = (
        await _ok(
            app, AGENT, "create_certification", _ids(space, name="Or", tag="certification/or")
        )
    )["certification"]
    before = await _draft_paths(app, space)
    sid = _ids(space, scheme_id="sensibilite")

    cases: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
        (
            "create_tag",
            _ids(space, tag_id="GOUVERNANCE/pii"),
            {"error": "tag_id_taken", "tag_id": "gouvernance/pii"},
        ),
        (
            "create_tag",
            _ids(space, tag_id="pas valide!"),
            {"error": "invalid_request", "fields": ["tag_id_invalid"]},
        ),
        (
            "update_tag",
            _ids(space, tag_id="gouvernance/absent", owner="x"),
            {"error": "tag_not_found", "tag_id": "gouvernance/absent"},
        ),
        (
            "delete_tag",
            _ids(space, tag_id="../evasion"),
            {"error": "tag_not_found", "tag_id": "../evasion"},
        ),
        (
            "create_classification",
            _ids(space, name="Sensibilite"),
            {"error": "scheme_id_taken", "scheme_id": "sensibilite"},
        ),
        (
            "create_classification",
            _ids(space, name="!!!"),
            {"error": "invalid_request", "fields": ["scheme_id_invalid"]},
        ),
        (
            "update_classification",
            _ids(space, scheme_id="absent", name="x"),
            {"error": "scheme_not_found", "scheme_id": "absent"},
        ),
        (
            "delete_classification",
            _ids(space, scheme_id="../x"),
            {"error": "scheme_not_found", "scheme_id": "../x"},
        ),
        (
            "create_level",
            {**sid, "name": "Public", "short_name": "PUB2"},
            {"error": "level_name_taken", "name": "Public"},
        ),
        (
            "create_level",
            {**sid, "name": "Autre", "short_name": "PUB"},
            {"error": "level_short_name_taken", "short_name": "PUB"},
        ),
        (
            "update_level",
            {**sid, "level_id": internal, "name": "Public"},
            {"error": "level_name_taken", "name": "Public"},
        ),
        (
            "delete_level",
            {**sid, "level_id": "absent"},
            {"error": "level_not_found", "scheme_id": "sensibilite", "level_id": "absent"},
        ),
        (
            "create_level",
            {**sid, "name": "a,b", "short_name": "AB"},
            {"error": "invalid_request", "fields": ["level_name_comma"]},
        ),
        # Critère 3 : la liste doit contenir exactement les niveaux du schéma.
        (
            "reorder_levels",
            {**sid, "order": [public]},
            {"error": "invalid_request", "fields": ["level_order_invalid"]},
        ),
        (
            "reorder_levels",
            {**sid, "order": [public, public, internal]},
            {"error": "invalid_request", "fields": ["level_order_invalid"]},
        ),
        (
            "create_certification",
            _ids(space, name="Gold", tag="certification/or"),
            {"error": "certification_tag_taken", "tag": "certification/or"},
        ),
        (
            "create_certification",
            _ids(space, name="Or bis", tag="certification/or-bis", slug="or"),
            {"error": "certification_slug_taken", "slug": "or"},
        ),
        (
            "update_certification",
            _ids(space, certification_id="absent", rank=3),
            {"error": "certification_not_found", "certification_id": "absent"},
        ),
        (
            "delete_certification",
            _ids(space, certification_id="../x"),
            {"error": "certification_not_found", "certification_id": "../x"},
        ),
        (
            "update_certification",
            _ids(space, certification_id=cert["id"], rank=0),
            {"error": "invalid_request", "fields": ["certification_rank_invalid"]},
        ),
    ]
    for name, args, expected in cases:
        assert await _refusal(app, AGENT, name, args) == expected, name

    # Aucune de ces tentatives n'a écrit : le brouillon n'a pas bougé.
    assert await _draft_paths(app, space) == before


# --- Critère 4 : droits ----------------------------------------------------------------


async def test_une_cle_ne_voit_ni_n_appelle_aucune_ecriture(app: FastAPI, space: ApiSpace) -> None:
    names = set(await tool_names(app, KEY))
    assert not WRITES & names
    assert "list_certifications" in names
    refused = await _refusal(app, KEY, "create_tag", _ids(space, tag_id="gouvernance/pii"))
    assert refused["error"] == "primitive_forbidden"


async def test_ecriture_bornee_par_les_droits_sur_l_espace(app: FastAPI, space: ApiSpace) -> None:
    async with app.state.pool.acquire() as conn:
        user_id = await conn.fetchval(
            "INSERT INTO users (email, role) VALUES ('ana@example.org', 'user') RETURNING id"
        )
        base = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) "
            "VALUES ('base-ana', '{spaces:read,governance:read}') RETURNING id"
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
            "INSERT INTO profiles (name, capabilities) VALUES ('gouv', '{governance:write}') "
            "RETURNING id"
        )
        await conn.execute(
            "INSERT INTO space_team_profiles (space_id, team_id, profile_id) VALUES ($1, $2, $3)",
            other,
            team,
            writer,
        )
        profile = await conn.fetchval("SELECT id FROM agent_profiles WHERE name = 'Gouvernance'")
    _wire(app, {"ana": McpCaller(kind="oauth", ref="a", agent_profile_id=profile, user_id=user_id)})

    # governance:write sur un AUTRE espace : outil visible, refusé sur celui-ci.
    assert "create_tag" in await tool_names(app, "ana")
    refused = await _refusal(app, "ana", "create_tag", _ids(space, tag_id="gouvernance/pii"))
    assert refused["error"] == "primitive_forbidden"

    # Donné sur cet espace : permis.
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "UPDATE profiles SET capabilities = '{spaces:read,governance:read,governance:write}' "
            "WHERE id = $1",
            base,
        )
    assert (await _ok(app, "ana", "create_tag", _ids(space, tag_id="gouvernance/pii")))["changed"]


# --- Critère 5 : annotations -------------------------------------------------------------


async def test_seules_les_suppressions_sont_destructives(app: FastAPI, space: ApiSpace) -> None:
    async with mcp_client(app, AGENT) as client:
        tools = {t.name: t.annotations for t in (await client.list_tools()).tools}
    for name in WRITES:
        assert BY_ID[name].person_only, name
        assert BY_ID[name].capability == "governance:write", name
        assert tools[name].read_only_hint is False, name
        assert tools[name].destructive_hint is name.startswith("delete_"), name
    assert tools["list_certifications"].read_only_hint is True


# --- L'écran passe par le même service ----------------------------------------------------


async def test_ecran_refuse_un_ordre_de_niveaux_avec_doublon(
    app: FastAPI, client: AsyncClient, space: ApiSpace
) -> None:
    await _ok(
        app,
        AGENT,
        "create_classification",
        _ids(
            space,
            name="Sensibilité",
            levels=[
                {"name": "Public", "short_name": "PUB"},
                {"name": "Interne", "short_name": "INT"},
            ],
        ),
    )
    base = f"/api/spaces/{space.space_id}/branches/{space.branch_id}/governance/classifications"
    levels = (await client.get(base)).json()["schemes"][0]["levels"]
    first = levels[0]["id"]
    response = await client.put(
        f"{base}/sensibilite/levels", json={"order": [first, first, levels[1]["id"]]}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    # L'agent et l'écran voient le même brouillon.
    response = await client.put(
        f"{base}/sensibilite/levels/{first}",
        json={"name": "Public", "short_name": "PUB", "label": "Ouvert"},
    )
    assert response.status_code == 200
    draft = await _ok(app, AGENT, "list_classifications", _ids(space))
    assert draft["schemes"][0]["levels"][0]["label"] == "Ouvert"
