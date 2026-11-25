"""Relier les points d'entrée du code aux opérations du contrat OpenAPI de la même
source (epic « Cartographie applicative dans raw », ticket 90ed0967) : l'état au
contrat va dans les `.extend.yaml` ; le contrat n'est jamais modifié."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ossie_studio.models import GitSpace
from ossie_studio.parsers.application import dump_functions, module_key
from ossie_studio.parsers.base import OutputAction
from ossie_studio.parsers.conventions import load_schema
from ossie_studio.parsers.openapi_full import (
    CONTRACTS_INDEX,
    ContractEntry,
    dump_contracts_index,
)
from ossie_studio.sources.collect import SourceCollector
from ossie_studio.sources.contract_links import (
    link_point,
    operation_index,
    route_key,
    server_prefixes,
    sync_contract_links,
)
from tests.test_collect_service import _base_branch, _make_bare_with, _push, _raw
from tests.test_collect_service import setup as setup  # fixture réutilisée
from tests.test_openapi_repo import _collect


def _op(operation_id: str = "") -> dict[str, Any]:
    op: dict[str, Any] = {"responses": {"200": {"description": "ok"}}}
    if operation_id:
        op["operationId"] = operation_id
    return op


def _http(verb: str, route: str | None) -> dict[str, Any]:
    return {"kind": "http", "verb": verb, "route": route}


CONTRACT: dict[str, Any] = {
    "openapi": "3.0.3",
    "servers": [{"url": "https://boutique.example/api"}],
    "paths": {
        "/commandes/{commandeId}": {"get": _op("lireCommande")},
        "/paniers/{id}/": {"delete": _op("viderPanier")},
    },
}


# --- Critère 1 : verbe + route normalisée -----------------------------------------------


def test_parametres_par_position_casse_et_barre_finale() -> None:
    same = {
        route_key(r)
        for r in (
            "/api/commandes/{id}",
            "api/Commandes/{commandeId}/",
            "/api/commandes/{id:guid}",
            "/API/commandes/{id?}",
        )
    }
    assert same == {"/api/commandes/{}"}
    assert route_key("/api/commandes/{id}/lignes") != route_key("/api/commandes/{id}")


def test_relie_ou_absent() -> None:
    index = operation_index({"boutique/contrat.full.openapi.yaml": CONTRACT})
    lire = link_point(_http("GET", "/commandes/{id}"), index)
    assert lire["contract"] == {
        "status": "linked",
        "operations": [
            {
                "file": "boutique/contrat.full.openapi.yaml",
                "verb": "GET",
                "path": "/commandes/{commandeId}",
                "operationId": "lireCommande",
            }
        ],
    }
    # Même route, autre verbe : absent.
    assert link_point(_http("PUT", "/commandes/{id}"), index)["contract"] == {"status": "absent"}
    assert link_point(_http("GET", "/factures"), index)["contract"] == {"status": "absent"}


# --- Critère 2 : préfixe des servers ----------------------------------------------------


def test_prefixe_des_servers_pris_en_compte() -> None:
    index = operation_index({"c.yaml": CONTRACT})
    # Avec ou sans le préfixe `/api` du contrat.
    for route in ("/api/commandes/{id}", "/commandes/{id}", "/api/paniers/{panierId}"):
        verb = "DELETE" if "paniers" in route else "GET"
        assert link_point(_http(verb, route), index)["contract"]["status"] == "linked", route
    with_variable = {
        "servers": [
            {"url": "https://{hote}/{base}/v2", "variables": {"base": {"default": "boutique"}}},
            {"url": "/{inconnu}"},
            {"url": "https://boutique.example"},
        ]
    }
    # `{hote}` est dans l'hôte : sans effet sur le chemin ; `{inconnu}` sans défaut :
    # adresse écartée ; une adresse sans chemin ne donne pas de préfixe.
    assert server_prefixes({"servers": with_variable["servers"][:1]}) == ["/boutique/v2"]
    assert server_prefixes({"servers": with_variable["servers"][1:]}) == []


# --- Critère 3 : route inconnue, point non HTTP, source sans contrat ----------------------


def test_route_inconnue_et_tache_non_comparees() -> None:
    index = operation_index({"c.yaml": CONTRACT})
    assert "contract" not in link_point(_http("GET", None), index)
    assert "contract" not in link_point({"kind": "task"}, index)
    # Source sans contrat : un ancien état est retiré.
    old = {**_http("GET", "/commandes/{id}"), "contract": {"status": "absent"}}
    assert link_point(old, None) == _http("GET", "/commandes/{id}")


# --- Critère 4 : une opération présente dans deux contrats --------------------------------


def test_meme_operation_dans_deux_contrats() -> None:
    index = operation_index({"b.yaml": CONTRACT, "a.yaml": CONTRACT})
    linked = link_point(_http("GET", "/api/commandes/{id}"), index)["contract"]
    assert [o["file"] for o in linked["operations"]] == ["a.yaml", "b.yaml"]


# --- Critère 5 : écrit seulement s'il change, contrat jamais modifié ----------------------


def _raw_files(extend: dict[str, Any], contract: dict[str, Any] | None) -> dict[str, str]:
    files = {"ctrl.extend.yaml": dump_functions(extend)}
    if contract is not None:
        entry = ContractEntry("api/contrat.full.openapi.yaml", "api/openapi.yaml", "B", "1", 2)
        files[CONTRACTS_INDEX] = dump_contracts_index({entry.path: entry})
        files["contracts/api/contrat.full.openapi.yaml"] = yaml.safe_dump(
            {**contract, "info": {"title": "B", "version": "1"}}
        )
    return files


def test_sync_ecrit_seulement_ce_qui_change() -> None:
    point = _http("GET", "/api/commandes/{id}")
    lire = {"name": "Lire", "visibility": "public", "entryPoint": point}
    extend = {"methods": [lire]}
    existing = _raw_files(extend, CONTRACT)
    writes = sync_contract_links(existing, [], [])
    assert [w.path for w in writes] == ["ctrl.extend.yaml"]  # jamais le contrat
    linked = load_schema(writes[0].content)
    assert linked["methods"][0]["entryPoint"]["contract"]["status"] == "linked"
    # Déjà à jour : aucune écriture.
    existing["ctrl.extend.yaml"] = writes[0].content
    assert sync_contract_links(existing, [], []) == []
    # Le parseur réécrit le `.extend.yaml` (sans état) : l'écriture est complétée sur place.
    fresh = OutputAction("ctrl.extend.yaml", dump_functions(extend))
    [again] = sync_contract_links(existing, [fresh], [])
    assert again.content == writes[0].content
    # Le contrat retiré par la collecte : l'état au contrat disparaît.
    gone = sync_contract_links(existing, [], ["contracts/api/contrat.full.openapi.yaml"])
    assert "contract" not in load_schema(gone[0].content)["methods"][0]["entryPoint"]


# --- Collecte de bout en bout : C# + contrat dans le même dépôt ---------------------------

CONTROLLER = """namespace Boutique.Api.Controllers;

[ApiController]
[Route("api/[controller]")]
public class CommandesController : ControllerBase
{
    [HttpGet("{id:guid}")]
    public Commande Lire(Guid id) => null;

    [HttpPut("{id}/statut")]
    public void Changer(Guid id, string statut) { }

    [HttpGet(Routes.Toutes)]
    public List<Commande> Toutes() => null;
}
"""

CONTRACT_YAML = """openapi: 3.0.3
info:
  title: Boutique
  version: '1.0'
servers:
  - url: https://boutique.example/api
paths:
  /commandes/{commandeId}:
    get:
      operationId: lireCommande
      responses:
        '200':
          description: ok
  /paniers/{id}:
    delete:
      operationId: viderPanier
      responses:
        '204':
          description: vide
"""

STATUT_OP = """  /commandes/{commandeId}/statut:
    put:
      operationId: changerStatut
      responses:
        '204':
          description: changé
"""

API = "src/Boutique.Api/Boutique.Api.csproj"
CTRL = "boutique-api-controllers-commandescontroller.extend.yaml"


def _points(raw: Path) -> dict[str, Any]:
    ctrl = load_schema((raw / CTRL).read_text("utf-8"))
    module = load_schema((raw / f"{module_key(API)}.extend.yaml").read_text("utf-8"))
    points = {m["name"]: m["entryPoint"] for m in ctrl["methods"]}
    points.update({f["name"]: f["entryPoint"] for f in module["functions"]})
    return points


async def test_collecte_relie_le_code_au_contrat(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    url, work = _make_bare_with(
        tmp_path,
        "src",
        {
            API: '<Project Sdk="Microsoft.NET.Sdk.Web"></Project>',
            "src/Boutique.Api/Controllers/CommandesController.cs": CONTROLLER,
            "src/Boutique.Api/Commande.cs": "namespace Boutique.Api;\npublic class Commande { }\n",
            "src/Boutique.Api/Program.cs": 'app.MapDelete("/paniers/{panierId}", Vider);',
            "api/openapi.yaml": CONTRACT_YAML,
        },
    )
    report = await _collect(collector, space, url)
    assert report.errors == []
    raw = _raw(tmp_path, space)
    copy = raw / "contracts/api/contrat.full.openapi.yaml"
    assert copy.read_text("utf-8") == CONTRACT_YAML  # le contrat n'est pas modifié
    points = _points(raw)
    assert points["Lire"]["contract"]["operations"] == [
        {
            "file": "api/contrat.full.openapi.yaml",
            "verb": "GET",
            "path": "/commandes/{commandeId}",
            "operationId": "lireCommande",
        }
    ]
    assert points["Changer"]["contract"] == {"status": "absent"}
    assert "contract" not in points["Toutes"]  # route inconnue
    assert points["Vider"]["contract"]["status"] == "linked"  # minimal API, module
    # Sans changement : rien n'est réécrit.
    assert (await _collect(collector, space, url)).local_commit is None

    # Le contrat seul change : le code n'est pas relu, l'état suit le contrat.
    (work / "api/openapi.yaml").write_text(CONTRACT_YAML + STATUT_OP, encoding="utf-8")
    _push(work)
    report = await _collect(collector, space, url)
    assert report.read == 1
    assert _points(raw)["Changer"]["contract"]["operations"][0]["operationId"] == "changerStatut"

    # Plus de contrat : rien à comparer, les états disparaissent.
    (work / "api/openapi.yaml").unlink()
    _push(work)
    await _collect(collector, space, url)
    assert all("contract" not in p for p in _points(raw).values())


# --- Conventions : l'exemple de la documentation est celui que produit le socle ----------


def test_exemple_des_conventions() -> None:
    from tests.test_application_formats import _doc_example

    example = _doc_example("Point d'entrée et contrat OpenAPI")
    contract = {
        "servers": [{"url": "https://boutique.example/api"}],
        "paths": {"/commandes/{commandeId}": {"get": _op("lireCommande")}},
    }
    index = operation_index({"api/contrat.full.openapi.yaml": contract})
    bare = [{**m, "entryPoint": link_point(m["entryPoint"], None)} for m in example["methods"]]
    linked = [{**m, "entryPoint": link_point(m["entryPoint"], index)} for m in bare]
    assert linked == example["methods"]
    assert load_schema(dump_functions(example)) == example  # valide au JSON Schema
