"""Les profils de relations par les primitives communes (epic « Primitives MCP —
lectures communes », ticket 970c90ae) : genre `profil_relation`, lisible et écrivable
par la méthode de l'écran ; le profil fourni « Data Vault 2.0 » se personnalise et se
rétablit comme à l'écran."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.mcp_surface.caller import McpCaller
from ossie_studio.sources.explorer import clear_caches
from ossie_studio.sources.sql_extract import _slug
from tests.mcp_helpers import call_tool
from tests.space_fixture import ApiSpace, forge_edit, make_api_space
from tests.test_relation_profiles import _files

AGENT = "profils-agent"
READER = "profils-lecteur"
DATAVAULT = "ossie-datavault"
ETOILE = {
    "document_type": "profil_relation",
    "identity": {"id": "etoile"},
    "content": {
        "name": "Étoile",
        "rules": [
            {"column": "(?<nom>[A-Z]+)_HK", "table": "{nom}_HUB", "target_column": "{nom}_HK"}
        ],
    },
}


@pytest.fixture(autouse=True)
def _fresh_caches() -> None:
    clear_caches()


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    """Une source Snowflake Data Vault qui applique le profil fourni. Un agent qui
    écrit pour l'administrateur, et un agent pour une personne qui ne fait que lire
    la gouvernance."""
    api_space = await make_api_space(client, app, tmp_path)
    forge_edit(api_space, _files(profiles=[DATAVAULT]))
    async with app.state.pool.acquire() as conn:
        writer = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives, all_read) VALUES ('p', $1, true) "
            "RETURNING id",
            ["write_documents", "patch_document"],
        )
        admin_id = await conn.fetchval("SELECT id FROM users WHERE is_bootstrap")
        reader_id = await conn.fetchval(
            "INSERT INTO users (email, role) VALUES ('lea@example.org', 'user') RETURNING id"
        )
        base = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) "
            "VALUES ('lecture-gouv', '{spaces:read,governance:read}') RETURNING id"
        )
        await conn.execute("UPDATE users SET profile_id = $2 WHERE id = $1", reader_id, base)
    callers = {
        AGENT: McpCaller(kind="oauth", ref="a", agent_profile_id=writer, user_id=admin_id),
        READER: McpCaller(kind="oauth", ref="l", agent_profile_id=writer, user_id=reader_id),
    }

    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return callers.get(token)

    app.state.mcp_resolvers.append(resolver)
    return api_space


async def _call(app: FastAPI, space: ApiSpace, tool: str, token: str = AGENT, **args: Any) -> Any:
    ids = {"space_id": space.space_id, "branch_id": space.branch_id}
    result = await call_tool(app, token, tool, {**ids, **args})
    if result.is_error:
        return True, json.loads(result.content[0].text)
    return False, result.structured_content


async def _profiles(app: FastAPI, space: ApiSpace, token: str = AGENT) -> dict[str, Any]:
    error, listed = await _call(
        app, space, "list_documents", token, document_type="profil_relation", branch="draft"
    )
    assert not error, listed
    return {d["identity"]["id"]: d for d in listed["documents"]}


async def _read(app: FastAPI, space: ApiSpace, profile_id: str) -> dict[str, Any]:
    error, read = await _call(
        app,
        space,
        "read_document",
        document_type="profil_relation",
        identity={"id": profile_id},
        branch="draft",
    )
    assert not error, read
    return read  # type: ignore[no-any-return]


async def _virtual(space: ApiSpace) -> list[str]:
    """Les références virtuelles d'un satellite, telles que l'Explorateur les montre."""
    sat = {"file": f"{_slug('BV.SAT_ACCOUNT')}.schema.yaml"}
    r = await space.client.get(f"{space.base}/raw-explorer/dwh/object", params=sat)
    assert r.status_code == 200, r.text
    return [x["label"] for x in r.json()["references"] if x.get("virtual")]


async def test_le_profil_fourni_est_liste_sans_fichier(app: FastAPI, space: ApiSpace) -> None:
    """Critère 1."""
    profiles = await _profiles(app, space)
    datavault = profiles[DATAVAULT]
    assert (datavault["builtin"], datavault["customized"]) == (True, False)
    assert datavault["name"] == "Data Vault 2.0" and datavault["rules"] == 2
    read = await _read(app, space, DATAVAULT)
    assert read["content"]["rules"][0]["table"] == "{nom}_HUB"
    assert read["version"] == datavault["version"]


async def test_creer_un_profil_et_refus_au_champ(app: FastAPI, space: ApiSpace) -> None:
    """Critère 2 : un profil créé est un fichier ; une expression invalide est refusée
    avec le chemin fautif et `aide`."""
    error, body = await _call(app, space, "write_documents", message="profil", documents=[ETOILE])
    assert not error, body
    assert body["documents"][0]["path"] == "governance/relation-profiles/etoile.yaml"
    profiles = await _profiles(app, space)
    assert (profiles["etoile"]["builtin"], profiles["etoile"]["name"]) == (False, "Étoile")
    bad_rule = {"column": "(", "table": "T", "target_column": "C"}
    bad = {**ETOILE, "identity": {"id": "casse"}, "content": {"name": "X", "rules": [bad_rule]}}
    error, body = await _call(app, space, "write_documents", message="profil", documents=[bad])
    assert error
    [refusal] = body["errors"]
    assert refusal["code"] == "write_content_invalid"
    assert refusal["path"] == "$.documents[0].content.rules[0].column"
    assert refusal["aide"] == {"primitive": "describe_standard", "standard": "profil-relation"}
    template = {**bad_rule, "column": "(?<nom>[A-Z]+)_HK", "table": "{autre}_HUB"}
    bad["content"] = {"name": "X", "rules": [ETOILE["content"]["rules"][0], template]}  # type: ignore[index]
    error, body = await _call(app, space, "write_documents", message="profil", documents=[bad])
    assert error and body["errors"][0]["path"] == "$.documents[0].content.rules[1].table"


async def test_personnaliser_puis_retablir_le_profil_fourni(app: FastAPI, space: ApiSpace) -> None:
    """Critères 3 et 5 : patch du profil fourni → personnalisé, et les relations de
    l'Explorateur suivent ; supprimer la personnalisation le rétablit."""
    assert await _virtual(space) == ["BV.ACCOUNT_HUB.ACCOUNT_HK"]
    read = await _read(app, space, DATAVAULT)
    operations = [
        {"op": "set", "path": "$.name", "value": "Data Vault maison"},
        {"op": "set", "path": "$.rules[0].table", "value": "H_{nom}"},
        {"op": "remove", "path": "$.rules[1]"},
    ]
    error, body = await _call(
        app,
        space,
        "patch_document",
        document_type="profil_relation",
        identity={"id": DATAVAULT},
        version=read["version"],
        operations=operations,
    )
    assert not error, body
    profiles = await _profiles(app, space)
    assert profiles[DATAVAULT]["customized"] is True
    assert profiles[DATAVAULT]["name"] == "Data Vault maison"
    assert await _virtual(space) == []
    customized = await _read(app, space, DATAVAULT)
    delete = {
        "document_type": "profil_relation",
        "identity": {"id": DATAVAULT},
        "version": customized["version"],
        "delete": True,
    }
    error, body = await _call(app, space, "write_documents", message="rétablir", documents=[delete])
    assert not error, body
    profiles = await _profiles(app, space)
    assert profiles[DATAVAULT]["customized"] is False
    assert profiles[DATAVAULT]["name"] == "Data Vault 2.0"
    assert await _virtual(space) == ["BV.ACCOUNT_HUB.ACCOUNT_HK"]


async def test_le_profil_fourni_ne_se_supprime_pas(app: FastAPI, space: ApiSpace) -> None:
    """Critère 4."""
    read = await _read(app, space, DATAVAULT)
    delete = {
        "document_type": "profil_relation",
        "identity": {"id": DATAVAULT},
        "version": read["version"],
        "delete": True,
    }
    error, body = await _call(app, space, "write_documents", message="x", documents=[delete])
    assert error
    [refusal] = body["errors"]
    assert refusal["code"] == "write_delete_unsupported"
    assert "rétablir" in refusal["message"]


async def test_droits_de_l_ecran(app: FastAPI, space: ApiSpace) -> None:
    """Critère 6 : la lecture suffit pour lister et lire ; écrire demande le droit
    d'écriture de la gouvernance."""
    profiles = await _profiles(app, space, READER)
    assert DATAVAULT in profiles
    error, body = await _call(
        app, space, "write_documents", READER, message="profil", documents=[ETOILE]
    )
    assert error
    assert "governance:write" in json.dumps(body)


async def test_le_genre_est_declare(app: FastAPI, space: ApiSpace) -> None:
    """Critère 7 (les exemples du standard passent le validateur : test commun des
    standards)."""
    kinds = (await call_tool(app, AGENT, "list_document_types", {})).structured_content[
        "document_types"
    ]
    profile = next(k for k in kinds if k["document_type"] == "profil_relation")
    assert profile["writable"] is True
    assert (profile["write_capability"], profile["read_capability"]) == (
        "governance:write",
        "governance:read",
    )
