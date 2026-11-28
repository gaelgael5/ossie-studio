"""Le modèle sémantique Ossie par les primitives communes (epic « Primitives MCP —
écriture commune par genre », ticket b4aeae83) : genres domaine, dataset,
relation_dataset, reference_dataset, écrits par la seule porte d'écriture des domaines
(`domains/documents.write_domain`), qui répartit les fichiers."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import yaml
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.cartographie.service import CartographieTarget
from ossie_studio.domains.documents import _check, _ossie_validator
from ossie_studio.mcp_surface.caller import McpCaller
from ossie_studio.spaces.service import db_get_space
from tests.mcp_helpers import call_tool
from tests.space_fixture import ApiSpace, admin_workdir, make_api_space

AGENT = "domaines-agent"


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    api_space = await make_api_space(client, app, tmp_path)
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives, all_read) VALUES ('d', $1, true) "
            "RETURNING id",
            ["write_documents", "patch_document"],
        )
        admin_id = await conn.fetchval("SELECT id FROM users WHERE is_bootstrap")
    caller = McpCaller(kind="oauth", ref="d", agent_profile_id=profile, user_id=admin_id)

    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return caller if token == AGENT else None

    app.state.mcp_resolvers.append(resolver)
    return api_space


async def _call(app: FastAPI, space: ApiSpace, tool: str, **args: Any) -> tuple[bool, Any]:
    ids = {"space_id": space.space_id, "branch_id": space.branch_id}
    result = await call_tool(app, AGENT, tool, {**ids, **args})
    if result.is_error:
        return True, json.loads(result.content[0].text)
    return False, result.structured_content


async def _write(app: FastAPI, space: ApiSpace, *documents: dict[str, Any]) -> tuple[bool, Any]:
    return await _call(app, space, "write_documents", message="domaine", documents=list(documents))


async def _files(app: FastAPI, space: ApiSpace) -> dict[str, str]:
    """Les fichiers de `domaines/` tels que le brouillon de la personne les contient."""
    workdir = await admin_workdir(app, space)
    async with app.state.pool.acquire() as conn:
        git_space = await db_get_space(conn, uuid.UUID(space.space_id))
    assert git_space is not None
    target = CartographieTarget(workdir, git_space, "work/principale", "x", None, "a@a")

    def read(root: Path) -> dict[str, str]:
        base = root / "domaines"
        if not base.is_dir():
            return {}
        return {f.name: f.read_text(encoding="utf-8") for f in sorted(base.iterdir())}

    files: dict[str, str] = await target.read(read)
    return files


def _field(name: str) -> dict[str, Any]:
    return {"name": name, "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": name}]}}


def _dataset(name: str, consultable: bool, *fields: str, **extension: Any) -> dict[str, Any]:
    return {
        "document_type": "dataset",
        "identity": {"domain": "ventes", "name": name},
        "content": {
            "ossie": {
                "name": name,
                "source": f"boutique.{name}",
                "fields": [_field(f) for f in fields],
            },
            "extension": {"consultable": consultable, "derived": False, **extension},
        },
    }


DOMAIN = {
    "document_type": "domaine",
    "identity": {"name": "ventes"},
    "content": {"label": "Ventes"},
}
CLIENT = _dataset("client", True, "id", "nom", fields={"nom": {"datatype": "String"}})
COMMANDE = _dataset("commande", True, "id", "client_id")
PANIER = _dataset("panier", False, "lignes")
RELATION = {
    "document_type": "relation_dataset",
    "identity": {"domain": "ventes", "name": "commande-client"},
    "content": {
        "from": "commande",
        "to": "client",
        "from_columns": ["client_id"],
        "to_columns": ["id"],
    },
}


async def test_creer_un_domaine_en_un_lot(app: FastAPI, space: ApiSpace) -> None:
    """Critère 1 : un seul write_documents (ordre libre) ; les trois fichiers sont
    écrits ; le document Ossie passe le schéma officiel et ne porte rien de hors norme.
    Une relation entre un consultable et un virtuel reste refusée par le service
    (question 4 de l'epic des domaines)."""
    error, body = await _write(app, space, RELATION, PANIER, CLIENT, COMMANDE, DOMAIN)
    assert not error, body
    files = await _files(app, space)
    assert set(files) == {"ventes.ossie.yaml", "ventes.virtuel.ossie.yaml", "ventes.extend.yaml"}
    ossie = yaml.safe_load(files["ventes.ossie.yaml"])
    _check(_ossie_validator(), ossie, "ossie_invalid")  # le schéma officiel
    [model] = ossie["semantic_model"]
    assert [d["name"] for d in model["datasets"]] == ["client", "commande"]
    assert model["relationships"][0]["name"] == "commande-client"
    text = files["ventes.ossie.yaml"]
    for foreign in ("consultable", "derived", "datatype", "label: Ventes"):
        assert foreign not in text, foreign
    virtual = yaml.safe_load(files["ventes.virtuel.ossie.yaml"])
    assert [d["name"] for d in virtual["semantic_model"][0]["datasets"]] == ["panier"]
    extend = yaml.safe_load(files["ventes.extend.yaml"])
    assert extend["label"] == "Ventes"
    assert extend["datasets"]["client"]["fields"] == {"nom": {"datatype": "String"}}
    crossed = {
        **RELATION,
        "identity": {"domain": "ventes", "name": "panier-client"},
        "content": {
            "from": "panier",
            "to": "client",
            "from_columns": ["lignes"],
            "to_columns": ["id"],
        },
    }
    error, body = await _write(app, space, crossed)
    assert error and body["errors"][0]["problem"] == "relationship_cross_document"


async def test_passer_un_dataset_en_non_consultable_le_deplace(
    app: FastAPI, space: ApiSpace
) -> None:
    """Critère 2."""
    await _write(app, space, DOMAIN, CLIENT, PANIER)
    error, read = await _call(
        app,
        space,
        "read_document",
        document_type="dataset",
        identity=CLIENT["identity"],
        branch="draft",
    )
    assert read["path"] == "domaines/ventes.ossie.yaml"
    error, body = await _call(
        app,
        space,
        "patch_document",
        document_type="dataset",
        identity=CLIENT["identity"],
        version=read["version"],
        operations=[{"op": "set", "path": "$.extension.consultable", "value": False}],
    )
    assert not error, body
    assert body["document"]["path"] == "domaines/ventes.virtuel.ossie.yaml"
    files = await _files(app, space)
    assert "ventes.ossie.yaml" not in files
    names = [
        d["name"]
        for d in yaml.safe_load(files["ventes.virtuel.ossie.yaml"])["semantic_model"][0]["datasets"]
    ]
    assert sorted(names) == ["client", "panier"]


async def test_supprimer_un_dataset_cite(app: FastAPI, space: ApiSpace) -> None:
    """Critère 3 : refusé seul ; accepté avec sa relation dans le même lot."""
    await _write(app, space, DOMAIN, CLIENT, COMMANDE, RELATION)
    versions = {}
    for doc in (CLIENT, RELATION):
        error, read = await _call(
            app,
            space,
            "read_document",
            document_type=doc["document_type"],
            identity=doc["identity"],
            branch="draft",
        )
        versions[doc["document_type"]] = read["version"]
    delete_client = {
        "document_type": "dataset",
        "identity": CLIENT["identity"],
        "version": versions["dataset"],
        "delete": True,
    }
    error, body = await _write(app, space, delete_client)
    assert error and body["errors"][0]["problem"] == "relationship_dataset_missing"
    delete_relation = {
        "document_type": "relation_dataset",
        "identity": RELATION["identity"],
        "version": versions["relation_dataset"],
        "delete": True,
    }
    error, body = await _write(app, space, delete_client, delete_relation)
    assert not error, body
    error, listed = await _call(
        app,
        space,
        "list_documents",
        document_type="dataset",
        identity={"domain": "ventes"},
        branch="draft",
    )
    assert [d["identity"]["name"] for d in listed["documents"]] == ["commande"]


async def test_relation_vers_une_colonne_inexistante(app: FastAPI, space: ApiSpace) -> None:
    """Critère 4 : refusée, avec le chemin fautif et `aide`."""
    wrong = {**RELATION, "content": {**RELATION["content"], "to_columns": ["code"]}}
    error, body = await _write(app, space, DOMAIN, CLIENT, COMMANDE, wrong)
    assert error
    [refusal] = body["errors"]
    assert refusal["path"] == "$.documents[3].content.to_columns[0]"
    assert refusal["problem"] == "relationship_column_unknown"
    assert refusal["aide"] == {"primitive": "describe_standard", "standard": "relation-dataset"}


async def test_lister_lire_et_schema(app: FastAPI, space: ApiSpace) -> None:
    """Critères 5 et 6 : liste avec chemin et version ; lecture complète ; schéma du
    contenu ; un chemin fourni est refusé."""
    await _write(app, space, DOMAIN, CLIENT, PANIER)
    error, listed = await _call(
        app,
        space,
        "list_documents",
        document_type="dataset",
        identity={"domain": "ventes"},
        branch="draft",
    )
    by_name = {d["identity"]["name"]: d for d in listed["documents"]}
    assert by_name["client"]["path"] == "domaines/ventes.ossie.yaml"
    assert by_name["panier"]["path"] == "domaines/ventes.virtuel.ossie.yaml"
    assert by_name["client"]["consultable"] is True and by_name["client"]["fields"] == 2
    error, read = await _call(
        app,
        space,
        "read_document",
        document_type="dataset",
        identity=CLIENT["identity"],
        branch="draft",
    )
    assert read["content"] == CLIENT["content"]
    assert read["version"] == by_name["client"]["version"]
    error, domains = await _call(
        app, space, "list_documents", document_type="domaine", branch="draft"
    )
    [domain] = domains["documents"]
    assert (domain["name"], domain["datasets"]) == ("Ventes", 2)
    error, schema = await _call(app, space, "read_schema", document_type="dataset")
    assert not error, schema
    assert schema["schema"]["properties"]["ossie"] == {"$ref": "#/$defs/Dataset"}
    assert "Dataset" in schema["schema"]["$defs"]
    error, body = await _write(app, space, {**CLIENT, "path": "domaines/autre.ossie.yaml"})
    assert error and body["errors"][0]["code"] == "write_path_forbidden"


async def test_ce_que_le_service_ne_garde_pas_est_refuse(app: FastAPI, space: ApiSpace) -> None:
    """La norme non altérée : une propriété d'Ossie que le service ne conserve pas, ou
    une seconde expression, est refusée plutôt que perdue."""
    lost = {
        **CLIENT,
        "content": {
            **CLIENT["content"],
            "ossie": {**CLIENT["content"]["ossie"], "ai_context": "client"},
        },
    }
    error, body = await _write(app, space, DOMAIN, lost)
    assert error and body["errors"][0]["path"] == "$.documents[1].content.ossie"
    assert "ai_context" in body["errors"][0]["message"]
