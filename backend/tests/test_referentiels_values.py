"""Onglet Valeurs côté serveur (ticket fbe04555) : lire, vérifier sans écrire,
enregistrer la liste complète dans le brouillon (triée par clé, ordre des
tableaux conservé) ; problèmes localisés par valeur et par champ ; refus nommés."""

from __future__ import annotations

import copy
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.referentiels.model import SEPARATOR, dump_model
from ossie_studio.referentiels.repo import model_path, values_path
from ossie_studio.referentiels.values import dump_values
from ossie_studio.spaces.branch_service import KIND_CHILD, db_create_branch
from tests.space_fixture import ApiSpace, forge_edit, forge_read, make_api_space

MODEL: dict[str, Any] = {
    "title": "Délais",
    "type": "object",
    "x-key": ["pays", "mode"],
    "required": ["pays", "mode"],
    "properties": {
        "pays": {"type": "string"},
        "mode": {"type": "string", "enum": ["site", "domicile"]},
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
    {"pays": "FR", "mode": "site"},
    {"pays": "BE", "mode": "site"},
]
NEW: dict[str, Any] = {
    "pays": "DE",
    "mode": "domicile",
    "paliers": [{"poids_max": 30, "delai_jours": 5}, {"poids_max": 1, "delai_jours": 2}],
}


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    api_space = await make_api_space(client, app, tmp_path)
    forge_edit(
        api_space,
        {
            model_path("delais"): dump_model(MODEL),
            values_path("delais"): dump_values(MODEL, VALUES),
        },
    )
    return api_space


def _url(space: ApiSpace, rest: str = "") -> str:
    return f"{space.base}/referentiels/delais/valeurs{rest}"


async def test_lire_les_valeurs_triees(space: ApiSpace) -> None:
    body = (await space.client.get(_url(space))).json()
    assert body == {"values": [VALUES[1], VALUES[0]], "count": 2}


# --- Critères 1 et 4 : ajouter une valeur à deux éléments, ordre des éléments conservé ------------


async def test_ajouter_et_enregistrer_dans_le_brouillon(space: ApiSpace) -> None:
    saved = await space.client.put(_url(space), json={"values": [*VALUES, NEW]})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["revision"].startswith("draft:")
    # Triées par clé ; l'ordre des éléments du tableau est celui donné.
    assert [(v["pays"], v["mode"]) for v in body["values"]] == [
        ("BE", "site"),
        ("DE", "domicile"),
        ("FR", "site"),
    ]
    assert [p["poids_max"] for p in body["values"][1]["paliers"]] == [30, 1]
    assert (await space.client.get(_url(space))).json()["count"] == 3
    # Brouillon seulement : la forge n'a que les deux valeurs publiées.
    published = forge_read(space, values_path("delais"))
    assert published is not None and '"DE"' not in published


# --- Critères 2 et 3 : clé déjà présente, élément incomplet ---------------------------------------


async def test_cle_deja_presente_et_element_incomplet(space: ApiSpace) -> None:
    twin = {"pays": "FR", "mode": "site"}
    broken = copy.deepcopy(NEW)
    del broken["paliers"][1]["delai_jours"]
    candidate = [*VALUES, twin, broken]
    check = (
        await space.client.post(_url(space, "/verification"), json={"values": candidate})
    ).json()
    assert check["ok"] is False
    assert {
        "path": "FR+site",
        "code": "key_duplicate",
        "detail": "FR+site",
        "index": 2,
        "field": "",
    } in check["problems"]
    field = f"paliers[1]{SEPARATOR}delai_jours"
    assert {
        "path": f"DE+domicile{SEPARATOR}{field}",
        "code": "value_required",
        "detail": "",
        "index": 3,
        "field": field,
    } in check["problems"]

    refused = await space.client.put(_url(space), json={"values": candidate})
    assert refused.status_code == 400
    assert refused.json()["error"] == "values_invalid"
    # Rien n'a été écrit.
    assert (await space.client.get(_url(space))).json()["count"] == 2


async def test_verification_d_une_liste_valide(space: ApiSpace) -> None:
    check = (await space.client.post(_url(space, "/verification"), json={"values": VALUES})).json()
    assert check == {"ok": True, "problems": []}


# --- Critère 6 : plusieurs centaines de valeurs ---------------------------------------------------


async def test_plusieurs_centaines_de_valeurs(space: ApiSpace) -> None:
    many = [{"pays": f"P{i:03d}", "mode": "site"} for i in range(400)]
    assert (await space.client.post(_url(space, "/verification"), json={"values": many})).json()[
        "ok"
    ]
    saved = await space.client.put(_url(space), json={"values": many})
    assert saved.status_code == 200, saved.text
    assert (await space.client.get(_url(space))).json()["count"] == 400


# --- Critères 7 et 8 : lecture seule, droits ------------------------------------------------------


async def test_racine_en_lecture_seule(app: FastAPI, space: ApiSpace) -> None:
    async with app.state.pool.acquire() as conn:
        root = await db_create_branch(
            conn, uuid.UUID(space.space_id), "main", "main", KIND_CHILD, git_ref="main"
        )
    url = f"/api/spaces/{space.space_id}/branches/{root.id}/referentiels/delais/valeurs"
    # Bug e7deb2b9 : plus de refus « branche racine » (brouillon de la personne).
    answer = await space.client.put(url, json={"values": VALUES})
    assert answer.json().get("error") != "branch_root_readonly", answer.text


async def test_enregistrer_demande_le_droit_de_modifier(app: FastAPI, space: ApiSpace) -> None:
    from httpx import ASGITransport

    from tests.conftest import create_plain_user, login

    await create_plain_user(app, "lea@example.org", "motdepasse-solide")
    async with app.state.pool.acquire() as conn:
        user_id = await conn.fetchval("SELECT id FROM users WHERE email = 'lea@example.org'")
        reader = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) "
            "VALUES ('lecteur-val', '{spaces:read,referentiels:read}') RETURNING id"
        )
        await conn.execute("UPDATE users SET profile_id = $2 WHERE id = $1", user_id, reader)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as lea:
        await login(lea, "lea@example.org", "motdepasse-solide")
        assert (await lea.get(_url(space))).status_code == 200
        check = await lea.post(_url(space, "/verification"), json={"values": VALUES})
        assert check.status_code == 200
        assert (await lea.put(_url(space), json={"values": VALUES})).status_code == 403
