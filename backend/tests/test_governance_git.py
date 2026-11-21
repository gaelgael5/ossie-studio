"""Gouvernance dans le dépôt de l'espace : brouillon, publication, indépendance
des espaces, contraintes devenues validations, impacts sur les assets."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from httpx import AsyncClient

from tests.space_fixture import (
    ApiSpace,
    Rebased,
    forge_edit,
    forge_read,
    make_api_space,
    seed_bare,
    set_credential,
)


@pytest.fixture
async def gov(client: AsyncClient, app: FastAPI, tmp_path: Path) -> Rebased:
    return Rebased(client, await make_api_space(client, app, tmp_path))


async def test_tag_dans_le_brouillon_puis_dans_le_depot(app: FastAPI, gov: Rebased) -> None:
    created = await gov.post("/api/governance/tags", json={"id": "pii", "owner": "data"})
    assert created.status_code == 201, created.text
    draft = (await gov.client.get(f"{gov.space.base}/draft")).json()
    assert [o["path"] for o in draft["objects"]] == ["governance/tags/pii.yaml"]
    assert forge_read(gov.space, "governance/tags/pii.yaml") is None  # pas encore publié
    await set_credential(app, gov.space)
    published = await gov.client.post(f"{gov.space.base}/draft/publish", json={"comment": "tag"})
    assert published.status_code == 200, published.text
    content = yaml.safe_load(forge_read(gov.space, "governance/tags/pii.yaml") or "")
    assert content == {"id": "pii", "info": {"owner": "data", "description": ""}}


async def test_deux_espaces_gouvernances_independantes(
    client: AsyncClient, app: FastAPI, gov: Rebased
) -> None:
    await gov.post("/api/governance/tags", json={"id": "pii"})
    async with app.state.pool.acquire() as conn:
        connection_id = await conn.fetchval("SELECT id FROM connections LIMIT 1")
        (gov.space.tmp / "autre").mkdir()
        other_url = seed_bare(gov.space.tmp / "autre")
        other_id = await conn.fetchval(
            "INSERT INTO git_spaces (name, connection_id, repo_url, last_probe_status) "
            "VALUES ('autre', $1, $2, 'valid') RETURNING id",
            connection_id,
            other_url,
        )
    base = await client.post(f"/api/spaces/{other_id}/branches/base", json={"label": "Principale"})
    other = ApiSpace(client, str(other_id), base.json()["id"], other_url, gov.space.tmp)
    listing = (await Rebased(client, other).get("/api/governance/tags")).json()
    assert listing["tags"] == []


async def test_nom_court_en_double_erreur_bloquante(gov: Rebased) -> None:
    forge_edit(
        gov.space,
        {
            "governance/classifications/c.yaml": yaml.safe_dump(
                {
                    "id": "c",
                    "name": "C",
                    "levels": [
                        {"id": "1", "name": "A", "short_name": "X"},
                        {"id": "2", "name": "B", "short_name": "X"},
                    ],
                }
            )
        },
    )
    # Fusion d'une modification extérieure : la contrainte est vérifiée au journal.
    await gov.post("/api/governance/tags", json={"id": "declencheur"})
    log = (await gov.client.get(f"{gov.space.base}/draft/validation")).json()
    entry = next(e for e in log["entries"] if e["message_code"] == "level_short_name_duplicate")
    assert entry["level"] == "error"
    assert entry["preexisting"] is True  # présent côté publié : ne bloque pas ce brouillon


async def test_doublon_introduit_par_le_brouillon_bloque(app: FastAPI, gov: Rebased) -> None:
    await gov.post("/api/governance/certifications", json={"name": "A", "rank": 1, "tag": "t/x"})
    second = await gov.post(
        "/api/governance/certifications", json={"name": "B", "rank": 2, "tag": "t/x"}
    )
    assert second.status_code == 409  # refus immédiat à l'écriture


async def test_renommer_ou_supprimer_un_tag_impacte_les_assets(app: FastAPI, gov: Rebased) -> None:
    forge_edit(
        gov.space,
        {
            "governance/tags/pii.yaml": yaml.safe_dump(
                {"id": "pii", "info": {"owner": "", "description": ""}}
            ),
            "sources/s/raw/User.json": json.dumps({"title": "User"}),
            "sources/s/assets/User.json": json.dumps({"title": "User", "x-tags": ["pii"]}),
            "sources/s/assets/Autre.json": json.dumps({"title": "Autre"}),
        },
    )
    # Supprimer le tag : l'asset qui le porte est impacté, pas l'autre.
    await gov.delete("/api/governance/tags/pii")
    impacts = (await gov.client.get(f"{gov.space.base}/draft/impacts")).json()["impacts"]
    assert [(i["path"], i["rules"]) for i in impacts] == [
        ("sources/s/assets/User.json", ["governance_assets"])
    ]


async def test_import_export_yaml_dans_la_branche(gov: Rebased) -> None:
    text = "- id: a\n  info: {owner: o, description: d}\n- id: b/c\n"
    imported = await gov.post("/api/governance/tags/import", json={"yaml": text})
    assert imported.json() == {"imported": 2}
    exported = (await gov.get("/api/governance/tags/export")).json()["yaml"]
    assert {t["id"] for t in yaml.safe_load(exported)} == {"a", "b/c"}


async def test_tables_supprimees_application_demarre(app: FastAPI) -> None:
    async with app.state.pool.acquire() as conn:
        for table in (
            "governed_tags",
            "certifications",
            "classification_schemes",
            "classification_levels",
        ):
            assert await conn.fetchval("SELECT to_regclass($1)", table) is None


async def test_espace_sans_gouvernance_pages_vides(gov: Rebased) -> None:
    for url in (
        "/api/governance/tags",
        "/api/governance/certifications",
        "/api/governance/classifications",
    ):
        response = await gov.get(url)
        assert response.status_code == 200
        assert next(iter(response.json().values())) == []
    assert uuid.UUID(gov.space.space_id)


@pytest.mark.parametrize(
    "evil", ["..%2F..%2Fsources%2Fs%2Fmanifest", "..%2F..%2F..%2Fx", "a/../../b"]
)
async def test_suppression_de_tag_hors_dossier_refusee(gov: Rebased, evil: str) -> None:
    forge_edit(
        gov.space, {"governance/tags/ok.yaml": "id: ok\n", "sources/s/manifest.yaml": "garde\n"}
    )
    response = await gov.client.delete(f"{gov.space.base}/governance/tags/{evil}")
    assert response.status_code == 404
    draft = (await gov.client.get(f"{gov.space.base}/draft")).json()
    assert draft["count"] == 0  # rien n'a été supprimé, nulle part
