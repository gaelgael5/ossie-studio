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

OBJ = "sources/s/assets/O.schema.yaml"


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
        uuid.UUID(sp.space_id), "work/principale", sp.url, "x", None, "a", "a", "écrit", write
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
    response = await sp.client.get(f"{sp.base}/draft/merge", params={"path": "autre.schema.yaml"})
    assert response.status_code == 404


# --- Impacts (même fixture d'espace) -------------------------------------------

RAW = "sources/s/raw/User.schema.yaml"
ASSET = "sources/s/assets/User.schema.yaml"


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
    response = await sp.client.post(
        f"{sp.base}/draft/impacts/verify", json={"path": "x.schema.yaml"}
    )
    assert response.status_code == 404


# --- Journal de validation ----------------------------------------------------


async def test_journal_par_type_de_document(app: FastAPI, sp: ApiSpace) -> None:
    """Ticket d7139b17 : le niveau dépend du type de document. Un `$ref` cassé bloque
    sur un asset, informe sur un raw ; un asset sans son raw informe."""
    forge_edit(sp, {RAW: json.dumps({"title": "User"})})
    await _draft_write(
        app,
        sp,
        "sources/s/raw/Order.schema.yaml",
        {"properties": {"u": {"$ref": "Absent.schema.yaml"}}},
    )
    await _draft_write(
        app,
        sp,
        "sources/s/assets/Seul.schema.yaml",
        {"title": "Seul", "properties": {"u": {"$ref": "Absent.schema.yaml"}}},
    )
    log = (await sp.client.get(f"{sp.base}/draft/validation")).json()
    rules = {(e["rule"], e["document_type"], e["level"], e["blocking"]) for e in log["entries"]}
    assert ("broken_ref", "schema_raw", "warning", False) in rules
    assert ("broken_ref", "schema_asset", "error", True) in rules
    assert ("orphan_asset", "schema_asset", "warning", False) in rules
    assert log["blocking"] == 1


async def test_plus_d_acceptation(app: FastAPI, sp: ApiSpace) -> None:
    """Un avertissement ne s'accepte plus (ticket d7139b17) : la route a disparu."""
    response = await sp.client.post(
        f"{sp.base}/draft/validation/accept",
        json={"rule": "orphan_asset", "path": "x.schema.yaml", "message_code": "asset_orphan"},
    )
    assert response.status_code in (404, 405)


COLLECTED = "sources/s/raw/R.schema.yaml"


async def test_objet_raw_fusion_en_lecture_seule_meme_en_conflit(
    app: FastAPI, sp: ApiSpace
) -> None:
    """Ticket 173c6ed6 (Gael : « sur la partie source il faut ouvrir la fusion mais
    elle est en read only ») : l'objet raw se consulte, le serveur refuse de le
    réconcilier. Les assets se résolvent : voir les tests ci-dessus (OBJ)."""
    forge_edit(sp, {COLLECTED: json.dumps({"a": 1, "b": 1})})
    await _draft_write(app, sp, COLLECTED, {"a": 2, "b": 1})
    forge_edit(sp, {COLLECTED: json.dumps({"a": 3, "b": 1})})
    view = (await sp.client.get(f"{sp.base}/draft/merge", params={"path": COLLECTED})).json()
    assert (view["kind"], view["readonly"], view["collected"]) == ("conflict", True, True)
    refused = await sp.client.post(
        f"{sp.base}/draft/merge", json={"path": COLLECTED, "resolutions": {"/a": "theirs"}}
    )
    assert refused.status_code == 409
    assert refused.json()["error"] == "merge_collected_readonly"
    assert (await _draft_content(sp))["changed_upstream_count"] == 1
