"""Lecture de la cartographie par l'agent : types, objets, relations et modèles, par
les primitives communes `list_documents` et `read_document` (epic « Primitives MCP —
écriture commune par genre », ticket 2113e7bc). Les tests des lectures par domaine
qu'elles remplacent (tickets 41bf680e, be89037a, 5d0b3e7d) sont conservés, réécrits
sur ces deux primitives : clé d'API (vue publiée) et agent connecté (vue brouillon)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.mcp_surface.caller import McpCaller
from ossie_studio.mcp_surface.catalog import BY_ID
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


async def _call(
    app: FastAPI, token: str, tool: str, space: ApiSpace, **args: Any
) -> tuple[bool, dict[str, Any]]:
    result = await call_tool(
        app, token, tool, {"space_id": space.space_id, "branch_id": space.branch_id, **args}
    )
    if result.is_error:
        return True, json.loads(result.content[0].text)
    data: dict[str, Any] = result.structured_content
    return False, data


async def _ok(app: FastAPI, token: str, tool: str, space: ApiSpace, **args: Any) -> dict[str, Any]:
    error, data = await _call(app, token, tool, space, **args)
    assert not error, data
    return data


async def _refused(app: FastAPI, token: str, tool: str, space: ApiSpace, **args: Any) -> dict:
    error, data = await _call(app, token, tool, space, **args)
    assert error, data
    return data


async def _list(app: FastAPI, space: ApiSpace, kind: str, token: str = KEY, **args: Any) -> dict:
    return await _ok(app, token, "list_documents", space, document_type=kind, **args)


def _keys(listing: dict[str, Any]) -> list[str]:
    return [d["identity"]["key"] for d in listing["documents"]]


APP_PATH = "cartographie/objets/application/app-boutique.yaml"
APP_ID = {"type": "application", "key": "APP-BOUTIQUE"}
REL_ID = {"type": "acces", "key": "REL-PRISE-COMMANDE-ECRIT-COMMANDE"}
PRC_ID = {"type": "processus-de-vente", "key": "PRC-PRISE-COMMANDE"}


async def test_lectures_communes_offertes(app: FastAPI, space: ApiSpace) -> None:
    """Critère 6 : les lectures par domaine ont disparu du catalogue."""
    names = set(await tool_names(app, KEY))
    assert {"list_documents", "read_document"} <= names
    for old in (
        "list_carto_types", "list_carto_objects", "get_carto_object", "list_carto_models",
        "get_carto_model", "list_referentiels", "search_referentiel_values",
        "get_referentiel_value", "list_tags", "list_classifications", "list_certifications",
    ):  # fmt: skip
        assert old not in BY_ID and old not in names


async def test_lister_les_types(app: FastAPI, space: ApiSpace) -> None:
    """Critère 3 (ticket 2113e7bc) : objet ou relation, type ArchiMate ; le catalogue
    ArchiMate complet passe par describe_standard('carto-type')."""
    data = await _list(app, space, "carto_type")
    assert data["view"] == "published" and data["document_type"] == "carto_type"
    by_slug = {t["identity"]["slug"]: t for t in data["documents"]}
    application = by_slug["application"]
    assert (application["archimate"], application["nature"]) == ("ApplicationComponent", "object")
    assert application["layer"] == "application" and application["objects"] == 1
    assert application["name"] == "Application"
    assert application["path"] == "cartographie/types/application.schema.yaml"
    assert len(application["version"]) == 40
    assert (by_slug["acces"]["archimate"], by_slug["acces"]["nature"]) == ("Access", "relation")
    relations = await _list(app, space, "carto_type", filters={"nature": "relation"})
    assert [t["identity"]["slug"] for t in relations["documents"]] == ["acces"]
    business = await _list(app, space, "carto_type", filters={"archimate": "BusinessObject"})
    assert [t["identity"]["slug"] for t in business["documents"]] == ["document-metier"]
    described = await call_tool(app, KEY, "describe_standard", {"standard": "carto-type"})
    catalogue = {t["archimate"]: t for t in described.structured_content["archimate_catalogue"]}
    assert len(catalogue) == 72
    assert catalogue["BusinessInteraction"] == {
        "archimate": "BusinessInteraction",
        "label": "Interaction métier",
        "layer": "business",
        "nature": "object",
    }


async def test_lister_les_objets(app: FastAPI, space: ApiSpace) -> None:
    """Critère 1 : type, nom (recherche), chemin, version et identité ; pagination."""
    data = await _list(app, space, "carto_objet", filters={"archimate": "ApplicationComponent"})
    assert _keys(data) == ["APP-BOUTIQUE"] and data["total"] == 1
    [boutique] = data["documents"]
    assert boutique["identity"] == APP_ID and boutique["path"] == APP_PATH
    assert len(boutique["version"]) == 40 and boutique["name"] == "Boutique en ligne"
    kept = await _list(app, space, "carto_objet", identity={"type": "application"}, search="BOUT")
    assert _keys(kept) == ["APP-BOUTIQUE"]
    empty = await _list(app, space, "carto_objet", identity={"type": "application"}, search="xyz")
    assert (empty["documents"], empty["total"], empty["has_more"]) == ([], 0, False)
    accents = await _list(app, space, "carto_objet", search="ecrit")
    assert _keys(accents) == ["REL-PRISE-COMMANDE-ECRIT-COMMANDE"]
    relations = await _list(app, space, "carto_objet", filters={"nature": "relation"})
    [relation] = relations["documents"]
    assert relation["nature"] == "relation"
    assert relation["source"] == {
        "key": "PRC-PRISE-COMMANDE",
        "name": "Prise de commande",
        "type": "processus-de-vente",
        "exists": True,
        "path": "cartographie/objets/processus-de-vente/prc-prise-commande.yaml",
    }
    assert relation["target"]["key"] == "OBJ-COMMANDE"
    page = await _list(app, space, "carto_objet", filters={"nature": "object"}, limit=2)
    assert page["total"] == 3 and page["has_more"] is True and len(page["documents"]) == 2
    rest = await _list(app, space, "carto_objet", filters={"nature": "object"}, limit=2, offset=2)
    assert rest["has_more"] is False and len(rest["documents"]) == 1


async def test_relations_d_un_objet(app: FastAPI, space: ApiSpace) -> None:
    """Critère 2 : `source` rend les relations sortantes d'un objet, `cible` les
    entrantes (ce que rendait get_carto_object dans « relations »)."""
    outgoing = await _list(app, space, "carto_objet", filters={"source": "PRC-PRISE-COMMANDE"})
    assert _keys(outgoing) == ["REL-PRISE-COMMANDE-ECRIT-COMMANDE"]
    [relation] = outgoing["documents"]
    assert relation["target"] == {
        "key": "OBJ-COMMANDE",
        "name": "Commande",
        "type": "document-metier",
        "exists": True,
        "path": "cartographie/objets/document-metier/obj-commande.yaml",
    }
    assert relation["archimate"] == "Access"
    incoming = await _list(app, space, "carto_objet", filters={"cible": "OBJ-COMMANDE"})
    assert _keys(incoming) == ["REL-PRISE-COMMANDE-ECRIT-COMMANDE"]
    none = await _list(app, space, "carto_objet", filters={"source": "APP-BOUTIQUE"})
    assert none["documents"] == []


async def test_lire_un_objet(app: FastAPI, space: ApiSpace) -> None:
    """Ce que rendait get_carto_object : contenu par read_document, libellés des
    attributs ArchiMate par read_schema (x-labels)."""
    boutique = await _ok(
        app, KEY, "read_document", space, document_type="carto_objet", identity=APP_ID
    )
    assert boutique["content"]["proprietes"] == {"criticite": "haute"}
    assert boutique["content"]["documentation"] == "Site de vente."
    relation = await _ok(
        app, KEY, "read_document", space, document_type="carto_objet", identity=REL_ID
    )
    assert relation["content"]["source"] == "PRC-PRISE-COMMANDE"
    assert relation["content"]["proprietes"] == {"acces": "Write"}
    schema = await _ok(app, KEY, "read_schema", space, document_type="carto_objet", type="acces")
    acces = schema["schema"]["properties"]["proprietes"]["properties"]["acces"]
    assert acces["x-labels"]["Write"] == "écriture"
    missing = await _refused(
        app, KEY, "read_document", space, document_type="carto_objet",
        identity={"type": "application", "key": "ABSENT"},
    )  # fmt: skip
    assert missing["error"] == "document_not_found"


async def test_vue_brouillon_et_vue_publiee(app: FastAPI, space: ApiSpace) -> None:
    """Critère 5 : un objet présent seulement dans le brouillon est absent sans
    `branch`, présent avec `branch: draft` et marqué unpublished ; la clé d'API ne
    lit jamais le brouillon."""
    await draft_write(
        app,
        space,
        "cartographie/objets/application/app-crm.yaml",
        {"cle": "APP-CRM", "type": "application", "nom": "CRM"},
    )
    by_type = {"type": "application"}
    draft = await _list(app, space, "carto_objet", AGENT, identity=by_type, branch="draft")
    assert draft["view"] == "draft"
    crm = next(o for o in draft["documents"] if o["identity"]["key"] == "APP-CRM")
    assert crm["unpublished"] is True
    boutique = next(o for o in draft["documents"] if o["identity"]["key"] == "APP-BOUTIQUE")
    assert "unpublished" not in boutique
    published = await _list(app, space, "carto_objet", AGENT, identity=by_type)
    assert _keys(published) == ["APP-BOUTIQUE"]
    refused = await _refused(
        app, KEY, "list_documents", space, document_type="carto_type", branch="draft"
    )
    assert refused["error"] == "draft_requires_person"
    other = await _refused(
        app, AGENT, "list_documents", space, document_type="carto_type", branch="main"
    )
    assert other["error"] == "branch_unsupported"


async def test_genre_ou_filtre_inconnu(app: FastAPI, space: ApiSpace) -> None:
    """Critère 7 : erreur explicite, avec ce qui est possible et `aide`."""
    unknown = await _refused(app, KEY, "list_documents", space, document_type="inconnu")
    assert unknown["error"] == "document_type_unknown"
    assert unknown["aide"] == {"primitive": "list_document_types"}
    stray = await _refused(
        app, KEY, "list_documents", space, document_type="carto_type", filters={"source": "X"}
    )
    assert stray["error"] == "list_filter_unknown" and stray["path"] == "$.filters.source"
    assert stray["expected"] == ["nature", "archimate"]
    assert stray["aide"] == {"primitive": "list_document_types"}
    identity = await _refused(
        app, KEY, "list_documents", space, document_type="carto_objet", identity={"cle": "X"}
    )
    assert identity["error"] == "list_filter_unknown" and identity["path"] == "$.identity.cle"
    bad = await _refused(
        app, KEY, "list_documents", space, document_type="carto_objet", filters={"nature": "autre"}
    )
    assert bad["error"] == "list_filter_invalid" and bad["expected"] == ["object", "relation"]


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


async def test_lire_un_modele_sans_le_dessin(app: FastAPI, with_model: ApiSpace) -> None:
    """Ce que rendait get_carto_model : le contenu du modèle (objets et relations
    retenus, vues, nœuds, connexions) par read_document, les noms par list_documents ;
    le dessin est un autre document (carto_graphique)."""
    identity = {"slug": "boutique-applicatif"}
    data = await _ok(
        app, KEY, "read_document", with_model, document_type="carto_modele", identity=identity
    )
    model = data["content"]
    assert data["path"] == "cartographie/modeles/boutique-applicatif.modele.yaml"
    assert len(data["version"]) == 40
    assert model["nom"] == "Boutique — vue applicative"
    assert model["elements"] == ["APP-BOUTIQUE", "PRC-PRISE-COMMANDE", "OBJ-COMMANDE"]
    assert model["relationships"] == ["REL-PRISE-COMMANDE-ECRIT-COMMANDE"]
    assert model["regles"] == "cartographie/regles/standard.rules.yaml"
    assert model["graphique"] == "cartographie/modeles/boutique-applicatif.graphique.yaml"
    [view] = model["vues"]
    assert (view["nom"], view["point_de_vue"]) == ("Vue principale", "layered")
    assert view["connexions"] == [
        {"id": "C1", "relationship": "REL-PRISE-COMMANDE-ECRIT-COMMANDE", "de": "N2", "vers": "N3"}
    ]
    assert '"x"' not in json.dumps(model)
    names = await _list(app, with_model, "carto_objet")
    by_key = {d["identity"]["key"]: d["name"] for d in names["documents"]}
    assert [by_key[k] for k in model["elements"]] == [
        "Boutique en ligne",
        "Prise de commande",
        "Commande",
    ]
    graphic = await _ok(
        app, KEY, "read_document", with_model, document_type="carto_graphique", identity=identity
    )
    assert graphic["content"]["vues"]["VUE-1"]["noeuds"]["N1"]["x"] == 40
    missing = await _refused(
        app,
        KEY,
        "read_document",
        with_model,
        document_type="carto_modele",
        identity={"slug": "absent"},
    )
    assert missing["error"] == "document_not_found"


async def test_ou_apparait_un_objet(app: FastAPI, with_model: ApiSpace) -> None:
    """Ce que rendaient list_carto_models et get_carto_object (« models ») : les
    modèles, leurs décomptes et vues, et ceux qui retiennent un objet (`contains`)."""
    listing = await _list(app, with_model, "carto_modele")
    [model] = listing["documents"]
    assert model["identity"] == {"slug": "boutique-applicatif"}
    assert (model["name"], model["objects"], model["relations"]) == (
        "Boutique — vue applicative",
        3,
        1,
    )
    assert model["views"] == [{"id": "VUE-1", "name": "Vue principale", "viewpoint": "layered"}]
    assert model["problems"] == [] and model["readable"] is True
    assert model["rules"] == "cartographie/regles/standard.rules.yaml"
    assert model["path"] == "cartographie/modeles/boutique-applicatif.modele.yaml"
    kept = await _list(app, with_model, "carto_modele", filters={"contains": "APP-BOUTIQUE"})
    assert [m["identity"]["slug"] for m in kept["documents"]] == ["boutique-applicatif"]
    forge_edit(
        with_model,
        {
            "cartographie/objets/document-metier/obj-facture.yaml": (
                "cle: OBJ-FACTURE\ntype: document-metier\nnom: Facture\n"
            )
        },
    )
    alone = await _list(app, with_model, "carto_modele", filters={"contains": "OBJ-FACTURE"})
    assert alone["documents"] == []
    rules = await _list(app, with_model, "carto_regles")
    assert [r["identity"] for r in rules["documents"]] == [{"slug": "standard"}]
    graphics = await _list(app, with_model, "carto_graphique")
    assert [g["path"] for g in graphics["documents"]] == [
        "cartographie/modeles/boutique-applicatif.graphique.yaml"
    ]


async def test_version_suit_le_contenu(app: FastAPI, space: ApiSpace) -> None:
    """La version d'un document (empreinte Git du fichier) est la même dans la liste
    et à la lecture, et change avec le contenu."""
    listed = await _list(app, space, "carto_objet", identity=APP_ID)
    read = await _ok(app, KEY, "read_document", space, document_type="carto_objet", identity=APP_ID)
    assert listed["documents"][0]["version"] == read["version"]
    forge_edit(space, {APP_PATH: "cle: APP-BOUTIQUE\ntype: application\nnom: Boutique\n"})
    changed = await _ok(
        app, KEY, "read_document", space, document_type="carto_objet", identity=APP_ID
    )
    assert changed["version"] != read["version"]
    wrong_type = await _refused(
        app, KEY, "read_document", space, document_type="carto_objet",
        identity={"type": "processus-de-vente", "key": "APP-BOUTIQUE"},
    )  # fmt: skip
    assert wrong_type["error"] == "document_not_found"


async def test_objet_du_brouillon_selon_branch(app: FastAPI, space: ApiSpace) -> None:
    """Absent sans branch, présent et marqué unpublished avec branch=draft ; une autre
    branche est refusée."""
    crm = {"type": "application", "key": "APP-CRM"}
    crm_path = "cartographie/objets/application/app-crm.yaml"
    await draft_write(app, space, crm_path, {"cle": "APP-CRM", "type": "application", "nom": "CRM"})
    published = await _refused(
        app, AGENT, "read_document", space, document_type="carto_objet", identity=crm
    )
    assert published["error"] == "document_not_found"
    draft = await _ok(
        app,
        AGENT,
        "read_document",
        space,
        document_type="carto_objet",
        identity=crm,
        branch="draft",
    )
    assert draft["unpublished"] is True and draft["view"] == "draft" and draft["version"]
    other = await _refused(
        app, AGENT, "read_document", space, document_type="carto_objet", identity=crm, branch="main"
    )
    assert other["error"] == "branch_unsupported"
