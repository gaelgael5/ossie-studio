"""Contrat OpenAPI complet à côté de son découpage (ticket docflow e79d008f).

Gael : « il faut également pouvoir voir le contrat complet… recopié dans le
répertoire sous un nom spécifique contrat.full.openapi.yaml ».
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.models import GitSpace
from ossie_studio.parsers.openapi_full import (
    CONTRACTS_INDEX,
    FULL_CONTRACT_NAME,
    copy_names,
    full_contract_actions,
    parse_contracts_index,
    readable_contract,
)
from ossie_studio.sources.collect import SourceCollector
from ossie_studio.sources.exclusion_prune import prune_excluded_raw
from ossie_studio.sources.explorer import (
    FAMILY_OPENAPI,
    build_source_index,
    subtree_file_counts,
)
from tests.space_fixture import ApiSpace, forge_edit, make_api_space
from tests.test_collect_service import _base_branch, _make_bare_with, _push, _raw, setup
from tests.test_openapi_repo import _CONTRAT_COMMANDES, _CONTRAT_FACTURES, _MAL_FORME, _collect
from tests.test_raw_explorer import _manifest, _schema, _tech

__all__ = ["setup"]

_CONTRAT_RICHE = """openapi: 3.0.3
info:
  title: API Colis
  version: 2.0.0
  description: Suivre ses colis.
servers:
  - url: https://api.exemple.fr
paths:
  /colis/{id}:
    parameters:
      - $ref: '#/components/parameters/Id'
    get:
      summary: Lire un colis
      parameters:
        - name: lang
          in: query
          schema: {type: string, enum: [fr, en]}
      responses:
        '200':
          description: le colis
          content:
            application/json:
              schema: {$ref: '#/components/schemas/Colis'}
        '404':
          $ref: '#/components/responses/Introuvable'
  /colis:
    post:
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: array
              items: {$ref: '#/components/schemas/Colis'}
      responses:
        '201': {description: créés}
components:
  parameters:
    Id: {name: id, in: path, schema: {type: string}}
  responses:
    Introuvable: {description: pas de colis}
  schemas:
    Colis:
      type: object
      properties:
        numero: {type: string}
"""


# --- nommage --------------------------------------------------------------------


def test_un_seul_contrat_dans_le_dossier_prend_le_nom_en_dur() -> None:
    assert copy_names(["api/OAS/geo/Geo.yaml"], repository=True) == {
        "api/OAS/geo/Geo.yaml": f"api/OAS/geo/{FULL_CONTRACT_NAME}"
    }


def test_plusieurs_contrats_dans_un_dossier_gardent_le_suffixe() -> None:
    names = copy_names(
        ["api/send-event/AMS.yaml", "api/send-event/PSSA.yaml", "openapi.json"], repository=True
    )
    assert names == {
        "api/send-event/AMS.yaml": f"api/send-event/AMS.{FULL_CONTRACT_NAME}",
        "api/send-event/PSSA.yaml": f"api/send-event/PSSA.{FULL_CONTRACT_NAME}",
        "openapi.json": FULL_CONTRACT_NAME,
    }


def test_contrat_depose_range_sa_copie_sous_son_nom_de_fichier() -> None:
    # Source de contrats déposés : l'arbre range les schémas sous le fichier.
    assert copy_names(["v1.yaml"], repository=False) == {"v1.yaml": f"v1.yaml/{FULL_CONTRACT_NAME}"}


def test_copie_disparue_supprimee_et_index_vide_reecrit() -> None:
    previous = yaml.safe_dump({"contracts": [{"path": f"a/{FULL_CONTRACT_NAME}", "source": "a/x"}]})
    writes, deletes = full_contract_actions({}, previous, repository=True)
    assert deletes == [f"contracts/a/{FULL_CONTRACT_NAME}"]
    assert [w.path for w in writes] == [CONTRACTS_INDEX]
    assert parse_contracts_index(writes[0].content) == {}
    # Ni contrat ni index précédent : rien à écrire.
    assert full_contract_actions({}, None, repository=True) == ([], [])


# --- vue lisible ----------------------------------------------------------------


def test_vue_lisible_resout_parametres_et_reponses_partages() -> None:
    doc = yaml.safe_load(_CONTRAT_RICHE)
    view = readable_contract(doc, {"Colis": "x-colis.schema.yaml"})
    assert (view["title"], view["version"], view["servers"]) == (
        "API Colis",
        "2.0.0",
        ["https://api.exemple.fr"],
    )
    get, post = view["operations"]
    assert (get["method"], get["path"], get["summary"]) == ("GET", "/colis/{id}", "Lire un colis")
    params = {p["name"]: p for p in get["parameters"]}
    assert params["id"]["in"] == "path" and params["id"]["required"] is True
    assert params["lang"]["required"] is False
    assert params["lang"]["schema"]["enum"] == ["fr", "en"]
    ok, missing = get["responses"]
    assert ok["schema"] == {"kind": "ref", "name": "Colis", "file": "x-colis.schema.yaml"}
    assert (missing["code"], missing["description"]) == ("404", "pas de colis")
    assert post["body"]["required"] is True
    assert post["body"]["schema"]["kind"] == "list"
    assert post["body"]["schema"]["of"]["name"] == "Colis"
    assert view["objects"] == [{"name": "Colis", "file": "x-colis.schema.yaml"}]


def test_vue_lisible_contrat_sans_operation() -> None:
    view = readable_contract(yaml.safe_load(_CONTRAT_FACTURES), {})
    assert view["operations"] == []
    assert view["objects"] == [{"name": "Commande", "file": ""}]


# --- collecte et explorateur ------------------------------------------------------


async def test_collecte_copie_les_contrats_valides_tels_quels(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    files = {
        "api/send-event/AMS.yaml": _CONTRAT_COMMANDES,
        "api/send-event/PSSA.json": _CONTRAT_FACTURES,
        "api/colis/colis.yaml": _CONTRAT_RICHE,
        "api/casse/broken.yaml": _MAL_FORME,
    }
    url, work = _make_bare_with(tmp_path, "src", files)
    await _collect(collector, space, url)
    raw = _raw(tmp_path, space)
    seul = raw / "contracts" / "api" / "colis" / FULL_CONTRACT_NAME
    assert seul.read_text(encoding="utf-8") == _CONTRAT_RICHE
    ams = raw / "contracts" / "api" / "send-event" / f"AMS.{FULL_CONTRACT_NAME}"
    pssa = raw / "contracts" / "api" / "send-event" / f"PSSA.{FULL_CONTRACT_NAME}"
    assert ams.read_text(encoding="utf-8") == _CONTRAT_COMMANDES
    assert pssa.read_text(encoding="utf-8") == _CONTRAT_FACTURES
    # Contrat mal formé : aucune copie (son erreur reste signalée par la collecte).
    assert not (raw / "contracts" / "api" / "casse").exists()
    index = parse_contracts_index((raw / CONTRACTS_INDEX).read_text(encoding="utf-8"))
    assert index[f"api/colis/{FULL_CONTRACT_NAME}"].operations == 2
    assert index[f"api/colis/{FULL_CONTRACT_NAME}"].title == "API Colis"

    # L'explorateur range la copie dans le dossier de ses schémas (source Git).
    view = raw.parent.parent.parent
    (raw.parent / "manifest.yaml").write_text(
        "version: 2\nslug: api\nlabel: API\ntype: git\n", encoding="utf-8"
    )
    tree = build_source_index(view, "api")
    folder = tree.folders["api/colis"]
    assert [c.path for c in folder.contracts] == [f"api/colis/{FULL_CONTRACT_NAME}"]
    assert folder.objects  # les schémas découpés sont toujours là
    counts = subtree_file_counts(tree, FAMILY_OPENAPI)
    assert counts["api/colis"] == len(folder.objects) + 1

    # Le contrat PSSA disparaît : sa copie aussi ; AMS, désormais seul dans son
    # dossier, prend le nom en dur (les noms se recalculent à chaque collecte).
    (work / "api/send-event/PSSA.json").unlink()
    _push(work)
    await _collect(collector, space, url)
    assert not pssa.exists() and not ams.exists()
    assert (raw / "contracts" / "api" / "send-event" / FULL_CONTRACT_NAME).read_text(
        encoding="utf-8"
    ) == _CONTRAT_COMMANDES


async def test_exclure_un_repertoire_retire_la_copie_du_contrat(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    url, _ = _make_bare_with(tmp_path, "src", {"api/colis/colis.yaml": _CONTRAT_RICHE})
    await _collect(collector, space, url)
    raw = _raw(tmp_path, space)
    view = raw.parent.parent.parent
    removed = prune_excluded_raw(view, "api", ["api"])
    assert f"contracts/api/colis/{FULL_CONTRACT_NAME}" in removed
    assert parse_contracts_index((raw / CONTRACTS_INDEX).read_text(encoding="utf-8")) == {}


# --- API -------------------------------------------------------------------------


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    return await make_api_space(client, app, tmp_path)


async def test_api_dossier_et_vue_lisible_du_contrat(sp: ApiSpace) -> None:
    raw = "sources/app/raw"
    copy = f"api/colis/{FULL_CONTRACT_NAME}"
    forge_edit(
        sp,
        {
            "sources/app/manifest.yaml": _manifest("app"),
            # Le schéma découpé, au nom que le parseur lui donne.
            f"{raw}/api-colis-colis-2-0-0-colis.schema.yaml": _schema("Colis", fields=1),
            f"{raw}/manifest-openapi.yaml": _tech(
                {"api/colis/colis.yaml": (["api-colis-colis-2-0-0-colis.schema.yaml"], "openapi")}
            ),
            f"{raw}/contracts/{copy}": _CONTRAT_RICHE,
            f"{raw}/{CONTRACTS_INDEX}": yaml.safe_dump(
                {
                    "contracts": [
                        {
                            "path": copy,
                            "source": "api/colis/colis.yaml",
                            "title": "API Colis",
                            "version": "2.0.0",
                            "operations": 2,
                        }
                    ]
                }
            ),
        },
    )
    base = f"{sp.base}/raw-explorer"
    r = await sp.client.get(f"{base}/app/folder", params={"path": "api/colis"})
    assert r.status_code == 200, r.text
    assert [c["path"] for c in r.json()["contracts"]] == [copy]
    r = await sp.client.get(f"{base}/app/tree", params={"family": "openapi"})
    folders = {f["path"]: f["files"] for f in r.json()["folders"]}
    assert folders["api/colis"] == 2  # le schéma découpé + le contrat complet

    r = await sp.client.get(f"{base}/app/contract", params={"path": copy})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["yaml"] == _CONTRAT_RICHE
    view = body["readable"]
    assert [(o["method"], o["path"]) for o in view["operations"]] == [
        ("GET", "/colis/{id}"),
        ("POST", "/colis"),
    ]
    # L'objet du contrat est relié à son schéma découpé.
    assert view["objects"] == [{"name": "Colis", "file": "api-colis-colis-2-0-0-colis.schema.yaml"}]

    r = await sp.client.get(f"{base}/app/contract", params={"path": "absent/" + FULL_CONTRACT_NAME})
    assert r.status_code == 404
    r = await sp.client.get(f"{base}/app/contract", params={"path": "../x"})
    assert r.status_code == 404
