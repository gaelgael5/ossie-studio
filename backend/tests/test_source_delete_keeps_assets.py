"""Supprimer une source garde ses assets, qui apparaissent orphelins dans la
Curation (ticket docflow ce21a9e8, Gael : « si je supprime les raws, je dois voir
les orphelins sur les assets »). Effacer tout le dossier avait fait perdre ses
assets et leur travail humain à Gael (bug 206d434c)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.sources.explorer import clear_caches
from ossie_studio.sources.manifest import parse_manifest
from ossie_studio.sources.service import (
    SourceExistsError,
    _remove_source_fn,
    _write_manifest_fn,
    read_sources_tree,
)
from tests.space_fixture import ApiSpace, forge_edit, make_api_space
from tests.test_curation_assets import _files
from tests.test_raw_explorer import _manifest


def _source(root: Path, with_asset: bool) -> Path:
    src = root / "sources" / "app"
    (src / "raw").mkdir(parents=True)
    (src / "manifest.yaml").write_text(_manifest("app"), encoding="utf-8")
    (src / "raw" / "order.schema.yaml").write_text("{}", encoding="utf-8")
    if with_asset:
        (src / "assets").mkdir()
        (src / "assets" / "order.schema.yaml").write_text("{}", encoding="utf-8")
        (src / "assets" / "order.extend.yaml").write_text("description: x\n", encoding="utf-8")
    return src


def test_suppression_garde_les_assets_et_redeclaration_acceptee(tmp_path: Path) -> None:
    src = _source(tmp_path, with_asset=True)
    _remove_source_fn("app")(tmp_path)
    assert sorted(p.name for p in src.iterdir()) == ["assets"]
    assert (src / "assets" / "order.extend.yaml").read_text(encoding="utf-8") == "description: x\n"
    # La liste des sources ne la montre plus (pas de « manifeste absent »).
    assert read_sources_tree(tmp_path) == []
    # La redéclarer au même slug est accepté et retrouve les assets.
    manifest = parse_manifest(_manifest("app"))
    _write_manifest_fn(manifest, must_be_new=True)(tmp_path)
    assert (src / "manifest.yaml").is_file() and (src / "assets" / "order.schema.yaml").is_file()
    with pytest.raises(SourceExistsError):
        _write_manifest_fn(manifest, must_be_new=True)(tmp_path)


def test_suppression_sans_asset_retire_tout(tmp_path: Path) -> None:
    src = _source(tmp_path, with_asset=False)
    _remove_source_fn("app")(tmp_path)
    assert not src.exists()


@pytest.fixture(autouse=True)
def _fresh() -> None:
    clear_caches()


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    return await make_api_space(client, app, tmp_path)


async def test_assets_orphelins_dans_la_curation_apres_suppression(sp: ApiSpace) -> None:
    forge_edit(sp, _files())
    cur = f"{sp.base}/sources/app/curation"
    r = await sp.client.post(f"{cur}/retain", json={"path": "order.schema.yaml", "fields": None})
    assert r.status_code == 200, r.text
    r = await sp.client.put(
        f"{cur}/enrich", json={"path": "order.schema.yaml", "obj": {"description": "Commande."}}
    )
    assert r.status_code == 200, r.text
    before = (await sp.client.get(f"{sp.base}/curation-assets")).json()["assets"]
    assert [(a["path"], a["orphan"]) for a in before] == [("order.schema.yaml", False)]

    r = await sp.client.delete(f"{sp.base}/sources/app")
    assert r.status_code == 204, r.text
    assert (await sp.client.get(f"{sp.base}/sources")).json()["sources"] == []
    after = (await sp.client.get(f"{sp.base}/curation-assets")).json()["assets"]
    assert [(a["source"], a["path"], a["orphan"], a["described"]) for a in after] == [
        ("app", "order.schema.yaml", True, True)
    ]
