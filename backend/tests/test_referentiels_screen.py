"""Écran « Référentiels » côté serveur (ticket 829a86a7) : créer un référentiel,
enregistrer son modèle (objets et tableaux d'objets, clé composée), aperçu de
l'effet sur les valeurs, refus nommés, racine en lecture seule, droits,
historique et journal d'audit."""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.referentiels.model import SEPARATOR, dump_model
from ossie_studio.referentiels.repo import model_path, values_path
from ossie_studio.referentiels.values import dump_values
from ossie_studio.spaces.branch_service import KIND_CHILD, db_create_branch
from tests.conftest import create_plain_user, login
from tests.space_fixture import ApiSpace, forge_edit, forge_read, make_api_space

PASSWORD = "motdepasse-solide"

MODEL: dict[str, Any] = {
    "title": "Délais",
    "type": "object",
    "x-key": ["pays", "mode"],
    "required": ["pays", "mode", "libelle"],
    "properties": {
        "pays": {"type": "string"},
        "mode": {"type": "string"},
        "libelle": {"type": "string"},
        "paliers": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["poids_max", "delai_jours"],
                "properties": {
                    "poids_max": {"type": "number"},
                    "delai_jours": {"type": "integer"},
                },
            },
        },
    },
}
VALUES: list[dict[str, Any]] = [
    {
        "pays": "FR",
        "mode": "site",
        "libelle": "Relais",
        "paliers": [{"poids_max": 1, "delai_jours": 2}],
    },
    {"pays": "FR", "mode": "domicile", "libelle": "Domicile"},
    {"pays": "BE", "mode": "site", "libelle": "Relais BE"},
]


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    return await make_api_space(client, app, tmp_path)


def _url(space: ApiSpace, rest: str = "") -> str:
    return f"{space.base}/referentiels{rest}"


def _publish(space: ApiSpace) -> None:
    forge_edit(
        space,
        {
            model_path("delais"): dump_model(MODEL),
            values_path("delais"): dump_values(MODEL, VALUES),
        },
    )


# --- Critère 2 : créer, modéliser (tableau d'objets, clé de deux propriétés) ----------------


async def test_creer_puis_modeliser(space: ApiSpace) -> None:
    created = await space.client.post(_url(space), json={"label": "Délais", "slug": "delais"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["model"]["x-key"] == ["code"]
    assert body["model_path"] == "referentiels/delais/modele.schema.yaml"
    assert body["unpublished"] is True

    saved = await space.client.put(_url(space, "/delais/modele"), json={"model": MODEL})
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"].startswith("draft:")
    detail = (await space.client.get(_url(space, "/delais"))).json()
    assert detail["model"] == MODEL
    assert detail["model_yaml"] == dump_model(MODEL)
    assert detail["readonly"] is False
    listing = (await space.client.get(_url(space))).json()
    assert [(r["name"], r["key"], r["unpublished"]) for r in listing["referentiels"]] == [
        ("delais", ["pays", "mode"], True)
    ]
    # Rien n'est publié : le brouillon seul porte le référentiel.
    assert forge_read(space, model_path("delais")) is None

    taken = await space.client.post(_url(space), json={"label": "Autre", "slug": "delais"})
    assert taken.status_code == 409
    assert taken.json()["error"] == "referentiel_name_taken"
    bad = await space.client.post(_url(space), json={"label": " ", "slug": "Pas Bon"})
    assert bad.status_code == 400
    assert set(bad.json()["error_args"]["fields"]) == {"label_required", "slug_invalid"}


# --- Critère 4 : la clé reste simple et obligatoire -----------------------------------------


async def test_cle_facultative_ou_non_simple_refusee(space: ApiSpace) -> None:
    _publish(space)
    optional = copy.deepcopy(MODEL)
    optional["required"] = ["pays", "libelle"]
    refused = await space.client.put(_url(space, "/delais/modele"), json={"model": optional})
    assert refused.status_code == 400
    assert refused.json()["error"] == "model_invalid"
    codes = {p["code"] for p in refused.json()["error_args"]["problems"]}
    assert codes == {"key_property_optional"}

    array_key = {**MODEL, "x-key": ["pays", "paliers"]}
    refused = await space.client.put(_url(space, "/delais/modele"), json={"model": array_key})
    assert {p["code"] for p in refused.json()["error_args"]["problems"]} == {
        "key_property_not_simple"
    }


# --- Critère 5 : retirer une propriété quand des valeurs existent -----------------------------


async def test_retirer_une_propriete_apercu_puis_enregistrement(space: ApiSpace) -> None:
    _publish(space)
    without = copy.deepcopy(MODEL)
    del without["properties"]["paliers"]
    preview = await space.client.post(_url(space, "/delais/modele/apercu"), json={"model": without})
    assert preview.status_code == 200, preview.text
    assert preview.json()["ok"] is True
    assert preview.json()["values"] == 3 and preview.json()["valid"] == 3
    assert preview.json()["removed"] == [{"property": "paliers", "values": 1}]

    # Une propriété imbriquée retirée est aussi comptée.
    nested = copy.deepcopy(MODEL)
    del nested["properties"]["paliers"]["items"]["properties"]["delai_jours"]
    nested["properties"]["paliers"]["items"]["required"] = ["poids_max"]
    deep = (
        await space.client.post(_url(space, "/delais/modele/apercu"), json={"model": nested})
    ).json()
    assert deep["removed"] == [{"property": f"paliers[]{SEPARATOR}delai_jours", "values": 1}]

    saved = await space.client.put(_url(space, "/delais/modele"), json={"model": without})
    assert saved.status_code == 200, saved.text
    # Les valeurs du brouillon n'ont plus la propriété retirée.
    detail = (await space.client.get(_url(space, "/delais"))).json()
    assert "paliers" not in detail["model"]["properties"]


async def test_apercu_d_une_propriete_requise_ajoutee(space: ApiSpace) -> None:
    _publish(space)
    stricter = copy.deepcopy(MODEL)
    stricter["properties"]["zone"] = {"type": "string"}
    stricter["required"] = [*stricter["required"], "zone"]
    preview = (
        await space.client.post(_url(space, "/delais/modele/apercu"), json={"model": stricter})
    ).json()
    assert preview["ok"] is False and preview["valid"] == 0
    assert preview["problem_count"] == 3
    refused = await space.client.put(_url(space, "/delais/modele"), json={"model": stricter})
    assert refused.status_code == 400
    assert refused.json()["error"] == "values_invalid"


# --- Critère 6 : nouvelle clé non unique --------------------------------------------------------


async def test_nouvelle_cle_non_unique_doublons_listes(space: ApiSpace) -> None:
    _publish(space)
    by_country = {**MODEL, "x-key": ["pays"]}
    preview = (
        await space.client.post(_url(space, "/delais/modele/apercu"), json={"model": by_country})
    ).json()
    assert preview["duplicates"] == ["FR"]
    refused = await space.client.put(_url(space, "/delais/modele"), json={"model": by_country})
    assert refused.status_code == 400
    problems = refused.json()["error_args"]["problems"]
    assert {"path": "FR", "code": "key_duplicate", "detail": "FR"} in problems


# --- Critère 7 : racine du dépôt en lecture seule -----------------------------------------------


async def test_racine_en_lecture_seule(app: FastAPI, space: ApiSpace) -> None:
    _publish(space)
    async with app.state.pool.acquire() as conn:
        root = await db_create_branch(
            conn, uuid.UUID(space.space_id), "main", "main", KIND_CHILD, git_ref="main"
        )
    base = f"/api/spaces/{space.space_id}/branches/{root.id}/referentiels"
    assert (await space.client.get(base)).json()["readonly"] is True
    refused = await space.client.put(f"{base}/delais/modele", json={"model": MODEL})
    assert refused.status_code == 409
    assert refused.json()["error"] == "branch_root_readonly"


# --- Critère 8 : droits, historique, journal d'audit ---------------------------------------------


async def test_droits_consulter_et_modifier(app: FastAPI, space: ApiSpace) -> None:
    _publish(space)
    await create_plain_user(app, "ana@example.org", PASSWORD)
    async with app.state.pool.acquire() as conn:
        user_id = await conn.fetchval("SELECT id FROM users WHERE email = 'ana@example.org'")
        reader = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) "
            "VALUES ('lecteur-ref', '{spaces:read,referentiels:read}') RETURNING id"
        )
        await conn.execute("UPDATE users SET profile_id = $2 WHERE id = $1", user_id, reader)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ana:
        await login(ana, "ana@example.org", PASSWORD)
        assert (await ana.get(_url(space))).status_code == 200
        denied = await ana.post(_url(space), json={"label": "X", "slug": "x"})
        assert denied.status_code == 403
        # L'API de lecture publiée demande le même droit « consulter ».
        published = await ana.get(f"/api/spaces/{space.space_id}/referentiels")
        assert published.status_code == 200
        async with app.state.pool.acquire() as conn:
            await conn.execute(
                "UPDATE profiles SET capabilities = '{spaces:read}' WHERE id = $1", reader
            )
        assert (await ana.get(_url(space))).status_code == 403
        assert (await ana.get(f"/api/spaces/{space.space_id}/referentiels")).status_code == 403


async def test_historique_et_journal_d_audit(space: ApiSpace) -> None:
    _publish(space)
    history = (await space.client.get(_url(space, "/delais/historique"))).json()["commits"]
    assert len(history) == 1 and history[0]["author"]
    audit = await space.client.get(
        "/api/audit",
        params={"space_id": space.space_id, "branch_id": space.branch_id, "type": "referentiel"},
    )
    assert audit.status_code == 200, audit.text
    events = audit.json()["events"]
    assert [(e["object_type"], e["object_id"], e["action"]) for e in events] == [
        ("referentiel", "delais", "created")
    ]
    assert "referentiel" in audit.json()["types"]
    # Valeurs publiées relues telles quelles.
    raw = forge_read(space, values_path("delais"))
    assert raw is not None and len(json.loads(raw)) == 3
