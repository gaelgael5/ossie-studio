"""Le moteur d'import écrit par le registre des genres (epic « Primitives MCP —
écriture commune par genre », ticket 1612cce4) : une ligne d'import invalide est
refusée avec le même code, le même champ et le même message que la même entrée
envoyée à `write_documents` ; l'import n'a plus de validation propre."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.cartographie import import_target as carto_import
from ossie_studio.mcp_surface.caller import McpCaller
from ossie_studio.referentiels import import_target as referentiel_import
from ossie_studio.referentiels.model import dump_model
from ossie_studio.referentiels.repo import model_path, values_path
from ossie_studio.referentiels.values import dump_values
from tests.mcp_helpers import call_tool
from tests.space_fixture import ApiSpace, forge_edit, make_api_space

AGENT = "import-registre"
MODEL: dict[str, Any] = {
    "title": "Délais",
    "type": "object",
    "x-key": ["pays", "mode"],
    "required": ["pays", "mode"],
    "properties": {
        "pays": {"type": "string"},
        "mode": {"type": "string"},
        "delai": {"type": "integer"},
    },
}


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    api_space = await make_api_space(client, app, tmp_path)
    forge_edit(
        api_space,
        {
            "cartographie/types/application.schema.yaml": (
                "title: Application\nx-archimate: ApplicationComponent\ntype: object\n"
                "properties:\n  criticite:\n    type: string\n    enum: [haute, basse]\n"
            ),
            model_path("delais"): dump_model(MODEL),
            values_path("delais"): dump_values(MODEL, []),
        },
    )
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives, all_read) VALUES ('imp', $1, true) "
            "RETURNING id",
            ["write_documents"],
        )
        admin_id = await conn.fetchval("SELECT id FROM users WHERE is_bootstrap")
    caller = McpCaller(kind="oauth", ref="i", agent_profile_id=profile, user_id=admin_id)

    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return caller if token == AGENT else None

    app.state.mcp_resolvers.append(resolver)
    return api_space


async def _declare(space: ApiSpace, base: str, template: str, example: str) -> None:
    created = await space.client.post(
        base, json={"label": "Fichier", "slug": "fichier", "format": "csv"}
    )
    assert created.status_code == 201, created.text
    await space.client.put(
        f"{base}/fichier/exemple", files={"file": ("f.csv", example.encode(), "text/csv")}
    )
    await space.client.put(
        f"{base}/fichier/lecture", json={"separator": ";", "encoding": "utf-8", "header": True}
    )
    saved = await space.client.put(f"{base}/fichier/template", json={"template": template})
    assert saved.json()["template_problems"] == []


async def _import_errors(space: ApiSpace, base: str) -> list[dict[str, Any]]:
    refused = await space.client.post(f"{base}/fichier/import", data={"mode": "merge"})
    assert refused.status_code == 422, refused.text
    errors: list[dict[str, Any]] = refused.json()["error_args"]["report"]["error_details"]
    return errors


async def _mcp_errors(app: FastAPI, space: ApiSpace, entry: dict[str, Any]) -> list[dict]:
    result = await call_tool(
        app,
        AGENT,
        "write_documents",
        {
            "space_id": space.space_id,
            "branch_id": space.branch_id,
            "message": "même entrée",
            "documents": [entry],
        },
    )
    assert result.is_error
    errors: list[dict[str, Any]] = json.loads(result.content[0].text)["errors"]
    return errors


def _same(imported: dict[str, Any], mcp: dict[str, Any]) -> None:
    assert imported["code"] == mcp["code"]
    assert imported["message"] == mcp["message"]
    assert imported["field"] == mcp["path"].replace("$.documents[0].content", "$")


async def test_objet_de_cartographie_meme_refus(app: FastAPI, space: ApiSpace) -> None:
    base = f"{space.base}/cartographie/types/application/imports"
    template = '{"cle": $.cle, "nom": $.nom, "proprietes": {"criticite": $.criticite}}'
    await _declare(space, base, template, "cle;nom;criticite\nAPP-NEW;Nouvelle;moyenne\n")
    [imported] = await _import_errors(space, base)
    [mcp] = await _mcp_errors(
        app,
        space,
        {
            "kind": "carto_objet",
            "identity": {"type": "application", "key": "APP-NEW"},
            "content": {
                "cle": "APP-NEW",
                "nom": "Nouvelle",
                "proprietes": {"criticite": "moyenne"},
            },
        },
    )
    _same(imported, mcp)
    assert imported["code"] == "carto_object_invalid" and imported["key"] == "APP-NEW"


async def test_valeur_de_referentiel_meme_refus(app: FastAPI, space: ApiSpace) -> None:
    base = f"{space.base}/referentiels/delais/imports"
    template = '{"pays": $.Pays, "mode": $.Mode, "delai": $.Delai}'
    await _declare(space, base, template, "Pays;Mode;Delai\nLU;site;deux\n")
    [imported] = await _import_errors(space, base)
    [mcp] = await _mcp_errors(
        app,
        space,
        {
            "kind": "referentiel_valeur",
            "identity": {"referentiel": "delais", "key": {"pays": "LU", "mode": "site"}},
            "content": {"pays": "LU", "mode": "site", "delai": "deux"},
        },
    )
    _same(imported, mcp)
    assert imported["message"] == "Valeur LU+site : delai doit être de type integer."


def test_plus_de_validation_propre_a_l_import() -> None:
    """Critère 2 : les cibles d'import appellent le registre, jamais directement les
    contrôles des services."""
    for module in (carto_import, referentiel_import):
        assert "check_import(" in inspect.getsource(module), module.__name__
        for service_check in ("check_batch", "check_values", "save_object"):
            assert not hasattr(module, service_check), (module.__name__, service_check)
