from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.governance.tags import (
    normalize_tag_id,
    tag_prefixes,
    validate_tag_fields,
)
from ossie_studio.governance.yaml_io import (
    TagImportError,
    export_tags_yaml,
    parse_tags_yaml,
)
from ossie_studio.models import GovernedTag
from tests.conftest import bootstrap_admin, create_plain_user, login

# --- Partie pure (normalisation, hiérarchie, YAML) ---------------------------

def test_normalisation_insensible_a_la_casse() -> None:
    assert normalize_tag_id("  Gouvernance/PII ") == "gouvernance/pii"


def test_prefixes_hierarchiques() -> None:
    assert tag_prefixes("gouvernance/pii/x") == [
        "gouvernance",
        "gouvernance/pii",
        "gouvernance/pii/x",
    ]


def test_validation_id_et_champs() -> None:
    assert validate_tag_fields("gouvernance/pii", {}, {}) == []
    assert "tag_id_required" in validate_tag_fields("", {}, {})
    assert "tag_id_invalid" in validate_tag_fields("Mauvais Tag", {}, {})
    assert "tag_links_invalid" in validate_tag_fields("ok", {"n": 3}, {})
    assert "tag_properties_invalid" in validate_tag_fields("ok", {}, {"k": {"nested": 1}})


def test_yaml_aller_retour() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    tag = GovernedTag(
        id="gouvernance/donnees-personnelles",
        owner="equipe-data",
        description="Objet qui contient des données personnelles.",
        links={"politique": "https://exemple.org/politique"},
        properties={"conservationJours": 90},
        created_at=now,
        updated_at=now,
    )
    text = export_tags_yaml([tag])
    parsed = parse_tags_yaml(text)
    assert len(parsed) == 1
    assert parsed[0].id == tag.id
    assert parsed[0].owner == "equipe-data"
    assert parsed[0].properties["conservationJours"] == 90
    assert parsed[0].links["politique"] == "https://exemple.org/politique"


def test_yaml_import_normalise_la_casse() -> None:
    parsed = parse_tags_yaml("- id: Gouvernance/PII\n  info:\n    owner: x\n")
    assert parsed[0].id == "gouvernance/pii"


def test_yaml_import_invalide_rejete() -> None:
    with pytest.raises(TagImportError):
        parse_tags_yaml("- id: 'Tag Invalide'\n")


# --- API ---------------------------------------------------------------------

@pytest.fixture
async def admin(client: AsyncClient) -> AsyncClient:
    await bootstrap_admin(client)
    return client


async def test_creer_et_lister_un_tag(admin: AsyncClient) -> None:
    created = await admin.post(
        "/api/governance/tags",
        json={
            "id": "Gouvernance/Donnees-Personnelles",
            "owner": "equipe-data",
            "description": "PII",
            "links": {"politique": "https://exemple.org/p"},
            "properties": {"conservationJours": 90},
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["id"] == "gouvernance/donnees-personnelles"  # normalisé
    assert body["properties"]["conservationJours"] == 90

    listing = await admin.get("/api/governance/tags")
    assert [t["id"] for t in listing.json()["tags"]] == ["gouvernance/donnees-personnelles"]


async def test_casse_designe_le_meme_tag(admin: AsyncClient) -> None:
    await admin.post("/api/governance/tags", json={"id": "Gouvernance/PII"})
    again = await admin.post("/api/governance/tags", json={"id": "gouvernance/pii"})
    assert again.status_code == 409
    assert again.json()["error"] == "tag_id_taken"


async def test_id_invalide_refuse(admin: AsyncClient) -> None:
    response = await admin.post("/api/governance/tags", json={"id": "tag invalide"})
    assert response.status_code == 400
    assert "tag_id_invalid" in response.json()["error_args"]["fields"]


async def test_modifier_et_supprimer(admin: AsyncClient) -> None:
    await admin.post("/api/governance/tags", json={"id": "gouvernance/pii", "owner": "a"})
    updated = await admin.put(
        "/api/governance/tags/gouvernance/pii", json={"owner": "equipe-data", "description": "d"}
    )
    assert updated.status_code == 200
    assert updated.json()["owner"] == "equipe-data"

    deleted = await admin.delete("/api/governance/tags/gouvernance/pii")
    assert deleted.status_code == 204
    assert (await admin.get("/api/governance/tags")).json()["tags"] == []
    # Supprimer un tag absent → 404.
    assert (await admin.delete("/api/governance/tags/gouvernance/pii")).status_code == 404


async def test_export_puis_import(admin: AsyncClient) -> None:
    await admin.post(
        "/api/governance/tags",
        json={"id": "gouvernance/pii", "owner": "equipe-data", "description": "PII"},
    )
    exported = await admin.get("/api/governance/tags/export")
    assert exported.status_code == 200
    text = exported.json()["yaml"]
    assert "gouvernance/pii" in text

    # On vide puis on réimporte : le vocabulaire revient.
    await admin.delete("/api/governance/tags/gouvernance/pii")
    imported = await admin.post("/api/governance/tags/import", json={"yaml": text})
    assert imported.status_code == 200
    assert imported.json()["imported"] == 1
    assert [t["id"] for t in (await admin.get("/api/governance/tags")).json()["tags"]] == [
        "gouvernance/pii"
    ]


async def test_import_invalide_refuse(admin: AsyncClient) -> None:
    response = await admin.post(
        "/api/governance/tags/import", json={"yaml": "- id: 'Tag Invalide'\n"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "tag_import_invalid"


@pytest.fixture
async def user_client(admin: AsyncClient, app: FastAPI) -> AsyncIterator[AsyncClient]:
    await create_plain_user(app, "simple@example.org", "mot-de-passe-simple")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as second:
        await login(second, "simple@example.org", "mot-de-passe-simple")
        yield second


async def test_non_admin_refuse(user_client: AsyncClient) -> None:
    assert (await user_client.get("/api/governance/tags")).status_code == 403
    created = await user_client.post("/api/governance/tags", json={"id": "x"})
    assert created.status_code == 403
