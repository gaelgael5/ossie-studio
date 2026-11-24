"""Lier une propriété d'asset à une propriété de référentiel (ticket 8269c642) :
clé simple, clé composée (groupe complet obligatoire), une seule liaison par
propriété, délier tout le groupe, liens partiels ou cassés signalés et jamais
supprimés, survie au rafraîchissement, racine en lecture seule."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.mcp_surface.reads import _enrichment
from ossie_studio.parsers.conventions import dump_schema, load_schema
from ossie_studio.referentiels.model import dump_model
from ossie_studio.referentiels.repo import model_path
from ossie_studio.sources.curation import read_enrichment
from ossie_studio.sources.explorer import clear_caches
from ossie_studio.spaces.branch_service import KIND_CHILD, db_create_branch
from tests.space_fixture import ApiSpace, admin_workdir, forge_edit, make_api_space
from tests.test_raw_explorer import _git_files

STATUTS: dict[str, Any] = {
    "title": "Statuts de commande",
    "type": "object",
    "x-key": ["code"],
    "required": ["code"],
    "properties": {"code": {"type": "string"}, "libelle": {"type": "string"}},
}
DELAIS: dict[str, Any] = {
    "title": "Délais par pays et canal",
    "type": "object",
    "x-key": ["pays", "canal"],
    "required": ["pays", "canal"],
    "properties": {
        "pays": {"type": "string"},
        "canal": {"type": "string"},
        "delai": {"type": "integer"},
        "zones": {"type": "array", "items": {"type": "string"}},
    },
}
ASSET = "order.schema.yaml"
EXTEND = "sources/app/assets/order.extend.yaml"


@pytest.fixture(autouse=True)
def _fresh_caches() -> None:
    clear_caches()


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    space = await make_api_space(client, app, tmp_path)
    files = _git_files()
    files["sources/app/raw/order.schema.yaml"] = dump_schema(
        {
            "$id": ASSET,
            "title": "Order",
            "type": "object",
            "x-kind": "class",
            "properties": {
                "statut": {"type": "string"},
                "pays": {"type": "string"},
                "canal": {"type": "string"},
                "montant": {"type": "number"},
            },
        }
    )
    files[model_path("statuts-commande")] = dump_model(STATUTS)
    files[model_path("delais-pays-canal")] = dump_model(DELAIS)
    forge_edit(space, files)
    retained = await space.client.post(
        f"{space.base}/sources/app/curation/retain", json={"path": ASSET, "fields": None}
    )
    assert retained.status_code == 200, retained.text
    return space


def _url(sp: ApiSpace) -> str:
    return f"{sp.base}/sources/app/curation/referentiel-link"


async def _link(sp: ApiSpace, referentiel: str, links: dict[str, str], **extra: Any) -> Any:
    return await sp.client.put(
        _url(sp), json={"path": ASSET, "referentiel": referentiel, "links": links, **extra}
    )


async def _props(sp: ApiSpace) -> dict[str, Any]:
    detail = await sp.client.get(
        f"{sp.base}/curation-assets/asset", params={"source": "app", "path": ASSET}
    )
    assert detail.status_code == 200, detail.text
    return {p["name"]: p["referentiel"] for p in detail.json()["properties"]}


async def _draft_file(app: FastAPI, sp: ApiSpace, rel: str) -> dict[str, Any]:
    workdir = await admin_workdir(app, sp)

    def read(root: Path) -> dict[str, Any]:
        file = root / rel
        data = load_schema(file.read_text(encoding="utf-8")) if file.is_file() else {}
        return data if isinstance(data, dict) else {}

    return await workdir.read_branch(
        uuid.UUID(sp.space_id), "work/principale", sp.url, "x", None, read
    )


async def test_catalogue_des_referentiels(sp: ApiSpace) -> None:
    body = (await sp.client.get(f"{sp.base}/curation-assets/referentiels")).json()
    assert body["readonly"] is False
    delais = next(r for r in body["referentiels"] if r["name"] == "delais-pays-canal")
    assert delais["schema"] == "referentiels/delais-pays-canal/modele.schema.yaml"
    assert delais["key"] == ["pays", "canal"]
    # Propriétés simples seulement (pas de tableau).
    assert [p["name"] for p in delais["properties"]] == ["pays", "canal", "delai"]


# --- Critère 1 : clé simple ------------------------------------------------------------------


async def test_lier_a_une_cle_simple(app: FastAPI, sp: ApiSpace) -> None:
    done = await _link(sp, "statuts-commande", {"statut": "code"})
    assert done.status_code == 200, done.text
    assert done.json()["commit"]
    link = (await _props(sp))["statut"]
    assert (link["referentiel"], link["property"], link["status"]) == (
        "statuts-commande",
        "code",
        "ok",
    )
    assert (link["key_index"], link["key_size"]) == (1, 1)
    extend = await _draft_file(app, sp, EXTEND)
    assert extend["fields"]["statut"]["referentiel"] == {
        "schema": "referentiels/statuts-commande/modele.schema.yaml",
        "property": "code",
    }
    schema = await _draft_file(app, sp, "sources/app/assets/order.schema.yaml")
    assert "x-referentiel" not in schema["properties"]["statut"]
    assert "referentiel" not in schema["properties"]["statut"]


# --- Critères 2 et 3 : clé composée -----------------------------------------------------------


async def test_cle_composee_groupe_complet_obligatoire(sp: ApiSpace) -> None:
    partial = await _link(sp, "delais-pays-canal", {"pays": "pays"})
    assert partial.status_code == 400
    assert partial.json()["error"] == "referentiel_link_incomplete"
    assert partial.json()["error_args"]["missing"] == ["canal"]
    done = await _link(sp, "delais-pays-canal", {"pays": "pays", "canal": "canal"})
    assert done.status_code == 200, done.text
    props = await _props(sp)
    assert props["pays"]["group"] and props["pays"]["group"] == props["canal"]["group"]
    assert props["pays"]["status"] == "ok"
    assert (props["canal"]["key_index"], props["canal"]["linked_keys"]) == (2, 2)
    assert props["pays"]["members"] == ["canal", "pays"]


async def test_propriete_hors_cle_d_un_referentiel_a_cle_composee(sp: ApiSpace) -> None:
    done = await _link(sp, "delais-pays-canal", {"montant": "delai"})
    assert done.status_code == 200, done.text
    link = (await _props(sp))["montant"]
    assert (link["status"], link["group"], link["key_index"]) == ("ok", "", 0)


async def test_propriete_complexe_refusee(sp: ApiSpace) -> None:
    refused = await _link(sp, "delais-pays-canal", {"montant": "zones"})
    assert refused.status_code == 400
    assert refused.json()["error"] == "referentiel_link_property"


# --- Critère 4 : une seule liaison par propriété ------------------------------------------------


async def test_propriete_deja_liee(sp: ApiSpace) -> None:
    await _link(sp, "statuts-commande", {"statut": "code"})
    again = await _link(sp, "delais-pays-canal", {"statut": "delai"})
    assert again.status_code == 409
    assert again.json()["error"] == "referentiel_link_taken"
    in_group = await _link(sp, "delais-pays-canal", {"pays": "pays", "statut": "canal"})
    assert in_group.status_code == 409
    # « Modifier » : le groupe remplacé libère la propriété.
    edited = await _link(sp, "delais-pays-canal", {"statut": "delai"}, replace="statut")
    assert edited.status_code == 200, edited.text
    assert (await _props(sp))["statut"]["referentiel"] == "delais-pays-canal"


# --- Critère 5 : délier tout le groupe -----------------------------------------------------------


async def test_delier_tout_le_groupe(sp: ApiSpace) -> None:
    await _link(sp, "delais-pays-canal", {"pays": "pays", "canal": "canal"})
    done = await sp.client.delete(_url(sp), params={"path": ASSET, "field": "pays"})
    assert done.status_code == 200, done.text
    assert done.json()["fields"] == ["canal", "pays"]
    props = await _props(sp)
    assert props["pays"] is None and props["canal"] is None


# --- Critères 6 et 7 : liens partiels ou cassés, signalés, jamais supprimés --------------------


async def test_la_cle_grandit_le_lien_devient_partiel(app: FastAPI, sp: ApiSpace) -> None:
    await _link(sp, "statuts-commande", {"statut": "code"})
    forge_edit(
        sp, {model_path("statuts-commande"): dump_model({**STATUTS, "x-key": ["code", "libelle"]})}
    )
    link = (await _props(sp))["statut"]
    assert (link["status"], link["missing"], link["linked_keys"], link["key_size"]) == (
        "partial",
        ["libelle"],
        1,
        2,
    )
    listing = (await sp.client.get(f"{sp.base}/curation-assets")).json()
    assert listing["assets"][0]["referentiel_issues"] == 1
    assert (await _draft_file(app, sp, EXTEND))["fields"]["statut"]["referentiel"]


async def test_referentiel_supprime_lien_casse(app: FastAPI, sp: ApiSpace) -> None:
    await _link(sp, "statuts-commande", {"statut": "code"})
    forge_edit(sp, {model_path("statuts-commande"): None})
    assert (await _props(sp))["statut"]["status"] == "broken"
    assert (await _draft_file(app, sp, EXTEND))["fields"]["statut"]["referentiel"]


# --- Critère 8 : le rafraîchissement conserve les liens ------------------------------------------


async def test_rafraichir_conserve_les_liens(sp: ApiSpace) -> None:
    await _link(sp, "statuts-commande", {"statut": "code"})
    refreshed = await sp.client.post(
        f"{sp.base}/sources/app/curation/refresh", json={"paths": [ASSET]}
    )
    assert refreshed.status_code == 200, refreshed.text
    assert (await _props(sp))["statut"]["referentiel"] == "statuts-commande"


# --- Critères 9 et 10 : racine en lecture seule, droits -----------------------------------------


async def test_racine_en_lecture_seule(app: FastAPI, sp: ApiSpace) -> None:
    async with app.state.pool.acquire() as conn:
        root = await db_create_branch(
            conn, uuid.UUID(sp.space_id), "main", "main", KIND_CHILD, git_ref="main"
        )
    url = f"/api/spaces/{sp.space_id}/branches/{root.id}/sources/app/curation/referentiel-link"
    refused = await sp.client.put(
        url, json={"path": ASSET, "referentiel": "statuts-commande", "links": {"statut": "code"}}
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"] == "branch_root_readonly"
    catalog = await sp.client.get(
        f"/api/spaces/{sp.space_id}/branches/{root.id}/curation-assets/referentiels"
    )
    assert catalog.json()["readonly"] is True


async def test_sans_authentification_refus(app: FastAPI, sp: ApiSpace) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anonymous:
        refused = await anonymous.put(
            _url(sp),
            json={"path": ASSET, "referentiel": "statuts-commande", "links": {"statut": "code"}},
        )
    assert refused.status_code == 401


# --- Le lien est lisible par un agent (lecture MCP d'un asset) ----------------------------------


async def test_lien_visible_dans_la_lecture_mcp(app: FastAPI, sp: ApiSpace) -> None:
    await _link(sp, "statuts-commande", {"statut": "code"})
    workdir = await admin_workdir(app, sp)
    view = await workdir.read_branch(
        uuid.UUID(sp.space_id),
        "work/principale",
        sp.url,
        "x",
        None,
        lambda root: read_enrichment(root, "app", ASSET),
    )
    assert view is not None
    statut = next(f for f in view.fields if f.name == "statut")
    assert _enrichment(statut)["referentiel"] == {
        "schema": "referentiels/statuts-commande/modele.schema.yaml",
        "property": "code",
    }
    montant = next(f for f in view.fields if f.name == "montant")
    assert "referentiel" not in _enrichment(montant)
