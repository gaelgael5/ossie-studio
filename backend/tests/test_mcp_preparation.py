"""Primitives de préparation de la publication (epic « Primitives MCP
d'écriture », ticket « Préparer la publication par l'agent ») : conflits avec le
publié, fusion automatique, impacts, journal de validation. Même service que
l'écran ; réservées à un agent connecté pour une personne ; rien ne se publie ;
les décisions de l'agent sont tracées dans le message du commit."""

from __future__ import annotations

import json
import subprocess
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
from tests.space_fixture import ApiSpace, draft_write, forge_edit, make_api_space, set_credential

AGENT = "agent-preparation"
KEY = "cle-preparation"
PREPARATION = {
    "get_merge_conflicts",
    "resolve_merge",
    "auto_merge",
    "list_impacts",
    "verify_impact",
    "get_validation_log",
    "accept_validation_warning",
}
OBJ = "sources/s/raw/O.schema.yaml"
OTHER = "sources/s/raw/P.schema.yaml"
RAW = "sources/s/raw/User.schema.yaml"
ASSET = "sources/s/assets/User.schema.yaml"


def _wire(app: FastAPI, callers: dict[str, McpCaller]) -> None:
    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return callers.get(token)

    app.state.mcp_resolvers.append(resolver)


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    """Un espace ; un agent connecté pour l'administrateur (client « claude »), et
    une clé, avec un profil « Préparation » (toutes les lectures + les gestes de
    préparation)."""
    api_space = await make_api_space(client, app, tmp_path)
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives, all_read) "
            "VALUES ('Préparation', $1, true) RETURNING id",
            sorted(PREPARATION),
        )
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


async def _ok(app: FastAPI, name: str, args: dict[str, Any], token: str = AGENT) -> dict[str, Any]:
    result = await call_tool(app, token, name, args)
    assert not result.is_error, result.content[0].text
    data: dict[str, Any] = result.structured_content
    return data


async def _refusal(
    app: FastAPI, name: str, args: dict[str, Any], token: str = AGENT
) -> dict[str, Any]:
    result = await call_tool(app, token, name, args)
    assert result.is_error
    data: dict[str, Any] = json.loads(result.content[0].text)
    return data


async def _reasons(app: FastAPI, space: ApiSpace) -> list[str]:
    reasons: list[str] = (await _ok(app, "get_publish_readiness", _ids(space)))["reasons"]
    return reasons


async def _conflict(app: FastAPI, space: ApiSpace) -> None:
    """OBJ changé des deux côtés sur la même propriété."""
    forge_edit(space, {OBJ: json.dumps({"a": 1, "b": 1})})
    await draft_write(app, space, OBJ, {"a": 2, "b": 1})
    forge_edit(space, {OBJ: json.dumps({"a": 3, "b": 1})})


# --- Critère 1 : un conflit vu, résolu, et la publication ne le cite plus ---------------


async def test_conflit_vu_puis_resolu(app: FastAPI, space: ApiSpace) -> None:
    await _conflict(app, space)
    seen = await _ok(app, "get_merge_conflicts", _ids(space))
    assert seen["conflicts"] == [
        {
            "path": OBJ,
            "state": "modified",
            "kind": "conflict",
            "expects": "resolutions",
            "conflicts": [{"pointer": "/a", "base": 1, "theirs": 3, "mine": 2}],
        }
    ]
    assert "conflicts" in await _reasons(app, space)
    detail = await _ok(app, "get_merge_conflicts", _ids(space, path=OBJ))
    assert (detail["base"], detail["theirs"], detail["mine"]) == (
        {"a": 1, "b": 1},
        {"a": 3, "b": 1},
        {"a": 2, "b": 1},
    )

    done = await _ok(app, "resolve_merge", _ids(space, path=OBJ, resolutions={"/a": {"value": 42}}))
    assert done["changed_paths"] == [OBJ]
    assert "conflicts" not in await _reasons(app, space)
    assert (await _ok(app, "get_merge_conflicts", _ids(space)))["conflicts"] == []
    after = await _ok(app, "get_merge_conflicts", _ids(space, path=OBJ))
    assert after["mine"] == {"a": 42, "b": 1}


async def test_suppression_et_repli_texte_disent_ce_qu_ils_attendent(
    app: FastAPI, space: ApiSpace
) -> None:
    forge_edit(space, {OBJ: json.dumps({"a": 1}), "notes/lisez-moi.md": "ligne\n"})
    await draft_write(app, space, OBJ, {"a": 2})
    await draft_write(app, space, "notes/lisez-moi.md", "brouillon")
    forge_edit(space, {OBJ: None, "notes/lisez-moi.md": "publié\n"})
    conflicts = {
        c["path"]: c for c in (await _ok(app, "get_merge_conflicts", _ids(space)))["conflicts"]
    }
    assert conflicts[OBJ]["kind"] == "deleted_upstream"
    assert conflicts[OBJ]["expects"] == "delete"
    assert conflicts[OBJ]["theirs"] is None
    assert conflicts["notes/lisez-moi.md"]["kind"] == "text_conflict"
    assert conflicts["notes/lisez-moi.md"]["expects"] == "resolutions['']"
    assert conflicts["notes/lisez-moi.md"]["theirs"] == "publié\n"

    await _ok(app, "resolve_merge", _ids(space, path=OBJ, delete=False))
    await _ok(
        app, "resolve_merge", _ids(space, path="notes/lisez-moi.md", resolutions={"": "mine"})
    )
    assert "conflicts" not in await _reasons(app, space)


# --- Critère 2 : la fusion automatique fait ce que fait l'écran --------------------------


async def test_fusion_automatique_laisse_les_conflits(app: FastAPI, space: ApiSpace) -> None:
    await _conflict(app, space)
    forge_edit(space, {OTHER: json.dumps({"x": 1, "y": 1})})
    await draft_write(app, space, OTHER, {"x": 1, "y": 2})
    forge_edit(space, {OTHER: json.dumps({"x": 9, "y": 1})})

    seen = await _ok(app, "get_merge_conflicts", _ids(space))
    assert seen["auto_mergeable"] == [OTHER]
    assert [c["path"] for c in seen["conflicts"]] == [OBJ]

    merged = await _ok(app, "auto_merge", _ids(space))
    assert (merged["merged"], merged["conflicts"]) == ([OTHER], [OBJ])
    assert merged["changed_paths"] == [OTHER]
    detail = await _ok(app, "get_merge_conflicts", _ids(space, path=OTHER))
    assert detail["mine"] == {"x": 9, "y": 2}
    # Rejouée : rien de plus.
    again = await _ok(app, "auto_merge", _ids(space))
    assert (again["merged"], again["conflicts"]) == ([], [OBJ])


# --- Critère 3 : impacts ---------------------------------------------------------------------


async def test_impacts_verifies_par_l_agent(app: FastAPI, space: ApiSpace) -> None:
    forge_edit(space, {RAW: json.dumps({"title": "User"}), ASSET: json.dumps({"title": "User"})})
    await draft_write(app, space, RAW, {"title": "User", "v": 2})
    impacts = await _ok(app, "list_impacts", _ids(space))
    assert impacts["impacts"] == [
        {"path": ASSET, "reads": [RAW], "rules": ["raw_asset"], "verified": False}
    ]
    assert impacts["pending"] == 1
    assert "impacts_pending" in await _reasons(app, space)

    assert (await _ok(app, "verify_impact", _ids(space, path=ASSET)))["verified"] is True
    assert (await _ok(app, "list_impacts", _ids(space)))["pending"] == 0
    assert "impacts_pending" not in await _reasons(app, space)


# --- Critère 4 : avertissement accepté, erreur refusée ------------------------------------------


async def test_avertissement_accepte_erreur_refusee(app: FastAPI, space: ApiSpace) -> None:
    forge_edit(space, {RAW: json.dumps({"title": "User"})})
    await draft_write(
        app,
        space,
        "sources/s/raw/Order.schema.yaml",
        {"properties": {"u": {"$ref": "A.schema.yaml"}}},
    )
    await draft_write(app, space, "sources/s/assets/Seul.schema.yaml", {"title": "Seul"})
    log = await _ok(app, "get_validation_log", _ids(space))
    assert log["blocking"] == 1
    warning = next(e for e in log["entries"] if e["rule"] == "orphan_asset")
    error = next(e for e in log["entries"] if e["rule"] == "broken_ref")

    accepted = await _ok(
        app,
        "accept_validation_warning",
        _ids(space, rule="orphan_asset", path=warning["path"], message_code="asset_orphan"),
    )
    assert accepted["accepted"] is True
    log = await _ok(app, "get_validation_log", _ids(space))
    assert next(e for e in log["entries"] if e["rule"] == "orphan_asset")["accepted"] is True

    refused = await _refusal(
        app,
        "accept_validation_warning",
        _ids(space, rule="broken_ref", path=error["path"], message_code=error["message_code"]),
    )
    assert refused == {"error": "validation_error_not_acceptable", "path": error["path"]}
    assert (await _ok(app, "get_validation_log", _ids(space)))["blocking"] == 1


# --- Critère 5 : références inconnues, résolution incomplète : refus sans écriture --------------


async def test_refus_nommes_sans_ecriture(app: FastAPI, space: ApiSpace) -> None:
    await _conflict(app, space)
    await draft_write(app, space, OTHER, {"seul": True})  # dans le brouillon, sans conflit
    before = await _ok(app, "get_my_draft", _ids(space))

    cases: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
        (
            "get_merge_conflicts",
            _ids(space, path="ailleurs.schema.yaml"),
            {"error": "draft_object_not_found", "path": "ailleurs.schema.yaml"},
        ),
        (
            "resolve_merge",
            _ids(space, path="ailleurs.schema.yaml", resolutions={"/a": "mine"}),
            {"error": "draft_object_not_found", "path": "ailleurs.schema.yaml"},
        ),
        ("resolve_merge", _ids(space, path=OBJ), {"error": "merge_unresolved", "pointers": ["/a"]}),
        (
            "resolve_merge",
            _ids(space, path=OBJ, resolutions={"/a": "mine", "/b": "theirs"}),
            {"error": "merge_pointer_not_found", "pointers": ["/b"]},
        ),
        (
            "resolve_merge",
            _ids(space, path=OTHER, resolutions={}),
            {"error": "merge_not_needed", "path": OTHER},
        ),
        (
            "verify_impact",
            _ids(space, path="x.schema.yaml"),
            {"error": "impact_not_found", "path": "x.schema.yaml"},
        ),
        (
            "accept_validation_warning",
            _ids(space, rule="orphan_asset", path="x.schema.yaml", message_code="asset_orphan"),
            {"error": "warning_not_found", "path": "x.schema.yaml"},
        ),
    ]
    for name, args, expected in cases:
        assert await _refusal(app, name, args) == expected, name
    bad_choice = await _refusal(
        app, "resolve_merge", _ids(space, path=OBJ, resolutions={"/a": "les-deux"})
    )
    assert bad_choice["error"] == "invalid_arguments"

    assert await _ok(app, "get_my_draft", _ids(space)) == before
    assert [
        c["path"] for c in (await _ok(app, "get_merge_conflicts", _ids(space)))["conflicts"]
    ] == [OBJ]


async def test_sans_brouillon_rien_a_preparer(app: FastAPI, space: ApiSpace) -> None:
    assert await _ok(app, "get_merge_conflicts", _ids(space)) == {
        "view": "draft",
        "conflicts": [],
        "auto_mergeable": [],
    }
    assert (await _ok(app, "auto_merge", _ids(space)))["merged"] == []
    assert (await _ok(app, "list_impacts", _ids(space)))["impacts"] == []


# --- Critère 6 : droits ----------------------------------------------------------------------


async def test_une_cle_ne_voit_ni_n_appelle_aucune_preparation(
    app: FastAPI, space: ApiSpace
) -> None:
    assert not PREPARATION & set(await tool_names(app, KEY))
    refused = await _refusal(app, "list_impacts", _ids(space), token=KEY)
    assert refused["error"] == "primitive_forbidden"


async def test_sans_droit_sur_l_espace_refuse(app: FastAPI, space: ApiSpace) -> None:
    async with app.state.pool.acquire() as conn:
        user_id = await conn.fetchval(
            "INSERT INTO users (email, role) VALUES ('ana@example.org', 'user') RETURNING id"
        )
        base = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) VALUES ('base-ana', '{}') RETURNING id"
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
        reader = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) VALUES ('lecteur', '{spaces:read}') "
            "RETURNING id"
        )
        await conn.execute(
            "INSERT INTO space_team_profiles (space_id, team_id, profile_id) VALUES ($1, $2, $3)",
            other,
            team,
            reader,
        )
        profile = await conn.fetchval("SELECT id FROM agent_profiles WHERE name = 'Préparation'")
    _wire(app, {"ana": McpCaller(kind="oauth", ref="a", agent_profile_id=profile, user_id=user_id)})

    # spaces:read sur un AUTRE espace seulement : outil visible, refusé sur celui-ci.
    assert "resolve_merge" in await tool_names(app, "ana")
    for name, args in (
        ("get_merge_conflicts", _ids(space)),
        ("resolve_merge", _ids(space, path=OBJ, delete=True)),
        ("verify_impact", _ids(space, path=ASSET)),
    ):
        refused = await _refusal(app, name, args, token="ana")
        assert refused["error"] == "primitive_forbidden", name


async def test_annotations(app: FastAPI, space: ApiSpace) -> None:
    async with mcp_client(app, AGENT) as client:
        tools = {t.name: t.annotations for t in (await client.list_tools()).tools}
    for name in PREPARATION:
        assert BY_ID[name].person_only, name
        assert BY_ID[name].capability == "spaces:read", name
    for name in ("get_merge_conflicts", "list_impacts", "get_validation_log"):
        assert tools[name].read_only_hint is True, name
    assert tools["resolve_merge"].destructive_hint is True
    for name in ("auto_merge", "verify_impact", "accept_validation_warning"):
        assert tools[name].read_only_hint is False, name
        assert tools[name].destructive_hint is False, name
        assert tools[name].idempotent_hint is True, name


# --- Traçabilité : les décisions de l'agent sont dites dans le commit -----------------------------


def _last_message(space: ApiSpace) -> str:
    return subprocess.run(
        [
            "git",
            "--git-dir",
            space.url.removeprefix("file://"),
            "log",
            "-1",
            "--format=%B",
            "work/principale",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


async def test_decisions_de_l_agent_dans_le_message_du_commit(
    app: FastAPI, space: ApiSpace
) -> None:
    forge_edit(
        space,
        {
            "sources/s/raw/U.schema.yaml": "{}",
            "sources/s/assets/U.schema.yaml": "{}",
            "sources/s/raw/V.schema.yaml": "{}",
            "sources/s/assets/V.schema.yaml": "{}",
        },
    )
    await draft_write(app, space, "sources/s/raw/U.schema.yaml", {"v": 2})
    await draft_write(app, space, "sources/s/raw/V.schema.yaml", {"v": 2})
    await draft_write(app, space, "sources/s/assets/Orphelin.schema.yaml", {"t": 1})
    await set_credential(app, space)
    # L'agent vérifie U et accepte l'avertissement ; la personne vérifie V.
    await _ok(app, "verify_impact", _ids(space, path="sources/s/assets/U.schema.yaml"))
    await _ok(
        app,
        "accept_validation_warning",
        _ids(
            space,
            rule="orphan_asset",
            path="sources/s/assets/Orphelin.schema.yaml",
            message_code="asset_orphan",
        ),
    )
    response = await space.client.post(
        f"{space.base}/draft/impacts/verify", json={"path": "sources/s/assets/V.schema.yaml"}
    )
    assert response.status_code == 200
    response = await space.client.post(
        f"{space.base}/draft/publish", json={"comment": "Préparé avec l'agent"}
    )
    assert response.status_code == 200, response.text
    body = _last_message(space)
    assert "Impact vérifié : sources/s/assets/U.schema.yaml (via l'agent claude)" in body
    assert "Impact vérifié : sources/s/assets/V.schema.yaml\n" in body
    assert (
        "Avertissement accepté : orphan_asset sources/s/assets/Orphelin.schema.yaml "
        "(via l'agent claude)" in body
    )


async def test_une_decision_reprise_par_la_personne_n_est_plus_celle_de_l_agent(
    app: FastAPI, space: ApiSpace
) -> None:
    forge_edit(space, {"sources/s/raw/U.schema.yaml": "{}", "sources/s/assets/U.schema.yaml": "{}"})
    await draft_write(app, space, "sources/s/raw/U.schema.yaml", {"v": 2})
    await set_credential(app, space)
    await _ok(app, "verify_impact", _ids(space, path="sources/s/assets/U.schema.yaml"))
    await space.client.post(
        f"{space.base}/draft/impacts/verify", json={"path": "sources/s/assets/U.schema.yaml"}
    )
    response = await space.client.post(f"{space.base}/draft/publish", json={"comment": "Repris"})
    assert response.status_code == 200, response.text
    body = _last_message(space)
    assert "Impact vérifié : sources/s/assets/U.schema.yaml\n" in body
    assert "via l'agent" not in body
