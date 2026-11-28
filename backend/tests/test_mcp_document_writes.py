"""Primitives MCP communes d'écriture : `write_documents` et `patch_document` (epic
« Primitives MCP — écriture commune par genre », ticket 10cc4ed6). Évolution des
primitives de la cartographie du ticket d4723be3 : leurs tests sont conservés,
adaptés à l'adressage par genre et identité, et complétés pour les référentiels, le
BPMN et la gouvernance. Mêmes contrôles que l'écran, erreurs localisées dans la
requête, rien d'écrit au moindre refus."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.cartographie.process import new_process_xml, with_detail
from ossie_studio.cartographie.service import CartographieTarget
from ossie_studio.mcp_surface.caller import McpCaller
from ossie_studio.spaces.service import db_get_space
from tests.conftest import create_plain_user
from tests.mcp_helpers import call_tool, tool_names
from tests.space_fixture import ApiSpace, admin_workdir, forge_edit, make_api_space

KEY = "doc-writes-key"
AGENT = "doc-writes-agent"
LIMITED = "doc-writes-limited"
WRITES = ["write_documents", "patch_document"]

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
PRC_ID = {"type": "processus-de-vente", "key": "PRC-PRISE-COMMANDE"}
OBJ_ID = {"type": "document-metier", "key": "OBJ-COMMANDE"}
REL_ID = {"type": "acces", "key": "REL-PRISE-COMMANDE-ECRIT-COMMANDE"}


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
            "cartographie/objets/document-metier/obj-facture.yaml": (
                "cle: OBJ-FACTURE\ntype: document-metier\nnom: Facture\n"
            ),
            REL: (
                "cle: REL-PRISE-COMMANDE-ECRIT-COMMANDE\ntype: acces\n"
                "source: PRC-PRISE-COMMANDE\ncible: OBJ-COMMANDE\nnom: Écrit\n"
            ),
        }
    )
    forge_edit(api_space, files)
    await create_plain_user(app, "lea@example.org", "motdepasse-solide")
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives, all_read) VALUES ('dw', $1, true) "
            "RETURNING id",
            WRITES,
        )
        admin_id = await conn.fetchval("SELECT id FROM users WHERE is_bootstrap")
        lea_id = await conn.fetchval("SELECT id FROM users WHERE email = 'lea@example.org'")
        carto_only = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) VALUES ('carto-seule', "
            "'{spaces:read,cartographie:read,cartographie:write}') RETURNING id"
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


def _ids(space: ApiSpace, **extra: Any) -> dict[str, Any]:
    return {"space_id": space.space_id, "branch_id": space.branch_id, **extra}


async def _call(
    app: FastAPI, tool: str, space: ApiSpace, token: str = AGENT, **args: Any
) -> tuple[bool, dict[str, Any]]:
    result = await call_tool(app, token, tool, _ids(space, **args))
    body = result.structured_content if not result.is_error else json.loads(result.content[0].text)
    return result.is_error, body


async def _write(
    app: FastAPI, space: ApiSpace, documents: list[dict[str, Any]], token: str = AGENT
) -> tuple[bool, dict[str, Any]]:
    return await _call(
        app, "write_documents", space, token, message="Atelier ventes", documents=documents
    )


async def _patch(
    app: FastAPI, space: ApiSpace, kind: str, identity: dict[str, Any], version: str, ops: list
) -> tuple[bool, dict[str, Any]]:
    return await _call(
        app,
        "patch_document",
        space,
        document_type=kind,
        identity=identity,
        version=version,
        operations=ops,
    )


async def _read(app: FastAPI, space: ApiSpace, path: str) -> dict[str, Any] | None:
    """Un objet de cartographie tel que le brouillon le contient, par son fichier : sa
    ligne de list_documents et son contenu (read_document) ; None s'il n'y est pas."""
    error, listed = await _call(
        app, "list_documents", space, document_type="carto_objet", branch="draft", limit=200
    )
    assert not error, listed
    found = next((d for d in listed["documents"] if d["path"] == path), None)
    if found is None:
        return None
    error, doc = await _call(
        app,
        "read_document",
        space,
        document_type="carto_objet",
        identity=found["identity"],
        branch="draft",
    )
    assert not error, doc
    return {**found, "properties": doc["content"].get("proprietes", {})}


async def _draft(app: FastAPI, space: ApiSpace, path: str) -> bytes | None:
    """Un fichier tel que le brouillon de la personne (l'admin, pour qui l'agent écrit)
    le contient ; None s'il n'y est pas."""
    workdir = await admin_workdir(app, space)
    async with app.state.pool.acquire() as conn:
        git_space = await db_get_space(conn, uuid.UUID(space.space_id))
    assert git_space is not None
    target = CartographieTarget(workdir, git_space, "work/principale", "x", None, "a@a")

    def read(root: Path) -> bytes | None:
        file = root / path
        return file.read_bytes() if file.is_file() else None

    return await target.read(read)


def _obj(type_slug: str, key: str, content: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "document_type": "carto_objet",
        "identity": {"type": type_slug, "key": key},
        "content": content,
        **extra,
    }


ROLE = _obj(
    "role", "ROL-PREPARATEUR", {"cle": "ROL-PREPARATEUR", "type": "role", "nom": "Préparateur"}
)
ASSIGN = _obj(
    "affectation",
    "REL-PREPARATEUR-PRISE-COMMANDE",
    {
        "cle": "REL-PREPARATEUR-PRISE-COMMANDE",
        "type": "affectation",
        "source": "ROL-PREPARATEUR",
        "cible": "PRC-PRISE-COMMANDE",
        "nom": "Prépare",
    },
)


# --- Cartographie (repris de d4723be3, adressage par genre) ------------------------------


async def test_role_et_affectation_dans_la_meme_liste(app: FastAPI, space: ApiSpace) -> None:
    """Critère 1 : l'affectation, listée avant le rôle qu'elle vise, passe ; un seul
    enregistrement ; emplacements calculés sans chemin fourni ; rien de publié."""
    error, body = await _write(app, space, [ASSIGN, ROLE])
    assert not error, body
    assert body["written"] is True and body["revision"].startswith("draft:")
    paths = {d["path"]: d for d in body["documents"]}
    assert set(paths) == {
        "cartographie/objets/role/rol-preparateur.yaml",
        "cartographie/objets/affectation/rel-preparateur-prise-commande.yaml",
    }
    assert all(d["action"] == "created" and len(d["version"]) == 40 for d in paths.values())
    role = await _read(app, space, "cartographie/objets/role/rol-preparateur.yaml")
    assert role is not None and role["unpublished"] is True
    role_id = {"type": "role", "key": "ROL-PREPARATEUR"}
    error, published = await _call(
        app, "read_document", space, document_type="carto_objet", identity=role_id
    )
    assert error and published["error"] == "document_not_found"


async def test_un_document_invalide_refuse_toute_la_liste(app: FastAPI, space: ApiSpace) -> None:
    """Critère 2 : rien n'est écrit ; l'erreur nomme le JSONPath dans la requête et
    renvoie vers le standard."""
    bad = _obj(
        "processus-de-vente",
        "PRC-LIVRAISON",
        {
            "cle": "PRC-LIVRAISON",
            "type": "processus-de-vente",
            "nom": "Livraison",
            "proprietes": {"volume_jour": -5},
        },
    )
    error, body = await _write(app, space, [ROLE, ASSIGN, bad])
    assert error and body["error"] == "write_refused" and body["written"] is False
    [problem] = body["errors"]
    assert problem["path"] == "$.documents[2].content.proprietes.volume_jour"
    assert problem["code"] == "carto_object_invalid"
    assert problem["received"] == -5 and problem["expected"] == {"type": "integer", "minimum": 0}
    assert "entier supérieur ou égal à 0" in problem["message"] and "-5" in problem["message"]
    assert problem["aide"] == {"primitive": "describe_standard", "standard": "carto-objet"}
    assert await _draft(app, space, "cartographie/objets/role/rol-preparateur.yaml") is None


async def test_relation_non_permise_cite_les_permises(app: FastAPI, space: ApiSpace) -> None:
    """Critère 3."""
    composition = _obj(
        "composition",
        "REL-PRISE-COMMANDE-COMPOSE-COMMANDE",
        {
            "cle": "REL-PRISE-COMMANDE-COMPOSE-COMMANDE",
            "type": "composition",
            "source": "PRC-PRISE-COMMANDE",
            "cible": "OBJ-COMMANDE",
            "nom": "Compose",
        },
    )
    error, body = await _write(app, space, [ROLE, composition])
    assert error
    [problem] = body["errors"]
    assert problem["path"] == "$.documents[1].content.type"
    assert problem["code"] == "relation_not_allowed"
    assert {"Access", "Association"} <= set(problem["expected"])


async def test_creer_l_existant_ou_version_perimee(app: FastAPI, space: ApiSpace) -> None:
    """Critère 4 : refusés, la version actuelle est rendue ; remplacement valide."""
    current = await _read(app, space, PRC)
    assert current is not None
    content = {"cle": "PRC-PRISE-COMMANDE", "type": "processus-de-vente", "nom": "Prise"}
    entry = {"document_type": "carto_objet", "identity": PRC_ID, "content": content}
    error, body = await _write(app, space, [{**entry, "version": "0" * 40}])
    assert error
    [problem] = body["errors"]
    assert (problem["path"], problem["code"]) == ("$.documents[0].version", "write_version_stale")
    assert problem["current_version"] == current["version"]
    error, body = await _write(app, space, [entry])
    assert body["errors"][0]["code"] == "write_exists"
    assert body["errors"][0]["current_version"] == current["version"]
    error, replaced = await _write(app, space, [{**entry, "version": current["version"]}])
    assert not error and replaced["documents"][0]["action"] == "replaced"
    assert (await _read(app, space, PRC) or {})["name"] == "Prise"


async def test_supprimer_un_objet_utilise(app: FastAPI, space: ApiSpace) -> None:
    """Critère 5."""
    obj_version = (await _read(app, space, OBJ) or {})["version"]
    rel_version = (await _read(app, space, REL) or {})["version"]
    delete_obj = {
        "document_type": "carto_objet",
        "identity": OBJ_ID,
        "version": obj_version,
        "delete": True,
    }
    error, body = await _write(app, space, [delete_obj])
    assert error
    [problem] = body["errors"]
    assert problem["path"] == "$.documents[0].delete"
    assert problem["code"] == "carto_object_in_use"
    assert problem["relations"] == ["REL-PRISE-COMMANDE-ECRIT-COMMANDE"]
    delete_rel = {
        "document_type": "carto_objet",
        "identity": REL_ID,
        "version": rel_version,
        "delete": True,
    }
    error, body = await _write(app, space, [delete_obj, delete_rel])
    assert not error, body
    assert {d["path"] for d in body["documents"] if d["action"] == "deleted"} == {OBJ, REL}
    assert await _read(app, space, OBJ) is None


async def test_patch_jsonpath(app: FastAPI, space: ApiSpace) -> None:
    """Critère 6 : champ figé refusé, énumération expliquée ; modification valide."""
    version = (await _read(app, space, PRC) or {})["version"]
    error, body = await _patch(
        app,
        space,
        "carto_objet",
        PRC_ID,
        version,
        [{"op": "set", "path": "$.type", "value": "role"}],
    )
    assert error and body["errors"][0]["path"] == "$.operations[0].path"
    assert body["errors"][0]["code"] == "write_identity_frozen"
    assert "supprimer l'ancien dans le même lot" in body["errors"][0]["message"]
    error, body = await _patch(
        app,
        space,
        "carto_objet",
        PRC_ID,
        version,
        [{"op": "add", "path": "$.proprietes", "value": {"canal": "web"}}],
    )
    assert error
    [problem] = body["errors"]
    assert problem["path"] == "$.proprietes.canal"
    assert problem["expected"]["enum"] == ["boutique", "internet"]
    assert '"boutique", "internet"' in problem["message"] and '"web"' in problem["message"]
    bad = await _patch(
        app, space, "carto_objet", PRC_ID, version, [{"op": "set", "path": "nom", "value": "x"}]
    )
    assert bad[1]["errors"][0]["code"] == "write_jsonpath_invalid"
    assert bad[1]["errors"][0]["aide"] == {"primitive": "describe_standard", "standard": "jsonpath"}
    error, body = await _patch(
        app,
        space,
        "carto_objet",
        PRC_ID,
        version,
        [
            {"op": "add", "path": "$.proprietes", "value": {"volume_jour": 180}},
            {"op": "set", "path": "$.nom", "value": "Prise de commande en ligne"},
        ],
    )
    assert not error, body
    after = await _read(app, space, PRC) or {}
    assert after["properties"] == {"volume_jour": 180}
    assert after["name"] == "Prise de commande en ligne"
    assert after["version"] == body["document"]["version"] != version


async def test_jsonpath_zero_ou_plusieurs_noeuds(app: FastAPI, space: ApiSpace) -> None:
    """Critère 9 (JSONPath)."""
    version = (await _read(app, space, PRC) or {})["version"]
    for path, op in (("$.proprietes.absent", "set"), ("$.nom.x", "set"), ("$.*", "set")):
        error, body = await _patch(
            app, space, "carto_objet", PRC_ID, version, [{"op": op, "path": path, "value": 1}]
        )
        assert error
        assert body["errors"][0]["code"] in ("write_path_not_single", "write_jsonpath_invalid")


async def test_branche_chemin_et_cle_d_api(app: FastAPI, space: ApiSpace) -> None:
    """Critère 10."""
    error, body = await _call(
        app, "write_documents", space, branch="main", message="x", documents=[ROLE]
    )
    assert error and body["error"] == "branch_unsupported"
    with_path = {**ROLE, "path": "cartographie/objets/role/rol-preparateur.yaml"}
    error, body = await _write(app, space, [with_path])
    assert error and body["errors"][0]["code"] == "write_path_forbidden"
    assert body["errors"][0]["path"] == "$.documents[0].path"
    names = set(await tool_names(app, KEY))
    assert not names & set(WRITES)
    refused = await call_tool(
        app, KEY, "write_documents", _ids(space, message="x", documents=[ROLE])
    )
    assert json.loads(refused.content[0].text)["error"] == "primitive_forbidden"


async def test_identite_incoherente_genre_inconnu_champ_inconnu(
    app: FastAPI, space: ApiSpace
) -> None:
    wrong = {**ROLE, "identity": {"type": "role", "key": "ROL-AUTRE"}}
    error, body = await _write(app, space, [wrong])
    assert error and body["errors"][0]["code"] == "write_identity_mismatch"
    assert body["errors"][0]["path"] == "$.documents[0].content.cle"
    unknown = {**ROLE, "document_type": "inconnu"}
    error, body = await _write(app, space, [unknown])
    assert body["errors"][0]["code"] == "document_type_unknown"
    assert body["errors"][0]["aide"] == {"primitive": "list_document_types"}
    extra = {**ROLE, "content": {**ROLE["content"], "couleur": "bleu"}}
    error, body = await _write(app, space, [extra])
    assert body["errors"][0]["path"] == "$.documents[0].content.couleur"
    bad_type = {**ROLE, "identity": {"type": "inconnu", "key": "ROL-PREPARATEUR"}}
    error, body = await _write(app, space, [bad_type])
    assert body["errors"][0]["path"] == "$.documents[0].identity.type"


async def test_rejeu_identique_sans_changement(app: FastAPI, space: ApiSpace) -> None:
    """Critère 12 : un rejeu identique ne change aucun fichier."""
    error, first = await _write(app, space, [ROLE])
    assert not error, first
    role_path = "cartographie/objets/role/rol-preparateur.yaml"
    before = await _draft(app, space, role_path)
    error, again = await _write(app, space, [ROLE])
    assert not error, again
    assert again["documents"][0]["action"] == "unchanged" and again["changed_paths"] == []
    assert await _draft(app, space, role_path) == before


async def test_droits_par_genre(app: FastAPI, space: ApiSpace) -> None:
    """La capacité du genre est exigée : une personne sans « Gouvernance : modifier »
    écrit la cartographie mais pas un tag."""
    tag = {"document_type": "tag", "identity": {"id": "pii"}, "content": {"info": {"owner": "dpo"}}}
    error, body = await _write(app, space, [ROLE, tag], token=LIMITED)
    assert error
    [problem] = body["errors"]
    assert (problem["path"], problem["code"]) == (
        "$.documents[1].document_type",
        "document_type_forbidden",
    )
    error, body = await _write(app, space, [ROLE], token=LIMITED)
    assert not error, body


# --- Référentiels -------------------------------------------------------------------------

PAYS = {
    "title": "Pays",
    "type": "object",
    "x-key": ["code"],
    "required": ["code", "nom"],
    "properties": {
        "code": {"type": "string"},
        "nom": {"type": "string"},
        "zone": {"type": "string"},
    },
}


async def test_patch_valeur_de_referentiel(app: FastAPI, space: ApiSpace) -> None:
    """Critère 7 : seule la valeur visée change ; valeurs.json reste trié."""
    model = {
        "document_type": "referentiel_modele",
        "identity": {"referentiel": "pays"},
        "content": PAYS,
    }
    values = [
        {
            "document_type": "referentiel_valeur",
            "identity": {"referentiel": "pays", "key": code},
            "content": v,
        }
        for code, v in (
            ("FR", {"code": "FR", "nom": "France"}),
            ("BE", {"code": "BE", "nom": "Belgique"}),
        )
    ]
    error, body = await _write(app, space, [*values, model])  # le modèle peut venir après
    assert not error, body
    fr = next(d for d in body["documents"] if d["identity"].get("key") == "FR")
    error, patched = await _patch(
        app,
        space,
        "referentiel_valeur",
        {"referentiel": "pays", "key": "FR"},
        fr["version"],
        [{"op": "add", "path": "$.zone", "value": "UE"}],
    )
    assert not error, patched
    stored = json.loads(await _draft(app, space, "referentiels/pays/valeurs.json") or b"")
    assert stored == [
        {"code": "BE", "nom": "Belgique"},
        {"code": "FR", "nom": "France", "zone": "UE"},
    ]
    error, refused = await _patch(
        app,
        space,
        "referentiel_valeur",
        {"referentiel": "pays", "key": "FR"},
        patched["document"]["version"],
        [{"op": "add", "path": "$.couleur", "value": "bleu"}],
    )
    assert error and refused["errors"][0]["path"] == "$.couleur"


# --- BPMN ---------------------------------------------------------------------------------


def _process(key: str) -> str:
    return with_detail(new_process_xml(key, "Vente"), "PRC-PRISE-COMMANDE", "processus-de-vente")


async def test_bpmn_ecrit_puis_modifie_par_xpath(app: FastAPI, space: ApiSpace) -> None:
    """Critère 8 : XPath modifie le XML ; non conforme à BPMN 2.0 refusé ; règle souple
    non respectée → avertissement, l'écriture a lieu."""
    identity = {"key": "PRC-VENTE-DETAIL"}
    error, body = await _write(
        app,
        space,
        [
            {
                "document_type": "processus",
                "identity": identity,
                "content": _process("PRC-VENTE-DETAIL"),
            }
        ],
    )
    assert not error, body
    version = body["documents"][0]["version"]
    error, renamed = await _patch(
        app,
        space,
        "processus",
        identity,
        version,
        [{"op": "set", "path": "//bpmn:process/@name", "value": "Vente détaillée"}],
    )
    assert not error, renamed
    text = (
        await _draft(app, space, "cartographie/processus/prc-vente-detail.bpmn") or b""
    ).decode()
    assert 'name="Vente détaillée"' in text and "urban:reference" in text
    version = renamed["document"]["version"]
    error, refused = await _patch(
        app,
        space,
        "processus",
        identity,
        version,
        [{"op": "add", "path": "//bpmn:process", "value": "<bpmn:inconnu/>"}],
    )
    assert error and refused["errors"][0]["code"] == "carto_process_schema_invalid"
    assert refused["errors"][0]["aide"] == {"primitive": "describe_standard", "standard": "bpmn"}
    data = (
        '<bpmn:dataObjectReference id="Data_F" name="Facture" dataObjectRef="DO_F">'
        '<bpmn:extensionElements><urban:reference cle="OBJ-FACTURE" type="document-metier"/>'
        "</bpmn:extensionElements></bpmn:dataObjectReference>"
    )
    error, warned = await _patch(
        app,
        space,
        "processus",
        identity,
        version,
        [
            {"op": "add", "path": "//bpmn:process", "value": '<bpmn:dataObject id="DO_F"/>'},
            {"op": "add", "path": "//bpmn:process", "value": data},
        ],
    )
    assert not error, warned
    assert any("OBJ-FACTURE" in w["message"] for w in warned["warnings"])


async def test_bpmn_xpath_plusieurs_noeuds_et_non_rattache(app: FastAPI, space: ApiSpace) -> None:
    """Critère 9 (XPath) ; décision de Gael : un processus écrit par un agent est rattaché."""
    bare = {
        "document_type": "processus",
        "identity": {"key": "PRC-NU"},
        "content": new_process_xml("PRC-NU", "Nu"),
    }
    error, body = await _write(app, space, [bare])
    assert error and body["errors"][0]["code"] == "write_process_unattached"
    entry = {
        "document_type": "processus",
        "identity": {"key": "PRC-A"},
        "content": _process("PRC-A"),
    }
    error, body = await _write(app, space, [entry])
    version = body["documents"][0]["version"]
    error, refused = await _patch(
        app, space, "processus", {"key": "PRC-A"}, version, [{"op": "remove", "path": "//bpmndi:*"}]
    )
    assert error and refused["errors"][0]["code"] == "write_path_not_single"
    error, refused = await _patch(
        app, space, "processus", {"key": "PRC-A"}, version, [{"op": "remove", "path": "//bpmn:["}]
    )
    assert refused["errors"][0]["code"] == "write_xpath_invalid"
    assert refused["errors"][0]["aide"] == {"primitive": "describe_standard", "standard": "xpath"}


# --- Gouvernance --------------------------------------------------------------------------


async def test_gouvernance_par_l_ecriture_commune(app: FastAPI, space: ApiSpace) -> None:
    """Critère 11 : tag, classification (et ses niveaux par patch_document),
    certification : créer, modifier, supprimer."""
    error, body = await _write(
        app,
        space,
        [
            {
                "document_type": "tag",
                "identity": {"id": "pii/email"},
                "content": {"info": {"owner": "dpo"}},
            },
            {
                "document_type": "classification",
                "identity": {"id": "sensibilite"},
                "content": {
                    "name": "Sensibilité",
                    "levels": [{"name": "Public", "short_name": "PUB"}],
                },
            },
            {
                "document_type": "certification",
                "identity": {"slug": "or"},
                "content": {"name": "Or", "rank": 1, "tag": "certif/or"},
            },
        ],
    )
    assert not error, body
    versions = {d["document_type"]: d["version"] for d in body["documents"]}
    # Un niveau ajouté par patch_document, puis remis en tête.
    error, patched = await _patch(
        app,
        space,
        "classification",
        {"id": "sensibilite"},
        versions["classification"],
        [{"op": "add", "path": "$.levels[0]", "value": {"name": "Interne", "short_name": "INT"}}],
    )
    assert not error, patched
    error, listed = await _call(
        app, "list_documents", space, document_type="classification", branch="draft"
    )
    assert not error
    scheme = next(s for s in listed["documents"] if s["identity"] == {"id": "sensibilite"})
    assert [lv["name"] for lv in scheme["levels"]] == ["Interne", "Public"]
    error, renamed = await _patch(
        app,
        space,
        "certification",
        {"slug": "or"},
        versions["certification"],
        [{"op": "set", "path": "$.name", "value": "Or massif"}],
    )
    assert not error, renamed
    error, deleted = await _write(
        app,
        space,
        [
            {
                "document_type": "tag",
                "identity": {"id": "pii/email"},
                "version": versions["tag"],
                "delete": True,
            }
        ],
    )
    assert not error, deleted
    assert await _draft(app, space, "governance/tags/pii/email.yaml") is None


async def test_changer_d_identite_recreer_puis_supprimer(app: FastAPI, space: ApiSpace) -> None:
    """Critère 6 : $.cle et $.type sont figés (recréer puis supprimer) ; créer
    OBJ-COMMANDE-V2, recréer sa relation et supprimer l'ancien dans un même lot passe."""
    obj = await _read(app, space, OBJ) or {}
    rel = await _read(app, space, REL) or {}
    error, body = await _patch(
        app,
        space,
        "carto_objet",
        OBJ_ID,
        obj["version"],
        [{"op": "set", "path": "$.cle", "value": "X"}],
    )
    assert error and body["errors"][0]["code"] == "write_identity_frozen"
    v2 = _obj(
        "document-metier",
        "OBJ-COMMANDE-V2",
        {"cle": "OBJ-COMMANDE-V2", "type": "document-metier", "nom": "Commande"},
    )
    new_rel = _obj(
        "acces",
        "REL-PRISE-COMMANDE-ECRIT-COMMANDE-V2",
        {
            "cle": "REL-PRISE-COMMANDE-ECRIT-COMMANDE-V2",
            "type": "acces",
            "source": "PRC-PRISE-COMMANDE",
            "cible": "OBJ-COMMANDE-V2",
            "nom": "Écrit",
        },
    )
    error, body = await _write(
        app,
        space,
        [
            {
                "document_type": "carto_objet",
                "identity": OBJ_ID,
                "version": obj["version"],
                "delete": True,
            },
            new_rel,
            {
                "document_type": "carto_objet",
                "identity": REL_ID,
                "version": rel["version"],
                "delete": True,
            },
            v2,
        ],
    )
    assert not error, body
    assert await _read(app, space, OBJ) is None
    assert await _read(app, space, "cartographie/objets/document-metier/obj-commande-v2.yaml")


async def test_type_et_objets_dans_le_meme_lot(app: FastAPI, space: ApiSpace) -> None:
    """Critère 7 : le lot est validé sur son état final (l'objet avant son type)."""
    app_obj = _obj(
        "application", "APP-CAISSE", {"cle": "APP-CAISSE", "type": "application", "nom": "Caisse"}
    )
    app_type = {
        "document_type": "carto_type",
        "identity": {"slug": "application"},
        "content": {
            "title": "Application",
            "x-archimate": "ApplicationComponent",
            "type": "object",
        },
    }
    error, body = await _write(app, space, [app_obj, app_type])
    assert not error, body
    assert {d["action"] for d in body["documents"]} == {"created"}


async def test_taille_maximale_d_un_document(
    app: FastAPI, space: ApiSpace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Critère 8 : une entrée trop grande est refusée avec sa taille, la limite, son chemin
    et « aide » ; rien n'est écrit. Idem pour le résultat d'un patch_document. La limite
    vient du réglage (200 Ko par défaut) ; le test la baisse pour rester léger."""
    monkeypatch.setattr(app.state.settings, "document_max_bytes", 5 * 1024)
    big = _obj(
        "role",
        "ROL-BAVARD",
        {"cle": "ROL-BAVARD", "type": "role", "nom": "Bavard", "documentation": "x" * 6000},
    )
    error, body = await _write(app, space, [ROLE, big])
    assert error
    [problem] = body["errors"]
    assert (problem["path"], problem["code"]) == ("$.documents[1]", "write_document_too_large")
    assert problem["limit"] == 5 * 1024 and problem["size"] > 6000
    assert problem["aide"] == {"primitive": "describe_standard", "standard": "carto-objet"}
    assert await _draft(app, space, "cartographie/objets/role/rol-preparateur.yaml") is None
    version = (await _read(app, space, PRC) or {})["version"]
    error, body = await _patch(
        app,
        space,
        "carto_objet",
        PRC_ID,
        version,
        [{"op": "add", "path": "$.documentation", "value": "y" * 6000}],
    )
    assert error and body["errors"][0]["code"] == "write_document_too_large"
