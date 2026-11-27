"""Primitive MCP commune `read_document` (epic « Primitives MCP — écriture commune par
genre », ticket 69be2ef9) : un document se lit par genre et identité, avec son contenu
brut, son fichier et la version qu'attendent `write_documents` et `patch_document`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import yaml
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.cartographie.process import new_process_xml, with_detail
from ossie_studio.mcp_surface.caller import McpCaller
from tests.conftest import create_plain_user
from tests.mcp_helpers import call_tool
from tests.space_fixture import ApiSpace, forge_edit, make_api_space

KEY = "doc-reads-key"
AGENT = "doc-reads-agent"
LIMITED = "doc-reads-limited"

PRC = "cartographie/objets/processus-de-vente/prc-prise-commande.yaml"
PRC_ID = {"type": "processus-de-vente", "key": "PRC-PRISE-COMMANDE"}
PROCESS = "cartographie/processus/prc-vente-detail.bpmn"
PAYS = {
    "title": "Pays",
    "type": "object",
    "x-key": ["code"],
    "required": ["code", "nom"],
    "properties": {"code": {"type": "string"}, "nom": {"type": "string"}},
}
VALUES = [{"code": "BE", "nom": "Belgique"}, {"code": "FR", "nom": "France"}]


def _process_xml() -> str:
    return with_detail(
        new_process_xml("PRC-VENTE-DETAIL", "Vente"), "PRC-PRISE-COMMANDE", "processus-de-vente"
    )


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    api_space = await make_api_space(client, app, tmp_path)
    forge_edit(
        api_space,
        {
            "cartographie/types/processus-de-vente.schema.yaml": (
                "title: Processus de vente\nx-archimate: BusinessProcess\ntype: object\n"
            ),
            PRC: "cle: PRC-PRISE-COMMANDE\ntype: processus-de-vente\nnom: Prise de commande\n",
            PROCESS: _process_xml(),
            "referentiels/pays/modele.schema.yaml": yaml.safe_dump(PAYS),
            "referentiels/pays/valeurs.json": json.dumps(VALUES),
            "governance/tags/pii/email.yaml": "id: pii/email\ninfo: {owner: dpo}\n",
        },
    )
    await create_plain_user(app, "lea@example.org", "motdepasse-solide")
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives, all_read) VALUES ('dr', $1, true) "
            "RETURNING id",
            ["read_document", "write_documents", "patch_document"],
        )
        admin_id = await conn.fetchval("SELECT id FROM users WHERE is_bootstrap")
        lea_id = await conn.fetchval("SELECT id FROM users WHERE email = 'lea@example.org'")
        carto_only = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) VALUES ('carto-lecture', "
            "'{spaces:read,cartographie:read}') RETURNING id"
        )
        await conn.execute("UPDATE users SET profile_id = $2 WHERE id = $1", lea_id, carto_only)
    callers = {
        KEY: McpCaller(kind="key", ref="k", agent_profile_id=profile),
        AGENT: McpCaller(
            kind="oauth", ref="c", agent_profile_id=profile, user_id=admin_id, client_id="claude"
        ),
        LIMITED: McpCaller(
            kind="oauth", ref="l", agent_profile_id=profile, user_id=lea_id, client_id="claude"
        ),
    }

    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return callers.get(token)

    app.state.mcp_resolvers.append(resolver)
    return api_space


async def _call(
    app: FastAPI, tool: str, space: ApiSpace, token: str = AGENT, **args: Any
) -> tuple[bool, dict[str, Any]]:
    ids = {"space_id": space.space_id, "branch_id": space.branch_id}
    result = await call_tool(app, token, tool, {**ids, **args})
    body = result.structured_content if not result.is_error else json.loads(result.content[0].text)
    return result.is_error, body


async def _read(
    app: FastAPI, space: ApiSpace, kind: str, identity: dict[str, Any], **args: Any
) -> dict[str, Any]:
    error, body = await _call(app, "read_document", space, kind=kind, identity=identity, **args)
    assert not error, body
    return body


async def test_contenu_chemin_version_puis_patch(app: FastAPI, space: ApiSpace) -> None:
    """Critère 2 : la version rendue, telle quelle, permet un patch_document qui réussit."""
    found = await _read(app, space, "carto_objet", PRC_ID)
    assert found["view"] == "published" and found["path"] == PRC
    assert found["content"] == {
        "cle": "PRC-PRISE-COMMANDE",
        "type": "processus-de-vente",
        "nom": "Prise de commande",
    }
    assert found["identity"] == PRC_ID and not found["unpublished"]
    assert len(found["version"]) == 40
    error, patched = await _call(
        app,
        "patch_document",
        space,
        kind="carto_objet",
        identity=PRC_ID,
        version=found["version"],
        operations=[{"op": "set", "path": "$.nom", "value": "Prise de commande en boutique"}],
    )
    assert not error, patched


async def test_relire_ce_qu_on_vient_d_ecrire(app: FastAPI, space: ApiSpace) -> None:
    """Critère 5, et critère 14 du ticket 10cc4ed6 : `branch` absent lit la branche
    publiée ; `draft` relit ce que write_documents vient d'écrire, avec sa version."""
    content = {"cle": "PRC-PRISE-COMMANDE", "type": "processus-de-vente", "nom": "Vente"}
    published = await _read(app, space, "carto_objet", PRC_ID)
    error, written = await _call(
        app,
        "write_documents",
        space,
        message="Renommer",
        documents=[
            {
                "kind": "carto_objet",
                "identity": PRC_ID,
                "version": published["version"],
                "content": content,
            }
        ],
    )
    assert not error, written
    draft = await _read(app, space, "carto_objet", PRC_ID, branch="draft")
    assert draft["view"] == "draft" and draft["unpublished"]
    assert draft["content"] == content
    assert draft["version"] == written["documents"][0]["version"]
    again = await _read(app, space, "carto_objet", PRC_ID)
    assert again["content"]["nom"] == "Prise de commande"
    assert again["version"] == published["version"]


async def test_processus_rend_le_xml(app: FastAPI, space: ApiSpace) -> None:
    """Critère 3 : le BPMN se lit en texte XML, par la primitive commune."""
    found = await _read(app, space, "processus", {"key": "PRC-VENTE-DETAIL"})
    assert found["path"] == PROCESS
    assert isinstance(found["content"], str)
    assert found["content"].startswith("<?xml") and "bpmn:definitions" in found["content"]
    assert "urban:reference" in found["content"]


async def test_valeur_de_referentiel_seule(app: FastAPI, space: ApiSpace) -> None:
    """La seule valeur demandée, pas tout valeurs.json ; sa version sert au patch."""
    found = await _read(app, space, "referentiel_valeur", {"referentiel": "pays", "key": "FR"})
    assert found["content"] == {"code": "FR", "nom": "France"}
    assert found["path"] == "referentiels/pays/valeurs.json"
    error, patched = await _call(
        app,
        "patch_document",
        space,
        kind="referentiel_valeur",
        identity={"referentiel": "pays", "key": "FR"},
        version=found["version"],
        operations=[{"op": "set", "path": "$.nom", "value": "République française"}],
    )
    assert not error, patched
    model = await _read(app, space, "referentiel_modele", {"referentiel": "pays"})
    assert model["content"]["x-key"] == ["code"]


async def test_identite_inconnue_ou_invalide_et_chemin(app: FastAPI, space: ApiSpace) -> None:
    """Critère 4 : identité inconnue → erreur explicite ; chemin fourni → refus."""
    ghost = {"type": "processus-de-vente", "key": "PRC-FANTOME"}
    error, body = await _call(app, "read_document", space, kind="carto_objet", identity=ghost)
    assert error and body["error"] == "document_not_found"
    assert body["identity"] == ghost
    assert body["expected_path"] == "cartographie/objets/processus-de-vente/prc-fantome.yaml"
    assert "aide" not in body
    error, body = await _call(
        app, "read_document", space, kind="carto_objet", identity={"type": "inconnu", "key": "A"}
    )
    assert error and body["error"] == "write_identity_invalid"
    assert body["path"] == "$.identity.type"
    error, body = await _call(
        app, "read_document", space, kind="carto_objet", identity=PRC_ID, path=PRC
    )
    assert error and body["error"] == "write_path_forbidden" and body["expected"] == PRC
    assert body["aide"] == {"primitive": "describe_standard", "standard": "writing-rules"}
    error, body = await _call(app, "read_document", space, kind="dataset", identity={})
    assert error and body["error"] == "write_kind_unknown"
    assert body["aide"] == {"primitive": "list_write_kinds"}
    error, body = await _call(
        app, "read_document", space, kind="certification", identity={"slug": "or"}
    )
    assert error and body["error"] == "document_not_found" and "expected_path" not in body


async def test_droits_et_cle_d_api(app: FastAPI, space: ApiSpace) -> None:
    """La capacité de lecture du genre est celle de l'écran ; une clé d'API lit la
    branche publiée, jamais un brouillon."""
    found = await _read(app, space, "carto_objet", PRC_ID, token=LIMITED)
    assert found["path"] == PRC
    error, body = await _call(
        app, "read_document", space, LIMITED, kind="tag", identity={"id": "pii/email"}
    )
    assert error and body["error"] == "primitive_forbidden"
    assert body["capability"] == "governance:read" and body["kind"] == "tag"
    tag = await _read(app, space, "tag", {"id": "pii/email"}, token=KEY)
    assert tag["content"]["info"] == {"owner": "dpo"}
    error, body = await _call(
        app, "read_document", space, KEY, kind="tag", identity={"id": "pii/email"}, branch="draft"
    )
    assert error and body["error"] == "draft_requires_person"
