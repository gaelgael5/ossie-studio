"""Primitives MCP d'écriture de la cartographie (ticket d4723be3) : une liste en
bloc, une modification par JSONPath ; mêmes contrôles que l'écran, erreurs
localisées dans la requête, rien d'écrit au moindre refus."""

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
from tests.mcp_helpers import call_tool, tool_names
from tests.space_fixture import ApiSpace, forge_edit, make_api_space

KEY = "carto-writes-key"
AGENT = "carto-writes-agent"
WRITES = ["write_carto_documents", "modify_carto_document"]

TYPES = {
    "role": ("Rôle", "BusinessRole", ""),
    "affectation": ("Affectation", "Assignment", ""),
    "document-metier": ("Document métier", "BusinessObject", ""),
    "composition": ("Composition", "Composition", ""),
    "acces": ("Accès", "Access", ""),
    "processus-de-vente": (
        "Processus de vente",
        "BusinessProcess",
        "properties:\n"
        "  canal:\n    type: string\n    enum: [boutique, internet]\n"
        "  volume_jour:\n    type: integer\n    minimum: 0\n",
    ),
}
PRC = "cartographie/objets/processus-de-vente/prc-prise-commande.yaml"
OBJ = "cartographie/objets/document-metier/obj-commande.yaml"
REL = "cartographie/objets/acces/rel-prise-commande-ecrit-commande.yaml"


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    api_space = await make_api_space(client, app, tmp_path)
    files = {
        f"cartographie/types/{slug}.schema.yaml": (
            f"title: {title}\nx-archimate: {arch}\ntype: object\n{extra}"
        )
        for slug, (title, arch, extra) in TYPES.items()
    }
    files.update(
        {
            PRC: "cle: PRC-PRISE-COMMANDE\ntype: processus-de-vente\nnom: Prise de commande\n",
            OBJ: "cle: OBJ-COMMANDE\ntype: document-metier\nnom: Commande\n",
            REL: (
                "cle: REL-PRISE-COMMANDE-ECRIT-COMMANDE\ntype: acces\n"
                "source: PRC-PRISE-COMMANDE\ncible: OBJ-COMMANDE\nnom: Écrit\n"
            ),
        }
    )
    forge_edit(api_space, files)
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives, all_read) VALUES ('cw', $1, true) "
            "RETURNING id",
            WRITES,
        )
        admin_id = await conn.fetchval("SELECT id FROM users WHERE is_bootstrap")
    callers = {
        KEY: McpCaller(kind="key", ref="k", agent_profile_id=profile),
        AGENT: McpCaller(
            kind="oauth", ref="c", agent_profile_id=profile, user_id=admin_id, client_id="claude"
        ),
    }

    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return callers.get(token)

    app.state.mcp_resolvers.append(resolver)
    return api_space


def _ids(space: ApiSpace, **extra: Any) -> dict[str, Any]:
    return {"space_id": space.space_id, "branch_id": space.branch_id, **extra}


async def _call(app: FastAPI, tool: str, space: ApiSpace, **args: Any) -> tuple[bool, dict]:
    result = await call_tool(app, AGENT, tool, _ids(space, **args))
    body = result.structured_content if not result.is_error else json.loads(result.content[0].text)
    return result.is_error, body


async def _write(app: FastAPI, space: ApiSpace, documents: list[dict[str, Any]]) -> tuple:
    return await _call(
        app, "write_carto_documents", space, message="Atelier ventes", documents=documents
    )


async def _read(app: FastAPI, space: ApiSpace, path: str) -> dict[str, Any] | None:
    error, body = await _call(app, "get_carto_object", space, path=path, branch="draft")
    return None if error else body


ROLE = {
    "path": "cartographie/objets/role/rol-preparateur.yaml",
    "content": {"cle": "ROL-PREPARATEUR", "type": "role", "nom": "Préparateur"},
}
ASSIGN = {
    "type": "affectation",
    "key": "REL-PREPARATEUR-PRISE-COMMANDE",
    "content": {
        "cle": "REL-PREPARATEUR-PRISE-COMMANDE",
        "type": "affectation",
        "source": "ROL-PREPARATEUR",
        "cible": "PRC-PRISE-COMMANDE",
        "nom": "Prépare",
    },
}


async def test_role_et_affectation_dans_la_meme_liste(app: FastAPI, space: ApiSpace) -> None:
    """Critères 1 et 9 : l'affectation, listée avant le rôle qu'elle vise, passe ;
    un seul enregistrement ; relus en brouillon, marqués non publiés."""
    error, body = await _write(app, space, [ASSIGN, ROLE])
    assert not error, body
    assert body["written"] is True and body["revision"].startswith("draft:")
    paths = {d["path"]: d for d in body["documents"]}
    assert set(paths) == {
        "cartographie/objets/role/rol-preparateur.yaml",
        "cartographie/objets/affectation/rel-preparateur-prise-commande.yaml",
    }
    assert all(d["created"] and len(d["version"]) == 40 for d in paths.values())
    role = await _read(app, space, ROLE["path"])
    assert role is not None and role["unpublished"] is True
    assert role["version"] == paths[ROLE["path"]]["version"]
    # Rien n'est publié.
    published = await call_tool(app, AGENT, "get_carto_object", _ids(space, key="ROL-PREPARATEUR"))
    assert published.is_error


async def test_un_document_invalide_refuse_toute_la_liste(app: FastAPI, space: ApiSpace) -> None:
    """Critère 2 : rien n'est écrit, l'erreur nomme le JSONPath dans la requête."""
    bad = {
        "path": "cartographie/objets/processus-de-vente/prc-livraison.yaml",
        "content": {
            "cle": "PRC-LIVRAISON",
            "type": "processus-de-vente",
            "nom": "Livraison",
            "proprietes": {"volume_jour": -5},
        },
    }
    error, body = await _write(app, space, [ROLE, ASSIGN, bad])
    assert error and body["error"] == "carto_write_refused" and body["written"] is False
    [problem] = body["errors"]
    assert problem["path"] == "$.documents[2].content.proprietes.volume_jour"
    assert problem["code"] == "carto_object_invalid"
    assert problem["received"] == -5
    assert problem["expected"] == {"type": "integer", "minimum": 0}
    assert "entier supérieur ou égal à 0" in problem["message"] and "-5" in problem["message"]
    for doc in (ROLE["path"], bad["path"]):
        assert await _read(app, space, doc) is None


async def test_relation_non_permise_cite_les_permises(app: FastAPI, space: ApiSpace) -> None:
    """Critère 3."""
    composition = {
        "type": "composition",
        "key": "REL-PRISE-COMMANDE-COMPOSE-COMMANDE",
        "content": {
            "cle": "REL-PRISE-COMMANDE-COMPOSE-COMMANDE",
            "type": "composition",
            "source": "PRC-PRISE-COMMANDE",
            "cible": "OBJ-COMMANDE",
            "nom": "Compose",
        },
    }
    error, body = await _write(app, space, [ROLE, composition])
    assert error
    [problem] = body["errors"]
    assert problem["path"] == "$.documents[1].content.type"
    assert problem["code"] == "relation_not_allowed"
    assert {"Access", "Association"} <= set(problem["expected"])
    assert "Accès" in problem["message"] and "Association" in problem["message"]


async def test_version_perimee_rend_la_version_actuelle(app: FastAPI, space: ApiSpace) -> None:
    """Critère 4, et le remplacement d'un document existant."""
    current = await _read(app, space, PRC)
    assert current is not None
    content = {"cle": "PRC-PRISE-COMMANDE", "type": "processus-de-vente", "nom": "Prise"}
    error, body = await _write(app, space, [{"path": PRC, "version": "0" * 40, "content": content}])
    assert error
    [problem] = body["errors"]
    assert problem["path"] == "$.documents[0].version"
    assert problem["code"] == "carto_version_stale"
    assert problem["current_version"] == current["version"]
    missing = await _write(app, space, [{"path": PRC, "content": content}])
    assert missing[1]["errors"][0]["code"] == "carto_version_stale"
    ok, replaced = await _write(
        app, space, [{"path": PRC, "version": current["version"], "content": content}]
    )
    assert not ok and replaced["documents"][0]["created"] is False
    assert (await _read(app, space, PRC) or {})["name"] == "Prise"


async def test_modifier_par_jsonpath(app: FastAPI, space: ApiSpace) -> None:
    """Critère 5 : champ figé refusé, énumération expliquée ; modification valide."""
    version = (await _read(app, space, PRC) or {})["version"]
    error, body = await _call(
        app,
        "modify_carto_document",
        space,
        path=PRC,
        version=version,
        operations=[{"path": "$.type", "value": "role"}],
    )
    assert error and body["errors"][0]["path"] == "$.operations[0].path"
    assert body["errors"][0]["code"] == "carto_object_type_frozen"
    error, body = await _call(
        app,
        "modify_carto_document",
        space,
        path=PRC,
        version=version,
        operations=[{"path": "$.proprietes.canal", "value": "web"}],
    )
    assert error
    [problem] = body["errors"]
    assert problem["path"] == "$.proprietes.canal"
    assert problem["expected"]["enum"] == ["boutique", "internet"]
    assert '"boutique", "internet"' in problem["message"] and '"web"' in problem["message"]
    bad_path = await _call(
        app,
        "modify_carto_document",
        space,
        path=PRC,
        version=version,
        operations=[{"path": "proprietes.canal", "value": "web"}],
    )
    assert bad_path[1]["errors"][0]["code"] == "carto_jsonpath_invalid"
    error, body = await _call(
        app,
        "modify_carto_document",
        space,
        path=PRC,
        version=version,
        operations=[
            {"path": "$.proprietes.volume_jour", "value": 180},
            {"path": "$.nom", "value": "Prise de commande en ligne"},
        ],
    )
    assert not error, body
    after = await _read(app, space, PRC) or {}
    assert after["properties"] == {"volume_jour": 180}
    assert after["name"] == "Prise de commande en ligne"
    assert after["version"] == body["documents"][0]["version"] != version


async def test_supprimer_un_objet_utilise(app: FastAPI, space: ApiSpace) -> None:
    """Critère 6."""
    obj_version = (await _read(app, space, OBJ) or {})["version"]
    rel_version = (await _read(app, space, REL) or {})["version"]
    error, body = await _write(app, space, [{"path": OBJ, "version": obj_version, "delete": True}])
    assert error
    [problem] = body["errors"]
    assert problem["path"] == "$.documents[0].delete"
    assert problem["code"] == "carto_object_in_use"
    assert problem["relations"] == ["REL-PRISE-COMMANDE-ECRIT-COMMANDE"]
    error, body = await _write(
        app,
        space,
        [
            {"path": OBJ, "version": obj_version, "delete": True},
            {"path": REL, "version": rel_version, "delete": True},
        ],
    )
    assert not error, body
    assert {d["path"] for d in body["documents"] if d.get("deleted")} == {OBJ, REL}
    assert await _read(app, space, OBJ) is None


async def test_branche_et_cle_d_api(app: FastAPI, space: ApiSpace) -> None:
    """Critères 7 et 8."""
    error, body = await _call(
        app, "write_carto_documents", space, branch="main", message="x", documents=[ROLE]
    )
    assert error and body["error"] == "branch_unsupported"
    names = set(await tool_names(app, KEY))
    assert not names & set(WRITES)
    refused = await call_tool(
        app, KEY, "write_carto_documents", _ids(space, message="x", documents=[ROLE])
    )
    assert json.loads(refused.content[0].text)["error"] == "primitive_forbidden"


async def test_chemin_incoherent_et_champ_inconnu(app: FastAPI, space: ApiSpace) -> None:
    wrong = {**ROLE, "path": "cartographie/objets/role/autre.yaml"}
    error, body = await _write(app, space, [wrong])
    assert error and body["errors"][0]["code"] == "carto_path_mismatch"
    assert body["errors"][0]["expected"] == "cartographie/objets/role/rol-preparateur.yaml"
    extra = {**ROLE, "content": {**ROLE["content"], "couleur": "bleu"}}
    error, body = await _write(app, space, [extra])
    assert body["errors"][0]["path"] == "$.documents[0].content.couleur"
    assert uuid.UUID(space.space_id)  # l'espace reste celui du test
