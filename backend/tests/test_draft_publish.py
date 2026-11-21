"""Publication du brouillon : un commit, sous l'identité de la personne, avec son
jeton, sous verrou ; refus nommés ; reprise sûre."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.spaces.workdir import WorkdirManager
from tests.space_fixture import (
    ApiSpace,
    draft_write,
    forge_edit,
    forge_read,
    make_api_space,
    set_credential,
)

OBJ = "sources/s/raw/O.json"


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    return await make_api_space(client, app, tmp_path)


async def _readiness(sp: ApiSpace) -> list[str]:
    response = await sp.client.get(f"{sp.base}/draft/readiness")
    assert response.status_code == 200, response.text
    reasons: list[str] = response.json()["reasons"]
    return reasons


async def _publish(sp: ApiSpace, comment: str = "Ma modification"):  # type: ignore[no-untyped-def]
    return await sp.client.post(f"{sp.base}/draft/publish", json={"comment": comment})


def _leftovers(app: FastAPI) -> list[Path]:
    return list(Path(app.state.settings.work_volume_path).glob("*/*.publish-*"))


def _log(sp: ApiSpace, fmt: str) -> str:
    return subprocess.run(
        [
            "git",
            "--git-dir",
            sp.url.removeprefix("file://"),
            "log",
            "-1",
            f"--format={fmt}",
            "work/principale",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


async def test_publication_un_commit_auteur_utilisateur(app: FastAPI, sp: ApiSpace) -> None:
    await draft_write(app, sp, OBJ, {"a": 1})
    await set_credential(app, sp)
    async with app.state.pool.acquire() as conn:
        await conn.execute("UPDATE users SET display_name = 'Gaël Admin' WHERE is_bootstrap")
    assert await _readiness(sp) == []
    response = await _publish(sp)
    assert response.status_code == 200, response.text
    assert json.loads(forge_read(sp, OBJ) or "{}") == {"a": 1}
    assert _log(sp, "%an|%s") == "Gaël Admin|ossie: Ma modification"
    assert (await sp.client.get(f"{sp.base}/draft")).json()["count"] == 0
    assert not _leftovers(app)  # aucun worktree de publication résiduel


async def test_sans_nom_l_auteur_est_l_email(app: FastAPI, sp: ApiSpace) -> None:
    await draft_write(app, sp, OBJ, {"a": 1})
    await set_credential(app, sp)
    assert (await _publish(sp)).status_code == 200
    assert "@" in _log(sp, "%an")


async def test_brouillon_vide(sp: ApiSpace) -> None:
    assert "empty_draft" in await _readiness(sp)


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (None, "credential_missing"),
        ("a_saisir", "credential_missing"),
        ("jamais_teste", "credential_not_valid"),
        ("refuse", "credential_not_valid"),
    ],
)
async def test_identifiant_absent_ou_non_valide(
    app: FastAPI, sp: ApiSpace, status: str | None, reason: str
) -> None:
    await draft_write(app, sp, OBJ, {"a": 1})
    if status is not None:
        await set_credential(app, sp, status)
    assert reason in await _readiness(sp)
    refused = await _publish(sp)
    assert refused.status_code == 409
    assert reason in refused.json()["error_args"]["reasons"]


async def test_conflit_bloque_et_brouillon_intact(app: FastAPI, sp: ApiSpace) -> None:
    forge_edit(sp, {OBJ: json.dumps({"a": 1})})
    await draft_write(app, sp, OBJ, {"a": 2})
    forge_edit(sp, {OBJ: json.dumps({"a": 3})})
    await set_credential(app, sp)
    assert "conflicts" in await _readiness(sp)
    refused = await _publish(sp)
    assert refused.status_code == 409
    assert (await sp.client.get(f"{sp.base}/draft")).json()["count"] == 1


async def test_fusion_auto_pendant_la_publication(app: FastAPI, sp: ApiSpace) -> None:
    forge_edit(sp, {OBJ: json.dumps({"a": 1, "b": 1})})
    await draft_write(app, sp, OBJ, {"a": 1, "b": 2})
    forge_edit(sp, {OBJ: json.dumps({"a": 9, "b": 1})})
    await set_credential(app, sp)
    assert "conflicts" not in await _readiness(sp)
    assert (await _publish(sp)).status_code == 200
    assert json.loads(forge_read(sp, OBJ) or "{}") == {"a": 9, "b": 2}


async def test_impact_en_attente_bloque(app: FastAPI, sp: ApiSpace) -> None:
    forge_edit(sp, {"sources/s/raw/U.json": "{}", "sources/s/assets/U.json": "{}"})
    await draft_write(app, sp, "sources/s/raw/U.json", {"v": 2})
    await set_credential(app, sp)
    assert "impacts_pending" in await _readiness(sp)


async def test_erreur_liee_au_brouillon_bloque_preexistante_non(app: FastAPI, sp: ApiSpace) -> None:
    forge_edit(sp, {"sources/vieux/manifest.yaml": "slug: [cassé"})
    await draft_write(app, sp, OBJ, {"a": 1})
    await set_credential(app, sp)
    assert "validation_errors" not in await _readiness(sp)
    await draft_write(app, sp, "sources/s/raw/R.json", {"properties": {"x": {"$ref": "Nope.json"}}})
    assert "validation_errors" in await _readiness(sp)


async def test_publication_en_cours(app: FastAPI, sp: ApiSpace) -> None:
    await draft_write(app, sp, OBJ, {"a": 1})
    await set_credential(app, sp)
    async with app.state.pool.acquire() as holder:
        await holder.fetchval(
            "SELECT pg_advisory_lock(hashtextextended($1, 0))", f"publish:{sp.branch_id}"
        )
        assert "publish_in_progress" in await _readiness(sp)
        refused = await _publish(sp)
        assert refused.status_code == 409
        assert refused.json()["error"] == "publish_in_progress"
        await holder.execute(
            "SELECT pg_advisory_unlock(hashtextextended($1, 0))", f"publish:{sp.branch_id}"
        )
    assert (await _publish(sp)).status_code == 200


async def test_commentaire_vide_refuse(app: FastAPI, sp: ApiSpace) -> None:
    await draft_write(app, sp, OBJ, {"a": 1})
    await set_credential(app, sp)
    response = await _publish(sp, "  ")
    assert response.status_code == 400
    assert "comment_required" in response.json()["error_args"]["fields"]


async def test_message_contient_les_decisions(app: FastAPI, sp: ApiSpace) -> None:
    forge_edit(
        sp,
        {
            "sources/s/raw/U.json": "{}",
            "sources/s/assets/U.json": "{}",
            "sources/s/raw/Seul.json": "{}",
        },
    )
    await draft_write(app, sp, "sources/s/raw/U.json", {"v": 2})
    await draft_write(app, sp, "sources/s/assets/Orphelin.json", {"t": 1})
    await set_credential(app, sp)
    await sp.client.post(
        f"{sp.base}/draft/impacts/verify", json={"path": "sources/s/assets/U.json"}
    )
    await sp.client.post(
        f"{sp.base}/draft/validation/accept",
        json={
            "rule": "orphan_asset",
            "path": "sources/s/assets/Orphelin.json",
            "message_code": "asset_orphan",
        },
    )
    assert (await _publish(sp, "Avec décisions")).status_code == 200
    body = _log(sp, "%B")
    assert "Impact vérifié : sources/s/assets/U.json" in body
    assert "Avertissement accepté : orphan_asset sources/s/assets/Orphelin.json" in body


async def test_push_exterieur_autre_objet_reconstruit(
    app: FastAPI, sp: ApiSpace, monkeypatch: pytest.MonkeyPatch
) -> None:
    await draft_write(app, sp, OBJ, {"a": 1})
    await set_credential(app, sp)
    manager: WorkdirManager = app.state.workdir_manager
    original = manager._objects_moved
    calls = {"n": 0}

    async def moved_then_race(*args, **kwargs):  # type: ignore[no-untyped-def]
        result = await original(*args, **kwargs)
        calls["n"] += 1
        if calls["n"] == 1:
            forge_edit(sp, {"sources/ailleurs.txt": "course"})  # push concurrent
        return result

    monkeypatch.setattr(manager, "_objects_moved", moved_then_race)
    response = await _publish(sp)
    assert response.status_code == 200, response.text
    assert calls["n"] == 2  # un nouvel essai sur la nouvelle tête
    assert forge_read(sp, "sources/ailleurs.txt") == "course"
    assert json.loads(forge_read(sp, OBJ) or "{}") == {"a": 1}


async def test_push_exterieur_meme_objet_renvoi_fusion(
    app: FastAPI, sp: ApiSpace, monkeypatch: pytest.MonkeyPatch
) -> None:
    await draft_write(app, sp, OBJ, {"a": 1})
    await set_credential(app, sp)
    manager: WorkdirManager = app.state.workdir_manager
    original = manager._objects_moved
    calls = {"n": 0}

    async def moved_then_race(*args, **kwargs):  # type: ignore[no-untyped-def]
        result = await original(*args, **kwargs)
        calls["n"] += 1
        if calls["n"] == 1:
            forge_edit(sp, {OBJ: json.dumps({"autre": True})})
        return result

    monkeypatch.setattr(manager, "_objects_moved", moved_then_race)
    response = await _publish(sp)
    assert response.status_code == 409
    assert response.json()["error"] == "branch_changed_conflict"
    assert (await sp.client.get(f"{sp.base}/draft")).json()["count"] == 1
    assert "publish_in_progress" not in await _readiness(sp)


async def test_processus_interrompu_verrou_relache_brouillon_intact(
    app: FastAPI, sp: ApiSpace, monkeypatch: pytest.MonkeyPatch
) -> None:
    await draft_write(app, sp, OBJ, {"a": 1})
    await set_credential(app, sp)
    manager: WorkdirManager = app.state.workdir_manager

    async def boom(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("processus interrompu")

    monkeypatch.setattr(manager, "_commit_and_push", boom)
    with pytest.raises(RuntimeError):
        await _publish(sp)
    monkeypatch.undo()
    assert "publish_in_progress" not in await _readiness(sp)
    assert (await sp.client.get(f"{sp.base}/draft")).json()["count"] == 1
    assert forge_read(sp, OBJ) is None  # rien de publié à moitié
    assert (await _publish(sp)).status_code == 200


async def test_jeton_revoque_entre_test_et_publication(app: FastAPI, sp: ApiSpace) -> None:
    hook = Path(sp.url.removeprefix("file://")) / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'Authentication failed for token' >&2\nexit 1\n")
    hook.chmod(0o755)
    await draft_write(app, sp, OBJ, {"a": 1})
    await set_credential(app, sp)
    response = await _publish(sp)
    assert response.status_code == 403
    assert response.json()["error"] == "publish_credential_rejected"
    assert (await sp.client.get(f"{sp.base}/draft")).json()["count"] == 1
    assert "publish_in_progress" not in await _readiness(sp)


async def test_sans_droit_de_publier_refuse(app: FastAPI, sp: ApiSpace) -> None:
    from httpx import ASGITransport

    from tests.conftest import create_plain_user, login

    await create_plain_user(app, "lecteur@x.org", "motdepasse-solide")
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) VALUES ('Lect', $1) RETURNING id",
            ["spaces:read"],
        )
        await conn.execute(
            "UPDATE users SET profile_id = $1 WHERE email = 'lecteur@x.org'", profile
        )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        await login(other, "lecteur@x.org", "motdepasse-solide")
        reasons = (await other.get(f"{sp.base}/draft/readiness")).json()["reasons"]
        assert "no_publish_right" in reasons
        refused = await other.post(f"{sp.base}/draft/publish", json={"comment": "x"})
        assert refused.status_code == 409
        assert "no_publish_right" in refused.json()["error_args"]["reasons"]
