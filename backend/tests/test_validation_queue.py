"""File de validation sérialisée (enabler 6b171b74, epic « Index des documents et
validation avant publication ») : une seule passe à la fois, file dédupliquée qui
survit à un redémarrage, déclencheurs, état lisible, passe en échec qui libère la
file."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.doc_index import queue
from ossie_studio.doc_index.passes import PassResult, Validation
from ossie_studio.doc_index.store import ViewKey
from ossie_studio.drafts.workdir import DraftWorkdir
from tests.conftest import create_plain_user
from tests.space_fixture import ApiSpace, admin_workdir, forge_edit, make_api_space

BRANCH = "work/principale"


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    api_space = await make_api_space(client, app, tmp_path)
    forge_edit(
        api_space,
        {
            "cartographie/types/application.schema.yaml": (
                "title: Application\nx-archimate: ApplicationComponent\ntype: object\n"
            ),
            "cartographie/objets/application/app-01.yaml": (
                "cle: APP-01\ntype: application\nnom: Caisse\n"
            ),
        },
    )
    return api_space


async def _lea(app: FastAPI, admin: DraftWorkdir) -> DraftWorkdir:
    await create_plain_user(app, "lea@example.org", "motdepasse-solide")
    async with app.state.pool.acquire() as conn:
        lea_id = await conn.fetchval("SELECT id FROM users WHERE email = 'lea@example.org'")
    return DraftWorkdir(
        app.state.workdir_manager, app.state.draft_views, app.state.pool, lea_id, admin.branch_id
    )


def _key(space: ApiSpace, workdir: DraftWorkdir | None = None) -> ViewKey:
    return ViewKey(
        uuid.UUID(space.space_id),
        uuid.UUID(space.branch_id),
        workdir.user_id if workdir else None,
    )


def _validation(app: FastAPI) -> Validation:
    found = app.state.validation
    assert isinstance(found, Validation)
    return found


async def _rows(app: FastAPI) -> list[Any]:
    async with app.state.pool.acquire() as conn:
        return list(
            await conn.fetch(
                "SELECT user_id, trigger, state FROM validation_requests ORDER BY id"
            )
        )


async def _request(app: FastAPI, key: ViewKey, trigger: str = queue.PERIODIC) -> bool:
    async with app.state.pool.acquire() as conn:
        return await queue.request_pass(conn, key, trigger)


async def test_une_seule_passe_a_la_fois(app: FastAPI, space: ApiSpace) -> None:
    """Critère 1 : deux demandes de deux utilisateurs ne tournent jamais en même temps ;
    la seconde attend la fin de la première."""
    admin = await admin_workdir(app, space)
    lea = await _lea(app, admin)
    await _request(app, _key(space, admin))
    await _request(app, _key(space, lea))
    validation = _validation(app)
    running: list[str] = []
    overlap: list[bool] = []

    async def slow(request: queue.Request) -> PassResult:
        overlap.append(bool(running))
        running.append(str(request.key.user_id))
        await asyncio.sleep(0.2)
        running.pop()
        return PassResult(request.key, 0, 0, 0)

    validation.run = slow  # type: ignore[method-assign]
    first, second = await asyncio.gather(validation.run_next(), validation.run_next())
    assert overlap == [False]  # une seule a pris la file
    assert (first is None) != (second is None)
    done = first or second
    assert done is not None and done.key.user_id == admin.user_id
    after = await validation.run_next()
    assert after is not None and after.key.user_id == lea.user_id
    assert await validation.run_next() is None


async def test_demandes_dedupliquees(app: FastAPI, space: ApiSpace) -> None:
    """Critère 2 : trois demandes pour le même brouillon pendant qu'une passe tourne
    n'en donnent qu'une en attente."""
    admin = await admin_workdir(app, space)
    key = _key(space, admin)
    assert await _request(app, key)
    async with app.state.pool.acquire() as conn:
        running = await queue.claim_next(conn)
    assert running is not None
    results = [await _request(app, key) for _ in range(3)]
    assert results == [True, False, False]
    rows = await _rows(app)
    assert [r["state"] for r in rows] == ["running", "pending"]


async def test_fin_d_import_demande_une_passe(app: FastAPI, space: ApiSpace) -> None:
    """Critère 3 : à la fin d'un import, une passe est demandée pour le brouillon de la
    personne."""
    base = f"{space.base}/cartographie/types/application/imports"
    created = await space.client.post(
        base, json={"label": "Fichier", "slug": "fichier", "format": "csv"}
    )
    assert created.status_code == 201, created.text
    await space.client.put(
        f"{base}/fichier/lecture", json={"separator": ";", "encoding": "utf-8", "header": True}
    )
    await space.client.put(
        f"{base}/fichier/template", json={"template": '{"cle": $.cle, "nom": $.nom}'}
    )
    done = await space.client.post(
        f"{base}/fichier/import",
        files={"file": ("f.csv", b"cle;nom\nAPP-02;Stock\n", "text/csv")},
    )
    assert done.status_code == 200, done.text
    admin = await admin_workdir(app, space)
    rows = await _rows(app)
    assert [(r["user_id"], r["trigger"]) for r in rows] == [(admin.user_id, queue.IMPORT)]


async def test_passe_periodique_et_reglage(app: FastAPI, space: ApiSpace) -> None:
    """Critère 4 : une passe est demandée pour chaque vue qui a des fichiers à valider,
    et seulement pour celles-là ; l'intervalle se règle sur la page « Paramètres »."""
    validation = _validation(app)
    assert await validation.interval_seconds() == 600
    changed = await space.client.put(
        "/api/settings/validation_interval_minutes", json={"value": 5}
    )
    assert changed.status_code == 200, changed.text
    assert await validation.interval_seconds() == 300
    admin = await admin_workdir(app, space)
    await admin.compare_index(uuid.UUID(space.space_id), BRANCH, space.url, "x", None)
    assert await validation.request_periodic() == 2  # vue publiée et brouillon
    while await validation.run_next() is not None:
        pass
    assert await validation.request_periodic() == 0
    assert await _rows(app) == []


async def test_vraie_passe_pose_les_etats(app: FastAPI, space: ApiSpace) -> None:
    """Une passe compare la vue au disque, valide et pose l'état de chaque fichier : un
    fichier illisible est en erreur, avec le problème ; les autres sont valides."""
    admin = await admin_workdir(app, space)
    forge_edit(space, {"cartographie/objets/application/app-09.yaml": "cle: [ouvert\n"})
    await _request(app, _key(space, admin), queue.PUBLICATION_PAGE)
    request = await _validation(app).run_next()
    assert request is not None
    async with app.state.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT f.path, f.status, f.problems FROM doc_index_file f "
            "JOIN doc_index_view v ON v.id = f.view_id WHERE v.user_id = $1",
            admin.user_id,
        )
        validated = await conn.fetchval(
            "SELECT validated_at FROM doc_index_view WHERE user_id = $1", admin.user_id
        )
    status = {r["path"]: r["status"] for r in rows}
    assert status["cartographie/objets/application/app-09.yaml"] == "en_erreur"
    assert status["cartographie/objets/application/app-01.yaml"] == "valide"
    problems = next(r["problems"] for r in rows if r["path"].endswith("app-09.yaml"))
    assert problems and problems[0]["level"] == "error"
    assert validated is not None


async def test_demandes_conservees_et_passe_interrompue(app: FastAPI, space: ApiSpace) -> None:
    """Critère 5 : la file est en base ; une passe interrompue (serveur arrêté pendant
    la passe, signe de vie trop ancien) est remise en attente au redémarrage."""
    admin = await admin_workdir(app, space)
    await _request(app, _key(space, admin))
    async with app.state.pool.acquire() as conn:
        assert await queue.claim_next(conn) is not None
        await conn.execute(
            "UPDATE validation_requests SET heartbeat_at = now() - interval '1 hour'"
        )
        assert await queue.recover(conn) == 1
    assert [r["state"] for r in await _rows(app)] == ["pending"]
    assert await _validation(app).run_next() is not None


async def test_etat_de_la_file_lisible(app: FastAPI, space: ApiSpace) -> None:
    """Critère 6 : la passe en cours (quelle vue, combien de fichiers faits sur combien)
    et la position d'une vue se lisent."""
    admin = await admin_workdir(app, space)
    lea = await _lea(app, admin)
    await _request(app, _key(space, lea))
    await _request(app, _key(space))
    await _request(app, _key(space, admin))
    async with app.state.pool.acquire() as conn:
        running = await queue.claim_next(conn)
        assert running is not None
        await queue.report_progress(conn, running.id, 3, 12)
    answer = await space.client.get(f"{space.base}/draft/validation/file")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["running"]["view"] == "draft"
    assert (body["running"]["done"], body["running"]["total"]) == (3, 12)
    assert body["pending"] == 2
    assert body["published"]["position"] == 1
    assert body["draft"]["position"] == 2


async def test_passe_en_echec(app: FastAPI, space: ApiSpace) -> None:
    """Critère 7 : une passe qui échoue libère la file, marque la vue comme non validée
    et l'écrit dans le journal ; la demande suivante démarre."""
    admin = await admin_workdir(app, space)
    lea = await _lea(app, admin)
    await _request(app, _key(space, admin))
    await _request(app, _key(space, lea))
    validation = _validation(app)
    real = validation.run

    async def broken(request: queue.Request) -> PassResult:
        if request.key.user_id == admin.user_id:
            raise RuntimeError("disque plein")
        return await real(request)

    validation.run = broken  # type: ignore[method-assign]
    failed = await validation.run_next()
    assert failed is not None and failed.key.user_id == admin.user_id
    async with app.state.pool.acquire() as conn:
        view = await conn.fetchrow(
            "SELECT validated_at, validation_error FROM doc_index_view WHERE user_id = $1",
            admin.user_id,
        )
        journal = await conn.fetchrow(
            "SELECT actor_label, detail FROM audit_log WHERE object_type = 'validation_pass'"
        )
    assert view is not None and view["validated_at"] is None
    assert "disque plein" in view["validation_error"]
    assert journal is not None and journal["actor_label"] == "system"
    assert journal["detail"]["outcome"] == "failed"
    following = await validation.run_next()
    assert following is not None and following.key.user_id == lea.user_id
    assert await _rows(app) == []


async def test_passe_sans_changement_ne_relance_pas_les_regles(
    app: FastAPI, space: ApiSpace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bug c6a4ab74 : une passe tient le verrou de la vue ; sans fichier changé depuis
    la dernière passe complète, elle ne relit pas toute la vue, et l'état reste."""
    from ossie_studio.doc_index import passes

    admin = await admin_workdir(app, space)
    forge_edit(space, {"cartographie/objets/application/app-09.yaml": "cle: [ouvert\n"})
    calls: list[Path] = []
    real = passes.run_rules

    def counting(root: Path) -> list[Any]:
        calls.append(root)
        return real(root)

    monkeypatch.setattr(passes, "run_rules", counting)
    validation = _validation(app)
    await _request(app, _key(space, admin), queue.PUBLICATION_PAGE)
    assert await validation.run_next() is not None
    assert len(calls) == 1
    await _request(app, _key(space, admin), queue.PUBLICATION_PAGE)
    assert await validation.run_next() is not None
    assert len(calls) == 1  # rien n'a changé : règles non relancées
    async with app.state.pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT f.status FROM doc_index_file f JOIN doc_index_view v ON v.id = f.view_id "
            "WHERE v.user_id = $1 AND f.path LIKE '%app-09.yaml'",
            admin.user_id,
        )
    assert status == "en_erreur"
    forge_edit(space, {"cartographie/objets/application/app-09.yaml": "cle: APP-09\n"})
    await _request(app, _key(space, admin), queue.PUBLICATION_PAGE)
    assert await validation.run_next() is not None
    assert len(calls) == 2  # un fichier a changé : passe complète
