from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.conftest import create_plain_user, login
from tests.space_fixture import Rebased, make_api_space


@pytest.fixture
async def admin(client: AsyncClient, app: FastAPI, tmp_path: Path) -> Rebased:
    # La gouvernance vit dans le dépôt de l'espace : routes de la branche.
    return Rebased(client, await make_api_space(client, app, tmp_path))


def _schemes(payload: dict) -> list[dict]:
    return payload["schemes"]


async def test_espace_neuf_sans_gouvernance(admin: Rebased) -> None:
    # Plus de schéma installé d'office : la gouvernance d'un espace vit dans son
    # dépôt, elle commence vide (et s'importe au besoin).
    listing = await admin.get("/api/governance/classifications")
    assert listing.status_code == 200
    assert _schemes(listing.json()) == []


SCHEME_YAML = """id: classification
name: Classification
is_primary: true
levels:
  - {name: Public, short_name: PUB, rank: 1}
  - {name: Interne, short_name: INT, rank: 2}
  - {name: Confidentiel, short_name: CONF, rank: 3}
  - {name: Restreint, short_name: RESTR, rank: 4}
"""


async def test_creer_schema_et_gerer_niveaux(admin: Rebased) -> None:
    created = await admin.post(
        "/api/governance/classifications", json={"name": "Sensibilité"}
    )
    assert created.status_code == 201, created.text
    scheme_id = created.json()["id"]
    assert scheme_id == "sensibilite"  # id dérivé du nom

    a = await admin.post(
        f"/api/governance/classifications/{scheme_id}/levels",
        json={"name": "Bas", "short_name": "BAS", "rank": 1},
    )
    b = await admin.post(
        f"/api/governance/classifications/{scheme_id}/levels",
        json={"name": "Haut", "short_name": "HAUT", "rank": 2},
    )
    assert a.status_code == 201 and b.status_code == 201
    # Réordonner : Haut avant Bas.
    reorder = await admin.put(
        f"/api/governance/classifications/{scheme_id}/levels",
        json={"order": [b.json()["id"], a.json()["id"]]},
    )
    assert reorder.status_code == 200
    listing = (await admin.get("/api/governance/classifications")).json()
    scheme = next(s for s in _schemes(listing) if s["id"] == scheme_id)
    assert [lv["name"] for lv in scheme["levels"]] == ["Haut", "Bas"]
    # Supprimer un niveau.
    assert (
        await admin.delete(f"/api/governance/classifications/{scheme_id}/levels/{a.json()['id']}")
    ).status_code == 204


async def test_id_immuable_au_renommage(admin: Rebased) -> None:
    created = await admin.post("/api/governance/classifications", json={"name": "Sensibilité"})
    scheme_id = created.json()["id"]
    updated = await admin.put(
        f"/api/governance/classifications/{scheme_id}",
        json={"name": "Tout autre nom", "display_order": 2},
    )
    assert updated.status_code == 200
    assert updated.json()["id"] == scheme_id
    assert updated.json()["name"] == "Tout autre nom"


async def test_import_id_existant_refuse(admin: Rebased) -> None:
    await admin.post("/api/governance/classifications/import", json={"yaml": SCHEME_YAML})
    response = await admin.post(
        "/api/governance/classifications", json={"id": "classification", "name": "Doublon"}
    )
    assert response.status_code == 409
    assert response.json()["error"] == "scheme_id_taken"


async def test_niveaux_uniques_et_sans_virgule(admin: Rebased) -> None:
    created = await admin.post("/api/governance/classifications", json={"name": "Éch"})
    scheme_id = created.json()["id"]
    await admin.post(
        f"/api/governance/classifications/{scheme_id}/levels",
        json={"name": "A", "short_name": "A"},
    )
    same_name = await admin.post(
        f"/api/governance/classifications/{scheme_id}/levels",
        json={"name": "A", "short_name": "B"},
    )
    assert same_name.status_code == 409
    assert same_name.json()["error"] == "level_name_taken"
    same_short = await admin.post(
        f"/api/governance/classifications/{scheme_id}/levels",
        json={"name": "C", "short_name": "A"},
    )
    assert same_short.json()["error"] == "level_short_name_taken"
    comma = await admin.post(
        f"/api/governance/classifications/{scheme_id}/levels",
        json={"name": "X, Y", "short_name": "XY"},
    )
    assert "level_name_comma" in comma.json()["error_args"]["fields"]


async def test_multivaleurs_pas_principal(admin: Rebased) -> None:
    response = await admin.post(
        "/api/governance/classifications",
        json={"name": "Catégories", "multi_value": True, "is_primary": True},
    )
    assert response.status_code == 400
    assert "scheme_primary_multivalue" in response.json()["error_args"]["fields"]


async def test_un_seul_schema_principal(admin: Rebased) -> None:
    # Autrefois garanti par la base ; désormais une erreur de validation qui
    # bloque la publication.
    await admin.post("/api/governance/classifications/import", json={"yaml": SCHEME_YAML})
    second = await admin.post(
        "/api/governance/classifications", json={"name": "Nouveau principal", "is_primary": True}
    )
    assert second.status_code == 201
    log = (await admin.client.get(f"{admin.space.base}/draft/validation")).json()
    codes = {(e["message_code"], e["blocking"]) for e in log["entries"]}
    assert ("classification_primary_duplicate", True) in codes


async def test_export_puis_import_yaml(admin: Rebased) -> None:
    await admin.post("/api/governance/classifications/import", json={"yaml": SCHEME_YAML})
    exported = await admin.get("/api/governance/classifications/classification/export")
    assert exported.status_code == 200
    text = exported.json()["yaml"].replace("id: classification", "id: tlp").replace(
        "name: Classification", "name: TLP"
    )
    imported = await admin.post("/api/governance/classifications/import", json={"yaml": text})
    assert imported.status_code == 201, imported.text
    assert imported.json()["id"] == "tlp"
    listing = (await admin.get("/api/governance/classifications")).json()
    assert any(s["id"] == "tlp" and len(s["levels"]) == 4 for s in _schemes(listing))


@pytest.fixture
async def user_client(admin: Rebased, app: FastAPI) -> AsyncIterator[Rebased]:
    await create_plain_user(app, "simple@example.org", "mot-de-passe-simple")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as second:
        await login(second, "simple@example.org", "mot-de-passe-simple")
        yield Rebased(second, admin.space)


async def test_non_admin_refuse(user_client: Rebased) -> None:
    assert (await user_client.get("/api/governance/classifications")).status_code == 403
    created = await user_client.post("/api/governance/classifications", json={"name": "X"})
    assert created.status_code == 403
