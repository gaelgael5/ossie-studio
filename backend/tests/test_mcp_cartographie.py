"""Primitives MCP de lecture de la cartographie (ticket 41bf680e) : lister les
types, lister les objets, lire un objet par sa clé ; par clé d'API (vue publiée)
et par agent connecté (vue brouillon)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.mcp_surface.caller import McpCaller
from tests.mcp_helpers import call_tool, tool_names
from tests.space_fixture import ApiSpace, draft_write, forge_edit, make_api_space

KEY = "carto-key"
AGENT = "carto-agent"

TYPES = {
    "application": ("Application", "ApplicationComponent"),
    "processus-de-vente": ("Processus de vente", "BusinessProcess"),
    "document-metier": ("Document métier", "BusinessObject"),
    "acces": ("Accès", "Access"),
}


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    api_space = await make_api_space(client, app, tmp_path)
    files: dict[str, str | None] = {
        f"cartographie/types/{slug}.schema.yaml": (
            f"title: {title}\nx-archimate: {arch}\ntype: object\n"
        )
        for slug, (title, arch) in TYPES.items()
    }
    files["cartographie/types/application.schema.yaml"] = (
        "title: Application\nx-archimate: ApplicationComponent\ntype: object\n"
        "properties:\n  criticite:\n    type: string\n"
    )
    files.update(
        {
            "cartographie/objets/application/app-boutique.yaml": (
                "cle: APP-BOUTIQUE\ntype: application\nnom: Boutique en ligne\n"
                "documentation: Site de vente.\nproprietes:\n  criticite: haute\n"
            ),
            "cartographie/objets/processus-de-vente/prc-prise-commande.yaml": (
                "cle: PRC-PRISE-COMMANDE\ntype: processus-de-vente\nnom: Prise de commande\n"
            ),
            "cartographie/objets/document-metier/obj-commande.yaml": (
                "cle: OBJ-COMMANDE\ntype: document-metier\nnom: Commande\n"
            ),
            "cartographie/objets/acces/rel-prise-commande-ecrit-commande.yaml": (
                "cle: REL-PRISE-COMMANDE-ECRIT-COMMANDE\ntype: acces\n"
                "source: PRC-PRISE-COMMANDE\ncible: OBJ-COMMANDE\nnom: Écrit la commande\n"
                "proprietes:\n  acces: Write\n"
            ),
        }
    )
    forge_edit(api_space, files)
    async with app.state.pool.acquire() as conn:
        lecture = await conn.fetchval("SELECT id FROM agent_profiles WHERE slug = 'lecture'")
        admin_id = await conn.fetchval("SELECT id FROM users WHERE is_bootstrap")
    callers = {
        KEY: McpCaller(kind="key", ref="k", agent_profile_id=lecture),
        AGENT: McpCaller(
            kind="oauth", ref="c", agent_profile_id=lecture, user_id=admin_id, client_id="claude"
        ),
    }

    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return callers.get(token)

    app.state.mcp_resolvers.append(resolver)
    return api_space


async def _ok(app: FastAPI, token: str, tool: str, space: ApiSpace, **args: Any) -> dict[str, Any]:
    result = await call_tool(
        app, token, tool, {"space_id": space.space_id, "branch_id": space.branch_id, **args}
    )
    assert not result.is_error, result.content[0].text
    data: dict[str, Any] = result.structured_content
    return data


async def test_trois_primitives_offertes(app: FastAPI, space: ApiSpace) -> None:
    names = set(await tool_names(app, KEY))
    assert {"list_carto_types", "list_carto_objects", "get_carto_object"} <= names


async def test_lister_les_types(app: FastAPI, space: ApiSpace) -> None:
    """Critère 1."""
    data = await _ok(app, KEY, "list_carto_types", space)
    assert data["view"] == "published" and "catalogue" not in data
    by_slug = {t["slug"]: t for t in data["types"]}
    assert (by_slug["application"]["archimate"], by_slug["application"]["nature"]) == (
        "ApplicationComponent",
        "object",
    )
    assert by_slug["application"]["layer"] == "application"
    assert by_slug["application"]["objects"] == 1
    assert (by_slug["acces"]["archimate"], by_slug["acces"]["nature"]) == ("Access", "relation")
    full = await _ok(app, KEY, "list_carto_types", space, catalogue=True)
    catalogue = {t["archimate"]: t for t in full["catalogue"]}
    assert len(catalogue) == 72
    assert catalogue["BusinessInteraction"] == {
        "archimate": "BusinessInteraction",
        "label": "Interaction métier",
        "layer": "business",
        "nature": "object",
    }


async def test_lister_les_objets(app: FastAPI, space: ApiSpace) -> None:
    """Critères 2 et 3."""
    data = await _ok(app, KEY, "list_carto_objects", space, type="ApplicationComponent")
    assert [o["key"] for o in data["objects"]] == ["APP-BOUTIQUE"] and data["total"] == 1
    kept = await _ok(app, KEY, "list_carto_objects", space, type="application", name="BOUT")
    assert [o["key"] for o in kept["objects"]] == ["APP-BOUTIQUE"]
    empty = await _ok(
        app, KEY, "list_carto_objects", space, type="ApplicationComponent", name="xyz"
    )
    assert (empty["objects"], empty["total"], empty["has_more"]) == ([], 0, False)
    accents = await _ok(app, KEY, "list_carto_objects", space, name="ecrit")
    assert [o["key"] for o in accents["objects"]] == ["REL-PRISE-COMMANDE-ECRIT-COMMANDE"]
    relations = await _ok(app, KEY, "list_carto_objects", space, nature="relation")
    [relation] = relations["objects"]
    assert relation["nature"] == "relation"
    assert relation["source"] == {
        "key": "PRC-PRISE-COMMANDE",
        "name": "Prise de commande",
        "type": "processus-de-vente",
        "exists": True,
    }
    assert relation["target"]["key"] == "OBJ-COMMANDE"
    page = await _ok(app, KEY, "list_carto_objects", space, nature="object", limit=2)
    assert page["total"] == 3 and page["has_more"] is True and len(page["objects"]) == 2
    rest = await _ok(app, KEY, "list_carto_objects", space, nature="object", limit=2, offset=2)
    assert rest["has_more"] is False and len(rest["objects"]) == 1
    unknown = await call_tool(
        app, KEY, "list_carto_objects",
        {"space_id": space.space_id, "branch_id": space.branch_id, "type": "Inconnu"},
    )  # fmt: skip
    assert unknown.is_error
    assert json.loads(unknown.content[0].text)["error"] == "carto_type_not_found"


async def test_lire_un_objet(app: FastAPI, space: ApiSpace) -> None:
    """Critère 4."""
    app_data = await _ok(app, KEY, "get_carto_object", space, key="APP-BOUTIQUE")
    assert app_data["properties"] == {"criticite": "haute"}
    assert app_data["documentation"] == "Site de vente."
    assert app_data["relations"] == []
    relation = await _ok(
        app, KEY, "get_carto_object", space, key="REL-PRISE-COMMANDE-ECRIT-COMMANDE"
    )
    assert relation["nature"] == "relation"
    assert relation["source"]["key"] == "PRC-PRISE-COMMANDE"
    assert relation["target"]["name"] == "Commande"
    assert relation["properties"] == {"acces": "Write"}
    assert relation["property_labels"] == {"acces": "écriture"}
    source = await _ok(app, KEY, "get_carto_object", space, key="PRC-PRISE-COMMANDE")
    assert source["relations"] == [
        {
            "key": "REL-PRISE-COMMANDE-ECRIT-COMMANDE",
            "name": "Écrit la commande",
            "type": "acces",
            "archimate": "Access",
            "direction": "outgoing",
            "other": {"key": "OBJ-COMMANDE", "name": "Commande", "type": "document-metier",
                      "exists": True},
        }
    ]  # fmt: skip
    missing = await call_tool(
        app, KEY, "get_carto_object",
        {"space_id": space.space_id, "branch_id": space.branch_id, "key": "ABSENT"},
    )  # fmt: skip
    assert missing.is_error
    error = json.loads(missing.content[0].text)
    assert error["error"] == "carto_object_not_found" and error["key"] == "ABSENT"


async def test_vue_brouillon_et_vue_publiee(app: FastAPI, space: ApiSpace) -> None:
    """Critère 5 : l'agent connecté voit le brouillon de la personne ; la clé d'API,
    la version publiée seulement."""
    await draft_write(
        app,
        space,
        "cartographie/objets/application/app-crm.yaml",
        {"cle": "APP-CRM", "type": "application", "nom": "CRM"},
    )
    draft = await _ok(app, AGENT, "list_carto_objects", space, type="application", branch="draft")
    assert draft["view"] == "draft"
    crm = next(o for o in draft["objects"] if o["key"] == "APP-CRM")
    assert crm["unpublished"] is True
    published = await _ok(app, KEY, "list_carto_objects", space, type="application")
    assert [o["key"] for o in published["objects"]] == ["APP-BOUTIQUE"]
    refused = await call_tool(
        app, KEY, "list_carto_types",
        {"space_id": space.space_id, "branch_id": space.branch_id, "view": "draft"},
    )  # fmt: skip
    assert refused.is_error


# --- Modèles et vues (ticket be89037a) -----------------------------------------------------

MODEL = """\
id: MOD-BOUTIQUE-APPLICATIF
nom: Boutique — vue applicative
description: Ce que la boutique expose et ce qu'elle manipule.
regles: cartographie/regles/standard.rules.yaml
graphique: cartographie/modeles/boutique-applicatif.graphique.yaml
documents:
- cartographie/objets/application/app-boutique.yaml
- cartographie/objets/processus-de-vente/prc-prise-commande.yaml
- cartographie/objets/document-metier/obj-commande.yaml
- cartographie/objets/acces/rel-prise-commande-ecrit-commande.yaml
elements: [APP-BOUTIQUE, PRC-PRISE-COMMANDE, OBJ-COMMANDE]
relationships: [REL-PRISE-COMMANDE-ECRIT-COMMANDE]
vues:
- id: VUE-1
  nom: Vue principale
  point_de_vue: layered
  noeuds:
  - id: N1
    element: APP-BOUTIQUE
    enfants:
    - {id: N2, element: PRC-PRISE-COMMANDE}
  - {id: N3, element: OBJ-COMMANDE}
  - {id: N4, note: Atelier}
  connexions:
  - {id: C1, relationship: REL-PRISE-COMMANDE-ECRIT-COMMANDE, de: N2, vers: N3}
"""


@pytest.fixture
def with_model(space: ApiSpace) -> ApiSpace:
    forge_edit(
        space,
        {
            "cartographie/regles/standard.rules.yaml": "lang: fr\n",
            "cartographie/modeles/boutique-applicatif.modele.yaml": MODEL,
            "cartographie/modeles/boutique-applicatif.graphique.yaml": (
                "vues:\n  VUE-1:\n    noeuds:\n      N1: {x: 40, y: 60, l: 320, h: 180}\n"
            ),
        },
    )
    return space


async def test_primitives_des_modeles_offertes(app: FastAPI, with_model: ApiSpace) -> None:
    names = set(await tool_names(app, KEY))
    assert {"list_carto_models", "get_carto_model"} <= names


async def test_lire_un_modele_sans_le_dessin(app: FastAPI, with_model: ApiSpace) -> None:
    """Critères 1 et 3."""
    data = await _ok(app, KEY, "get_carto_model", with_model, slug="boutique-applicatif")
    assert data["name"] == "Boutique — vue applicative" and data["problems"] == []
    assert [o["key"] for o in data["objects"]] == [
        "APP-BOUTIQUE",
        "PRC-PRISE-COMMANDE",
        "OBJ-COMMANDE",
    ]
    assert data["objects"][0] == {
        "key": "APP-BOUTIQUE",
        "name": "Boutique en ligne",
        "type": "application",
        "archimate": "ApplicationComponent",
        "exists": True,
    }
    [relation] = data["relations"]
    assert (relation["key"], relation["source"], relation["target"]) == (
        "REL-PRISE-COMMANDE-ECRIT-COMMANDE",
        "PRC-PRISE-COMMANDE",
        "OBJ-COMMANDE",
    )
    [view] = data["views"]
    assert (view["name"], view["viewpoint"]) == ("Vue principale", "layered")
    assert view["nodes"] == [
        {
            "id": "N1",
            "element": "APP-BOUTIQUE",
            "name": "Boutique en ligne",
            "children": [
                {"id": "N2", "element": "PRC-PRISE-COMMANDE", "name": "Prise de commande"}
            ],
        },
        {"id": "N3", "element": "OBJ-COMMANDE", "name": "Commande"},
        {"id": "N4", "note": "Atelier"},
    ]
    assert view["connections"] == [
        {"id": "C1", "relationship": "REL-PRISE-COMMANDE-ECRIT-COMMANDE", "from": "N2", "to": "N3"}
    ]
    # Critère 3 : aucune coordonnée.
    text = json.dumps(data)
    assert '"x"' not in text and "graphique" not in json.dumps(
        {k: v for k, v in data.items() if k != "path"}
    )
    missing = await call_tool(
        app, KEY, "get_carto_model",
        {"space_id": with_model.space_id, "branch_id": with_model.branch_id, "slug": "absent"},
    )  # fmt: skip
    assert missing.is_error
    assert json.loads(missing.content[0].text)["error"] == "carto_model_not_found"


async def test_ou_apparait_un_objet(app: FastAPI, with_model: ApiSpace) -> None:
    """Critère 2."""
    listing = await _ok(app, KEY, "list_carto_models", with_model)
    [model] = listing["models"]
    assert (model["slug"], model["objects"], model["relations"]) == ("boutique-applicatif", 3, 1)
    assert model["views"] == [{"id": "VUE-1", "name": "Vue principale", "viewpoint": "layered"}]
    kept = await _ok(app, KEY, "list_carto_models", with_model, contains="APP-BOUTIQUE")
    assert [m["slug"] for m in kept["models"]] == ["boutique-applicatif"]
    detail = await _ok(app, KEY, "get_carto_object", with_model, key="APP-BOUTIQUE")
    assert detail["models"] == [
        {"slug": "boutique-applicatif", "name": "Boutique — vue applicative"}
    ]
    forge_edit(
        with_model,
        {
            "cartographie/objets/document-metier/obj-facture.yaml": (
                "cle: OBJ-FACTURE\ntype: document-metier\nnom: Facture\n"
            )
        },
    )
    alone = await _ok(app, KEY, "list_carto_models", with_model, contains="OBJ-FACTURE")
    assert alone["models"] == []
    unknown = await call_tool(
        app, KEY, "list_carto_models",
        {"space_id": with_model.space_id, "branch_id": with_model.branch_id, "contains": "ABSENT"},
    )  # fmt: skip
    assert json.loads(unknown.content[0].text)["error"] == "carto_object_not_found"
