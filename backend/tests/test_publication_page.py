"""Page de publication : progression de la validation, problèmes en tags, filtre, bouton
Publier ; même état pour les primitives MCP (ticket 48618517, epic « Index des
documents et validation avant publication »).

Gael : « Quand on arrive sur la page de publication […] tu regardes en asynchrone les
fichiers qui nécessitent d'être revalidés et tu le fais. Puis tu checks que tous les
fichiers présents sur le disque sont bien dans l'index et validés. Tout ça en
asynchrone. Le bouton publier ne s'active que quand tout est validé. »"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.mcp_surface.caller import McpCaller
from tests.mcp_helpers import call_tool
from tests.space_fixture import (
    ApiSpace,
    draft_write,
    forge_edit,
    make_api_space,
    run_passes,
    set_credential,
)

AGENT = "agent-publication"
OBJ = "sources/s/raw/O.schema.yaml"
APP = "cartographie/objets/application/app-01.yaml"
BROKEN = "sources/vieux/manifest.yaml"


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    """Un espace avec un type et un objet de cartographie ; un agent connecté pour
    l'administrateur, qui peut lire l'état de publication."""
    api_space = await make_api_space(client, app, tmp_path)
    forge_edit(
        api_space,
        {
            "cartographie/types/application.schema.yaml": (
                "title: Application\nx-archimate: ApplicationComponent\ntype: object\n"
            ),
            APP: "cle: APP-01\ntype: application\nnom: Caisse\n",
        },
    )
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives, all_read) "
            "VALUES ('Publication', $1, true) RETURNING id",
            ["get_publish_readiness", "get_validation_log"],
        )
        admin_id = await conn.fetchval("SELECT id FROM users WHERE is_bootstrap")
    caller = McpCaller(
        kind="oauth", ref="c", agent_profile_id=profile, user_id=admin_id, client_id="claude"
    )

    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return caller if token == AGENT else None

    app.state.mcp_resolvers.append(resolver)
    return api_space


async def _state(space: ApiSpace, filter_name: str = "all", q: str = "") -> dict[str, Any]:
    answer = await space.client.get(
        f"{space.base}/draft/validation/state", params={"filter": filter_name, "q": q}
    )
    assert answer.status_code == 200, answer.text
    body: dict[str, Any] = answer.json()
    return body


async def _open_page(app: FastAPI, space: ApiSpace) -> None:
    """Ouvrir la page : une passe demandée ; le travailleur de fond (coupé en test) la fait."""
    answer = await space.client.post(f"{space.base}/draft/validation/passes")
    assert answer.status_code == 200, answer.text
    await run_passes(app)


async def _readiness(space: ApiSpace) -> dict[str, Any]:
    answer = await space.client.get(f"{space.base}/draft/readiness")
    assert answer.status_code == 200, answer.text
    body: dict[str, Any] = answer.json()
    return body


async def _mcp(app: FastAPI, name: str, space: ApiSpace, **extra: Any) -> dict[str, Any]:
    result = await call_tool(
        app, AGENT, name, {"space_id": space.space_id, "branch_id": space.branch_id, **extra}
    )
    assert not result.is_error, result.content[0].text
    data: dict[str, Any] = result.structured_content
    return data


def _file(state: dict[str, Any], path: str) -> dict[str, Any]:
    return next(f for f in state["files"] if f["path"] == path)


async def test_la_page_n_attend_pas_la_passe(app: FastAPI, space: ApiSpace) -> None:
    """Critère 1 : ouvrir la page demande une passe sans l'attendre ; l'état dit qu'elle
    est dans la file, puis la passe faite, que tout est validé."""
    await draft_write(app, space, OBJ, {"title": "O"})
    answer = await space.client.post(f"{space.base}/draft/validation/passes")
    assert answer.json() == {"queued": True}
    waiting = await _state(space)
    assert waiting["queue"]["state"] == "pending"
    assert waiting["queue"]["position"] == 1 and waiting["queue"]["ahead"] == 0
    assert "validation_pending" in (await _readiness(space))["reasons"]
    await run_passes(app)
    done = await _state(space)
    assert done["queue"]["state"] is None
    assert done["counts"]["to_validate"] == 0
    assert done["last_pass"]["trigger"] == "publication_page"
    assert done["last_pass"]["at"] is not None
    assert done["comparison"]["compared"] is True
    assert done["comparison"]["disk_files"] == done["comparison"]["index_files"]
    assert _file(done, OBJ)["draft"] == "created"
    assert _file(done, APP)["draft"] is None


async def test_filtre_a_probleme_et_comptes(app: FastAPI, space: ApiSpace) -> None:
    """Critère 2 : « À problème » ne montre que les fichiers en erreur ou avec
    information ; les comptes des puces sont justes."""
    await draft_write(app, space, OBJ, {"title": "O"})
    await draft_write(
        app, space, "sources/s/raw/R.schema.yaml", {"properties": {"x": {"$ref": "No.schema.yaml"}}}
    )
    await draft_write(app, space, "cartographie/objets/application/app-02.yaml", ["pas un objet"])
    await _open_page(app, space)
    every = await _state(space, "all")
    problems = await _state(space, "problems")
    counts = problems["counts"]
    assert {f["status"] for f in problems["files"]} <= {"error", "warning"}
    assert counts["problems"] == counts["errors"] + counts["warnings"] == problems["total"]
    assert counts["errors"] >= 1 and counts["warnings"] >= 1
    assert counts["all"] == len(every["files"])
    assert counts["mine"] == 3 == (await _state(space, "mine"))["total"]
    statuses = [f["status"] for f in every["files"]]
    assert counts["valid"] == statuses.count("valid")
    # Les fichiers qui demandent une action d'abord.
    assert statuses.index("error") < statuses.index("valid")
    # Recherche par chemin ou par clé.
    assert [f["path"] for f in (await _state(space, "all", "APP-01"))["files"]] == [APP]


async def test_ligne_en_erreur_tags_et_liens(app: FastAPI, space: ApiSpace) -> None:
    """Critère 3 : une ligne en erreur porte ses problèmes (tags), et de quoi ouvrir le
    document en cause (type et identité)."""
    forge_edit(space, {"cartographie/objets/document/doc-01.yaml": "cle: DOC-01\ntype: document\n"})
    forge_edit(
        space,
        {
            "cartographie/types/acces.schema.yaml": "title: A\nx-archimate: Access\ntype: object\n",
            "cartographie/types/document.schema.yaml": (
                "title: D\nx-archimate: BusinessObject\ntype: object\n"
            ),
            "cartographie/objets/acces/rel-01.yaml": (
                "cle: REL-01\ntype: acces\nsource: application/APP-01\n"
                "cible: document/DOC-01\nnom: Lit\n"
            ),
        },
    )
    await draft_write(app, space, "cartographie/objets/document/doc-01.yaml", None)
    await _open_page(app, space)
    row = _file(await _state(space, "errors"), "cartographie/objets/acces/rel-01.yaml")
    assert row["status"] == "error" and row["draft"] is None
    assert (row["document_type"], row["identity"]) == (
        "carto_objet",
        {"type": "acces", "key": "REL-01"},
    )
    [problem] = [p for p in row["problems"] if p["rule"] == "references"]
    assert (problem["message_code"], problem["blocking"], problem["published"]) == (
        "reference_unknown",
        True,
        False,
    )
    deleted = _file(await _state(space, "mine"), "cartographie/objets/document/doc-01.yaml")
    assert (deleted["status"], deleted["draft"]) == ("deleted", "deleted")


async def test_erreur_deja_publiee_bloque(app: FastAPI, space: ApiSpace) -> None:
    """Critère 4 : une erreur hors brouillon, déjà présente dans la version publiée, est
    listée comme telle et bloque quand même."""
    forge_edit(space, {BROKEN: "slug: [cassé"})
    await draft_write(app, space, OBJ, {"title": "O"})
    await set_credential(app, space)
    # La version publiée est validée (passe après une publication, par exemple).
    async with app.state.pool.acquire() as conn:
        from ossie_studio.doc_index.queue import PERIODIC, request_pass
        from ossie_studio.doc_index.store import ViewKey

        await request_pass(
            conn, ViewKey(uuid.UUID(space.space_id), uuid.UUID(space.branch_id)), PERIODIC
        )
    await _open_page(app, space)
    row = _file(await _state(space, "errors"), BROKEN)
    assert row["draft"] is None
    assert all(p["published"] and p["blocking"] for p in row["problems"])
    ready = await _readiness(space)
    assert "validation_errors" in ready["reasons"] and not ready["ok"]
    assert ready["validation"]["errors"] == 1
    assert ready["validation"]["errors_outside_draft"] == 1


async def test_bouton_actif_quand_tout_est_valide(app: FastAPI, space: ApiSpace) -> None:
    """Critère 5 : sans erreur ni fichier à valider, la validation ne bloque plus."""
    await draft_write(app, space, OBJ, {"title": "O"})
    await set_credential(app, space)
    assert "validation_pending" in (await _readiness(space))["reasons"]
    await _open_page(app, space)
    ready = await _readiness(space)
    assert ready["reasons"] == [] and ready["ok"] is True
    assert ready["validation"] == {
        "errors": 0,
        "errors_outside_draft": 0,
        "to_validate": 0,
        "queue": {"state": None, "position": None, "ahead": 0, "done": 0, "total": 0},
    }


async def test_fichier_modifie_hors_application(app: FastAPI, space: ApiSpace) -> None:
    """Critère 6 : un fichier modifié hors de l'application apparaît comme « nouveau sur
    le disque » puis est validé."""
    await draft_write(app, space, OBJ, {"title": "O"})
    await _open_page(app, space)
    assert (await _state(space))["comparison"]["detected"] == 0
    forge_edit(space, {APP: "cle: APP-01\ntype: application\nnom: Caisse 2\n"})
    await _open_page(app, space)
    state = await _state(space)
    row = _file(state, APP)
    assert row["detected"] is True and row["status"] == "valid"
    assert state["comparison"]["detected"] == 1
    # La passe suivante, sans rien de nouveau, n'en détecte plus.
    await _open_page(app, space)
    assert (await _state(space))["comparison"]["detected"] == 0


async def test_agent_meme_reponse_que_la_page(app: FastAPI, space: ApiSpace) -> None:
    """Critère 7 : `get_publish_readiness` donne la même réponse que la page, au même
    moment ; `get_validation_log` lit le même état (statut, problèmes, file)."""
    forge_edit(space, {BROKEN: "slug: [cassé"})
    await draft_write(app, space, OBJ, {"title": "O"})
    await set_credential(app, space)
    assert await _mcp(app, "get_publish_readiness", space) == await _readiness(space)
    log = await _mcp(app, "get_validation_log", space)
    assert log["queue"]["state"] == "pending"  # une passe demandée, pas attendue
    assert log["counts"] == (await _state(space))["counts"]
    await run_passes(app)
    assert await _mcp(app, "get_publish_readiness", space) == await _readiness(space)
    log = await _mcp(app, "get_validation_log", space, filter="errors")
    assert [f["path"] for f in log["files"]] == [BROKEN]
    assert log["files"][0]["problems"][0]["blocking"] is True


async def test_publier_avec_une_erreur_refuse(app: FastAPI, space: ApiSpace) -> None:
    """Critère 8 : publier par l'API alors qu'un fichier est en erreur est refusé, même
    sans passer par l'écran (le serveur fait lui-même une passe à jour)."""
    await draft_write(app, space, OBJ, {"title": "O"})
    await set_credential(app, space)
    forge_edit(space, {BROKEN: "slug: [cassé"})
    refused = await space.client.post(f"{space.base}/draft/publish", json={"comment": "x"})
    assert refused.status_code == 409, refused.text
    assert "validation_errors" in refused.json()["error_args"]["reasons"]
    forge_edit(space, {BROKEN: None})
    done = await space.client.post(f"{space.base}/draft/publish", json={"comment": "x"})
    assert done.status_code == 200, done.text
    assert done.json()["published"] == 1
