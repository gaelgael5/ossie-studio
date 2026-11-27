"""Importer des valeurs depuis un fichier (ticket a909dc66) : appliquer un import
à un fichier déposé (ou au fichier d'exemple), en fusion par clé ou en
remplacement, tout ou rien, dans le brouillon de la personne ; journal de
chaque import et date du dernier import. À l'écran seulement : les API des
référentiels sont en lecture seule (Gael, 24 novembre)."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.referentiels.model import SEPARATOR, dump_model
from ossie_studio.referentiels.repo import model_path, values_path
from ossie_studio.referentiels.values import dump_values
from ossie_studio.spaces.branch_service import KIND_CHILD, db_create_branch
from tests.space_fixture import ApiSpace, forge_edit, make_api_space
from tests.test_referentiels_read import _key

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
COUNTRIES = ["FR", "BE", "DE", "ES", "IT", "NL", "PT"]
VALUES = [{"pays": p, "mode": "site", "delai": 1} for p in COUNTRIES]
TEMPLATE = '{"pays": $.Pays, "mode": $.Mode, "delai": $.Delai#integer}\n'


def _csv(rows: list[tuple[str, str, str]]) -> bytes:
    return ("Pays;Mode;Delai\n" + "".join(f"{p};{m};{d}\n" for p, m, d in rows)).encode()


# Dix lignes : cinq inchangées, deux modifiées (FR, BE), trois nouvelles.
TEN = _csv(
    [
        ("FR", "site", "2"),
        ("BE", "site", "4"),
        ("DE", "site", "1"),
        ("ES", "site", "1"),
        ("IT", "site", "1"),
        ("NL", "site", "1"),
        ("PT", "site", "1"),
        ("FR", "domicile", "3"),
        ("BE", "domicile", "5"),
        ("LU", "site", "2"),
    ]
)


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
    created = await api_space.client.post(
        _url(api_space), json={"label": "Transporteur", "slug": "transporteur", "format": "csv"}
    )
    assert created.status_code == 201, created.text
    await api_space.client.put(
        _url(api_space, "/transporteur/lecture"),
        json={"separator": ";", "encoding": "utf-8", "header": True},
    )
    await api_space.client.put(
        _url(api_space, "/transporteur/template"), json={"template": TEMPLATE}
    )
    return api_space


@pytest.fixture
async def anonymous(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


def _url(space: ApiSpace, rest: str = "") -> str:
    return f"{space.base}/referentiels/delais/imports{rest}"


async def _import(
    space: ApiSpace, content: bytes | None, mode: str = "merge", name: str = "t.csv"
) -> Any:
    files = {"file": (name, content, "text/csv")} if content is not None else None
    return await space.client.post(
        _url(space, "/transporteur/import"), data={"mode": mode}, files=files
    )


async def _values(space: ApiSpace) -> list[dict[str, Any]]:
    body = (await space.client.get(f"{space.base}/referentiels/delais/valeurs")).json()
    values: list[dict[str, Any]] = body["values"]
    return values


async def _journal(app: FastAPI) -> list[Any]:
    async with app.state.pool.acquire() as conn:
        return list(
            await conn.fetch(
                "SELECT actor_label, object_label, detail FROM audit_log "
                "WHERE object_type = 'referentiel_import' ORDER BY id"
            )
        )


# --- Critère 1 : fusion par clé ------------------------------------------------------------


async def test_fusion_ajoute_et_met_a_jour_le_reste_inchange(space: ApiSpace) -> None:
    done = await _import(space, TEN)
    assert done.status_code == 200, done.text
    body = done.json()
    assert (body["new"], body["modified"], body["unchanged"], body["deleted"]) == (3, 2, 5, 0)
    assert body["revision"]
    assert body["mode"] == "merge"
    assert body["file"] == "t.csv"
    values = {(v["pays"], v["mode"]): v["delai"] for v in await _values(space)}
    assert len(values) == 10
    assert values[("FR", "site")] == 2
    assert values[("BE", "domicile")] == 5
    assert values[("DE", "site")] == 1


async def test_sans_fichier_l_import_porte_sur_le_fichier_d_exemple(space: ApiSpace) -> None:
    await space.client.put(
        _url(space, "/transporteur/exemple"), files={"file": ("e.csv", TEN, "text/csv")}
    )
    body = (await _import(space, None)).json()
    assert body["file"] == "transporteur.exemple.csv"
    assert body["new"] == 3


# --- Critère 2 : remplacement ----------------------------------------------------------------


async def test_remplacement_donne_exactement_les_valeurs_du_fichier(space: ApiSpace) -> None:
    two = _csv([("FR", "site", "9"), ("LU", "site", "2")])
    # L'essai, sur le même fichier en exemple, annonce déjà les suppressions.
    await space.client.put(
        _url(space, "/transporteur/exemple"), files={"file": ("e.csv", two, "text/csv")}
    )
    trial = await space.client.post(
        _url(space, "/transporteur/essai"), json={"template": TEMPLATE, "mode": "replace"}
    )
    assert trial.json()["deleted"] == 6
    body = (await _import(space, two, "replace")).json()
    assert (body["new"], body["modified"], body["deleted"]) == (1, 1, 6)
    assert {d["kind"] for d in body["differences"]} == {"new", "modified", "deleted"}
    assert await _values(space) == [
        {"delai": 9, "mode": "site", "pays": "FR"},
        {"delai": 2, "mode": "site", "pays": "LU"},
    ]


# --- Critère 3 : tout ou rien ----------------------------------------------------------------


async def test_une_erreur_bloque_tout_et_la_ligne_est_donnee(app: FastAPI, space: ApiSpace) -> None:
    bad = _csv([("LU", "site", "2"), ("FR", "site", "x"), ("LU", "site", "3")])
    refused = await _import(space, bad)
    assert refused.status_code == 422
    body = refused.json()
    assert body["error"] == "import_refused"
    report = body["error_args"]["report"]
    wheres = {(e["where"], e["code"]) for e in report["error_details"]}
    assert ("ligne 3", "template_conversion") in wheres
    # La clé LU+site deux fois dans le fichier.
    assert any(e["code"] == "key_duplicate" for e in report["error_details"])
    assert len(await _values(space)) == len(VALUES)
    [row] = await _journal(app)
    assert row["detail"]["outcome"] == "refused"


async def test_fichier_vide_refuse(space: ApiSpace) -> None:
    empty = await _import(space, b"Pays;Mode;Delai\n")
    assert empty.status_code == 400
    assert empty.json()["error"] == "import_file_empty"


# --- Critère 4 : même fichier deux fois, pas de second changement ----------------------------


async def test_meme_fichier_deux_fois_pas_de_second_changement(space: ApiSpace) -> None:
    first = (await _import(space, TEN)).json()
    second = (await _import(space, TEN)).json()
    assert first["revision"]
    assert second["revision"] is None
    assert (second["new"], second["modified"], second["unchanged"]) == (0, 0, 10)


# --- Critère 5 : web API, CSV puis JSON, authentification, 404 -------------------------------


async def test_web_api_json_meme_resultat(space: ApiSpace) -> None:
    created = await space.client.post(
        _url(space), json={"label": "API", "slug": "api", "format": "json"}
    )
    assert created.status_code == 201
    await space.client.put(
        _url(space, "/api/template"),
        json={"template": '{"pays": $.code.pays, "mode": $.code.mode, "delai": $.jours}\n'},
    )
    data = [
        {"code": {"pays": "FR", "mode": "site"}, "jours": 2},
        {"code": {"pays": "LU", "mode": "site"}, "jours": 4},
    ]
    done = await space.client.post(
        _url(space, "/api/import"),
        files={"file": ("a.json", json.dumps(data).encode(), "application/json")},
    )
    assert done.status_code == 200, done.text
    assert (done.json()["new"], done.json()["modified"]) == (1, 1)


async def test_sans_authentification_refus(space: ApiSpace, anonymous: AsyncClient) -> None:
    refused = await anonymous.post(
        _url(space, "/transporteur/import"), files={"file": ("t.csv", TEN, "text/csv")}
    )
    assert refused.status_code == 401


async def test_une_cle_d_api_ne_peut_pas_importer(
    app: FastAPI, space: ApiSpace, anonymous: AsyncClient
) -> None:
    """Gael : « Les api sur les référentiels sont en lecture seules » — l'import
    se fait à l'écran, par une personne connectée ; une clé d'API, même dotée de
    toutes les lectures des référentiels, est refusée."""
    key = await _key(
        app,
        space.client,
        ["list_documents", "read_document", "read_schema"],
    )
    refused = await anonymous.post(
        _url(space, "/transporteur/import"),
        files={"file": ("t.csv", TEN, "text/csv")},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert refused.status_code == 401
    assert len(await _values(space)) == len(VALUES)


async def test_import_ou_referentiel_inconnu_404(space: ApiSpace) -> None:
    missing = await space.client.post(
        _url(space, "/inconnu/import"), files={"file": ("t.csv", TEN, "text/csv")}
    )
    assert missing.status_code == 404
    assert missing.json()["error"] == "import_not_found"
    other = await space.client.post(
        f"{space.base}/referentiels/absent/imports/transporteur/import",
        files={"file": ("t.csv", TEN, "text/csv")},
    )
    assert other.status_code == 404


# --- Critère 6 : branche racine en lecture seule ---------------------------------------------


async def test_racine_en_lecture_seule(app: FastAPI, space: ApiSpace) -> None:
    async with app.state.pool.acquire() as conn:
        root = await db_create_branch(
            conn, uuid.UUID(space.space_id), "main", "main", KIND_CHILD, git_ref="main"
        )
    url = (
        f"/api/spaces/{space.space_id}/branches/{root.id}"
        "/referentiels/delais/imports/transporteur/import"
    )
    forge_edit(
        space,
        {
            model_path("delais"): dump_model(MODEL),
            values_path("delais"): dump_values(MODEL, VALUES),
            "referentiels/delais/imports/transporteur.yaml": (
                "label: Transporteur\nformat: csv\nseparator: ';'\n"
            ),
            "referentiels/delais/imports/transporteur.jslt": TEMPLATE,
        },
        ref="main",
    )
    # Bug e7deb2b9 : plus de refus « branche racine » (brouillon de la personne).
    answer = await space.client.post(url, files={"file": ("t.csv", TEN, "text/csv")})
    assert answer.json().get("error") != "branch_root_readonly", answer.text


# --- Critère 7 : journal et date du dernier import -------------------------------------------


async def test_journal_et_date_du_dernier_import(app: FastAPI, space: ApiSpace) -> None:
    before = (await space.client.get(_url(space))).json()["imports"][0]
    assert before["last_import"] is None
    await _import(space, TEN, name="transporteur-novembre.csv")
    await _import(space, TEN, name="transporteur-novembre.csv")
    first, second = await _journal(app)
    assert first["actor_label"].endswith(">") or "@" in first["actor_label"]
    assert first["object_label"] == f"delais{SEPARATOR}transporteur"
    assert first["detail"] == {
        "outcome": "imported",
        "mode": "merge",
        "file": "transporteur-novembre.csv",
        "lines": 10,
        "new": 3,
        "modified": 2,
        "unchanged": 5,
        "deleted": 0,
        "errors": 0,
        "revision": first["detail"]["revision"],
    }
    assert second["detail"]["outcome"] == "unchanged"
    listing = (await space.client.get(_url(space))).json()["imports"][0]
    assert listing["last_import"]
    assert listing["last_import_by"] == first["actor_label"]
    detail = (await space.client.get(_url(space, "/transporteur"))).json()
    assert detail["last_import"] == listing["last_import"]
    history = (await space.client.get("/api/audit", params={"type": "referentiel_import"})).json()
    assert len(history["events"]) == 2
