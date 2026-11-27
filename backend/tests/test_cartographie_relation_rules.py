"""Relations permises personnalisées par type (ticket 2643fd8f) : règle effective
= ArchiMate moins couples désactivés plus couples ajoutés, déclarée côté type source
(`x-relations`), calculée par le service et appliquée partout."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from tests.space_fixture import ApiSpace, forge_edit, make_api_space

TYPES = {
    "application": ("Application", "ApplicationComponent"),
    "processus": ("Processus métier", "BusinessProcess"),
    "flux": ("Flux", "Flow"),
    "composition": ("Composition", "Composition"),
    "service": ("Service", "Serving"),
}


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    space = await make_api_space(client, app, tmp_path)
    files: dict[str, str | None] = {
        f"cartographie/types/{slug}.schema.yaml": (
            f"title: {title}\nx-archimate: {arch}\ntype: object\n"
        )
        for slug, (title, arch) in TYPES.items()
    }
    for key, ctype, name in (
        ("APP-A", "application", "A"),
        ("APP-B", "application", "B"),
        ("APP-C", "application", "C"),
        ("PRC-X", "processus", "X"),
    ):
        files[f"cartographie/objets/{ctype}/{key.lower()}.yaml"] = (
            f"cle: {key}\ntype: {ctype}\nnom: {name}\n"
        )
    forge_edit(space, files)
    return space


def _types(space: ApiSpace, rest: str = "") -> str:
    return f"{space.base}/cartographie/types{rest}"


async def _choices(space: ApiSpace, source: str, target: str) -> list[str]:
    answer = await space.client.get(
        f"{space.base}/cartographie/objets/{source}/relations-permises", params={"cible": target}
    )
    assert answer.status_code == 200, answer.text
    return [t["slug"] for t in answer.json()["types"]]


async def _detail(space: ApiSpace) -> dict[str, Any]:
    answer = await space.client.get(_types(space, "/application"))
    assert answer.status_code == 200, answer.text
    data: dict[str, Any] = answer.json()
    return data


async def _save(space: ApiSpace, relations: dict[str, Any] | None) -> Any:
    document = dict((await _detail(space))["document"])
    document.pop("x-relations", None)
    if relations is not None:
        document["x-relations"] = relations
    return await space.client.put(_types(space, "/application"), json={"schema": document})


async def _file(space: ApiSpace) -> str:
    return str((await _detail(space))["yaml"])


async def test_sans_ecart_rien_ne_change_et_l_onglet_liste_les_cibles(space: ApiSpace) -> None:
    """Critère 1, et ce que l'onglet reçoit."""
    assert "flux" in await _choices(space, "APP-A", "APP-B")
    detail = await _detail(space)
    by_relation = {r["relation"]: r for r in detail["relations"]}
    assert set(by_relation) == {"composition", "flux", "service"}
    assert "application" in by_relation["flux"]["archimate_targets"]
    assert "processus" not in by_relation["composition"]["archimate_targets"]
    assert {t["slug"] for t in detail["space_types"]} == set(TYPES)


async def test_desactiver_puis_reactiver_un_couple(space: ApiSpace) -> None:
    """Critères 2 et 3, et le rejeu sans effet (critère 10)."""
    answer = await _save(space, {"desactivees": [{"relation": "flux", "cibles": ["application"]}]})
    assert answer.status_code == 200, answer.text
    assert answer.json()["revision"]
    assert "flux" not in await _choices(space, "APP-A", "APP-B")
    assert "x-relations" in await _file(space) and "desactivees" in await _file(space)
    # Créer la relation désactivée : refusée par le service (écran comme MCP).
    created = await space.client.post(
        f"{space.base}/cartographie/objets",
        json={"key": "REL-AB", "type": "flux", "name": "x", "source": "APP-A", "target": "APP-B"},
    )
    assert created.status_code == 400
    assert created.json()["error_args"]["problems"][0]["code"] == "relation_not_allowed"
    replay = await _save(space, {"desactivees": [{"relation": "flux", "cibles": ["application"]}]})
    assert replay.status_code == 200 and replay.json()["revision"] is None
    back = await _save(space, None)
    assert back.status_code == 200, back.text
    assert "flux" in await _choices(space, "APP-A", "APP-B")
    assert "x-relations" not in await _file(space)


async def test_desactiver_toute_une_relation(space: ApiSpace) -> None:
    """Critère 4 : toutes les cibles d'ArchiMate désactivées d'un coup."""
    targets = next(r for r in (await _detail(space))["relations"] if r["relation"] == "flux")
    answer = await _save(
        space,
        {"desactivees": [{"relation": "flux", "cibles": targets["archimate_targets"]}]},
    )
    assert answer.status_code == 200, answer.text
    assert "flux" not in await _choices(space, "APP-A", "APP-B")
    assert "flux" not in await _choices(space, "APP-A", "PRC-X")


async def test_ajouter_un_couple_hors_archimate(space: ApiSpace) -> None:
    """Critères 5 et 6 : proposé, créé, et non signalé à la lecture."""
    assert "composition" not in await _choices(space, "APP-A", "PRC-X")
    answer = await _save(
        space, {"ajoutees": [{"relation": "composition", "cibles": ["processus"]}]}
    )
    assert answer.status_code == 200, answer.text
    assert "composition" in await _choices(space, "APP-A", "PRC-X")
    created = await space.client.post(
        f"{space.base}/cartographie/objets",
        json={
            "key": "REL-A-COMPOSE-X",
            "type": "composition",
            "name": "Compose",
            "source": "APP-A",
            "target": "PRC-X",
        },
    )
    assert created.status_code == 201, created.text
    listed = await space.client.get(f"{space.base}/cartographie/objets/REL-A-COMPOSE-X")
    assert listed.status_code == 200
    assert not [p for p in listed.json().get("problems", []) if p["code"] == "relation_not_allowed"]


async def test_desactiver_un_couple_utilise_refuse(space: ApiSpace) -> None:
    """Critère 7 : refus avec les relations concernées, rien n'est écrit."""
    forge_edit(
        space,
        {
            "cartographie/objets/flux/rel-ab.yaml": (
                "cle: REL-AB\ntype: flux\nsource: APP-A\ncible: APP-B\nnom: Transmet\n"
            )
        },
    )
    answer = await _save(space, {"desactivees": [{"relation": "flux", "cibles": ["application"]}]})
    assert answer.status_code == 409, answer.text
    body = answer.json()
    assert body["error"] == "carto_type_relations_in_use"
    assert body["error_args"]["relations"] == [
        {"key": "REL-AB", "source": "APP-A", "target": "APP-B"}
    ]
    assert "x-relations" not in await _file(space)


async def test_ecarts_invalides_refuses(space: ApiSpace) -> None:
    """Critère 8 : type absent, écart sans effet — erreur avec chemin."""
    cases = [
        ({"ajoutees": [{"relation": "flux", "cibles": ["inconnu"]}]}, "relations_target_unknown"),
        (
            {"ajoutees": [{"relation": "absente", "cibles": ["processus"]}]},
            "relations_relation_unknown",
        ),
        (
            {"desactivees": [{"relation": "composition", "cibles": ["processus"]}]},
            "relations_no_effect",
        ),
        ({"ajoutees": [{"relation": "flux", "cibles": ["application"]}]}, "relations_no_effect"),
    ]
    for relations, code in cases:
        answer = await _save(space, relations)
        assert answer.status_code == 400, (relations, answer.text)
        [problem] = answer.json()["error_args"]["problems"]
        assert problem["code"] == code and problem["field"].startswith("x-relations/"), problem
    shape = await _save(space, {"ajoutees": "flux"})
    assert shape.status_code == 400
    assert shape.json()["error_args"]["fields"] == ["schema_relations"]
