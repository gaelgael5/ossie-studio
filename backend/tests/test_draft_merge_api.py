"""Routes de fusion : trois versions, résolution, fusion automatique."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.drafts.workdir import DraftWorkdir
from tests.space_fixture import ApiSpace, forge_edit, make_api_space

OBJ = "sources/s/raw/O.json"


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    return await make_api_space(client, app, tmp_path)


async def _admin_workdir(app: FastAPI, sp: ApiSpace) -> DraftWorkdir:
    async with app.state.pool.acquire() as conn:
        admin_id = await conn.fetchval("SELECT id FROM users WHERE is_bootstrap")
    return DraftWorkdir(
        app.state.workdir_manager,
        app.state.draft_views,
        app.state.pool,
        admin_id,
        uuid.UUID(sp.branch_id),
    )


async def _draft_write(app: FastAPI, sp: ApiSpace, rel: str, value: object | None) -> None:
    workdir = await _admin_workdir(app, sp)

    def write(root: Path) -> None:
        target = root / rel
        if value is None:
            target.unlink()
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value))

    await workdir.commit_to_branch(
        uuid.UUID(sp.space_id), "principale", sp.url, "x", None, "a", "a", "écrit", write
    )


async def _draft_content(sp: ApiSpace) -> dict[str, object]:
    return (await sp.client.get(f"{sp.base}/draft")).json()


async def test_lecture_seule_sans_changement_publie(app: FastAPI, sp: ApiSpace) -> None:
    forge_edit(sp, {OBJ: json.dumps({"a": 1})})
    await _draft_write(app, sp, OBJ, {"a": 2})
    view = (await sp.client.get(f"{sp.base}/draft/merge", params={"path": OBJ})).json()
    assert view["readonly"] is True
    assert view["kind"] == "none"
    assert (view["theirs"], view["mine"]) == ({"a": 1}, {"a": 2})


async def test_fusion_automatique_sans_conflit(app: FastAPI, sp: ApiSpace) -> None:
    forge_edit(sp, {OBJ: json.dumps({"a": 1, "b": 1})})
    await _draft_write(app, sp, OBJ, {"a": 1, "b": 2})
    forge_edit(sp, {OBJ: json.dumps({"a": 9, "b": 1})})
    view = (await sp.client.get(f"{sp.base}/draft/merge", params={"path": OBJ})).json()
    assert view["kind"] == "auto"
    assert view["proposed"] == {"a": 9, "b": 2}
    auto = (await sp.client.post(f"{sp.base}/draft/automerge")).json()
    assert auto == {"merged": [OBJ], "conflicts": []}
    draft = await _draft_content(sp)
    assert draft["changed_upstream_count"] == 0


async def test_conflit_resolu_par_choix(app: FastAPI, sp: ApiSpace) -> None:
    forge_edit(sp, {OBJ: json.dumps({"a": 1, "b": 1})})
    await _draft_write(app, sp, OBJ, {"a": 2, "b": 1})
    forge_edit(sp, {OBJ: json.dumps({"a": 3, "b": 1})})
    view = (await sp.client.get(f"{sp.base}/draft/merge", params={"path": OBJ})).json()
    assert view["kind"] == "conflict"
    assert view["conflicts"] == [{"pointer": "/a", "base": 1, "theirs": 3, "mine": 2}]
    auto = (await sp.client.post(f"{sp.base}/draft/automerge")).json()
    assert auto == {"merged": [], "conflicts": [OBJ]}
    missing = await sp.client.post(f"{sp.base}/draft/merge", json={"path": OBJ})
    assert missing.status_code == 400
    assert missing.json()["error"] == "merge_unresolved"
    done = await sp.client.post(
        f"{sp.base}/draft/merge", json={"path": OBJ, "resolutions": {"/a": {"value": 42}}}
    )
    assert done.status_code == 200, done.text
    draft = await _draft_content(sp)
    assert draft["changed_upstream_count"] == 0
    after = (await sp.client.get(f"{sp.base}/draft/merge", params={"path": OBJ})).json()
    assert after["mine"] == {"a": 42, "b": 1}


async def test_choisir_le_publie_retire_l_objet_du_brouillon(app: FastAPI, sp: ApiSpace) -> None:
    forge_edit(sp, {OBJ: json.dumps({"a": 1})})
    await _draft_write(app, sp, OBJ, {"a": 2})
    forge_edit(sp, {OBJ: json.dumps({"a": 3})})
    await sp.client.post(
        f"{sp.base}/draft/merge", json={"path": OBJ, "resolutions": {"/a": "theirs"}}
    )
    assert (await _draft_content(sp))["count"] == 0


async def test_supprime_cote_publie_garder_ou_supprimer(app: FastAPI, sp: ApiSpace) -> None:
    forge_edit(sp, {OBJ: json.dumps({"a": 1})})
    await _draft_write(app, sp, OBJ, {"a": 2})
    forge_edit(sp, {OBJ: None})
    view = (await sp.client.get(f"{sp.base}/draft/merge", params={"path": OBJ})).json()
    assert view["kind"] == "deleted_upstream"
    assert view["theirs_absent"] is True
    keep = await sp.client.post(f"{sp.base}/draft/merge", json={"path": OBJ, "delete": False})
    assert keep.status_code == 200, keep.text
    draft = await _draft_content(sp)
    assert draft["objects"] == [{"path": OBJ, "state": "created", "changed_upstream": False}]


async def test_supprime_cote_publie_accepter_la_suppression(app: FastAPI, sp: ApiSpace) -> None:
    forge_edit(sp, {OBJ: json.dumps({"a": 1})})
    await _draft_write(app, sp, OBJ, {"a": 2})
    forge_edit(sp, {OBJ: None})
    await sp.client.post(f"{sp.base}/draft/merge", json={"path": OBJ, "delete": True})
    assert (await _draft_content(sp))["count"] == 0


async def test_fermer_sans_valider_brouillon_inchange(app: FastAPI, sp: ApiSpace) -> None:
    forge_edit(sp, {OBJ: json.dumps({"a": 1})})
    await _draft_write(app, sp, OBJ, {"a": 2})
    forge_edit(sp, {OBJ: json.dumps({"a": 3})})
    before = await _draft_content(sp)
    await sp.client.get(f"{sp.base}/draft/merge", params={"path": OBJ})
    assert await _draft_content(sp) == before


async def test_objet_hors_brouillon_404(app: FastAPI, sp: ApiSpace) -> None:
    await _draft_write(app, sp, OBJ, {"a": 2})
    response = await sp.client.get(f"{sp.base}/draft/merge", params={"path": "autre.json"})
    assert response.status_code == 404


# --- Impacts (même fixture d'espace) -------------------------------------------

RAW = "sources/s/raw/User.json"
ASSET = "sources/s/assets/User.json"


async def test_impacts_puis_verifie(app: FastAPI, sp: ApiSpace) -> None:
    forge_edit(sp, {RAW: json.dumps({"title": "User"}), ASSET: json.dumps({"title": "User"})})
    await _draft_write(app, sp, RAW, {"title": "User", "v": 2})
    impacts = (await sp.client.get(f"{sp.base}/draft/impacts")).json()
    assert impacts == {
        "impacts": [{"path": ASSET, "reads": [RAW], "rules": ["raw_asset"], "verified": False}],
        "pending": 1,
    }
    ok = await sp.client.post(f"{sp.base}/draft/impacts/verify", json={"path": ASSET})
    assert ok.status_code == 200, ok.text
    assert (await sp.client.get(f"{sp.base}/draft/impacts")).json()["pending"] == 0
    # L'objet lu change encore : la vérification tombe.
    await _draft_write(app, sp, RAW, {"title": "User", "v": 3})
    assert (await sp.client.get(f"{sp.base}/draft/impacts")).json()["pending"] == 1


async def test_corriger_l_impact_le_fait_entrer_au_brouillon(app: FastAPI, sp: ApiSpace) -> None:
    forge_edit(sp, {RAW: json.dumps({"title": "User"}), ASSET: json.dumps({"title": "User"})})
    await _draft_write(app, sp, RAW, {"title": "User", "v": 2})
    await _draft_write(app, sp, ASSET, {"title": "User", "corrige": True})
    assert (await sp.client.get(f"{sp.base}/draft/impacts")).json() == {"impacts": [], "pending": 0}


async def test_verifier_un_impact_inconnu_404(app: FastAPI, sp: ApiSpace) -> None:
    await _draft_write(app, sp, RAW, {"title": "User"})
    response = await sp.client.post(f"{sp.base}/draft/impacts/verify", json={"path": "x.json"})
    assert response.status_code == 404


# --- Journal de validation ----------------------------------------------------


async def test_journal_erreur_liee_au_brouillon_et_avertissement(
    app: FastAPI, sp: ApiSpace
) -> None:
    forge_edit(sp, {RAW: json.dumps({"title": "User"})})
    # Le brouillon supprime la cible d'un $ref et laisse un asset sans son raw.
    await _draft_write(
        app, sp, "sources/s/raw/Order.json", {"properties": {"u": {"$ref": "Absent.json"}}}
    )
    await _draft_write(app, sp, "sources/s/assets/Seul.json", {"title": "Seul"})
    log = (await sp.client.get(f"{sp.base}/draft/validation")).json()
    rules = {(e["rule"], e["level"], e["blocking"]) for e in log["entries"]}
    assert ("broken_ref", "error", True) in rules
    assert ("orphan_asset", "warning", False) in rules
    assert log["blocking"] == 1
    warning = next(e for e in log["entries"] if e["rule"] == "orphan_asset")
    ok = await sp.client.post(
        f"{sp.base}/draft/validation/accept",
        json={"rule": "orphan_asset", "path": warning["path"], "message_code": "asset_orphan"},
    )
    assert ok.status_code == 200, ok.text
    log = (await sp.client.get(f"{sp.base}/draft/validation")).json()
    assert next(e for e in log["entries"] if e["rule"] == "orphan_asset")["accepted"] is True


async def test_accepter_une_erreur_refuse(app: FastAPI, sp: ApiSpace) -> None:
    await _draft_write(
        app, sp, "sources/s/raw/Order.json", {"properties": {"u": {"$ref": "A.json"}}}
    )
    response = await sp.client.post(
        f"{sp.base}/draft/validation/accept",
        json={
            "rule": "broken_ref",
            "path": "sources/s/raw/Order.json",
            "message_code": "ref_broken",
        },
    )
    assert response.status_code == 404
