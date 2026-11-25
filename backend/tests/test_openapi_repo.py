"""Contrats OpenAPI trouvés dans une source Git (ticket docflow 56be38c6).

Le parseur `openapi`, choisi sur une source Git à côté des parseurs de code, ne
prend que les YAML / JSON dont la racine porte la clé `openapi` (3.x) ; il
identifie les schémas par le chemin du contrat et sa version déclarée.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ossie_studio.models import GitSpace
from ossie_studio.parsers.conventions import load_schema
from ossie_studio.parsers.openapi import (
    CONTRACT_OPENAPI,
    CONTRACT_SWAGGER,
    inspect_contract,
    repo_prefix,
    sniff_contract,
)
from ossie_studio.parsers.registry import code_plugins, conflicting_glob, select_code_plugins
from ossie_studio.sources.collect import SourceCollector
from tests.test_collect_service import _base_branch, _make_bare_with, _push, _raw, setup

__all__ = ["setup"]  # fixture importée : pytest la trouve dans ce module

_CONTRAT_COMMANDES = """openapi: 3.0.3
info:
  title: API Commandes
  version: 1.2.0
paths:
  /commandes:
    post:
      requestBody:
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Commande'
      responses:
        '201':
          description: créée
components:
  schemas:
    Commande:
      type: object
      properties:
        id:
          type: string
"""

_CONTRAT_FACTURES = """{
  "openapi": "3.1.0",
  "info": {"title": "API Factures", "version": "2"},
  "paths": {},
  "components": {"schemas": {"Commande": {"type": "object",
    "properties": {"numero": {"type": "integer"}}}}}
}
"""

_SWAGGER = """swagger: '2.0'
info:
  title: Vieille API
  version: '0.9'
paths: {}
"""

_CONFIG_YAML = """server:
  port: 8080
  openapi: 3.0.0
"""

_PACKAGE_JSON = '{"name": "front", "version": "1.0.0", "dependencies": {}}'

_MAL_FORME = """openapi: 3.0.3
info:
  title: Cassé
  version: 1.0.0
components:
  schemas:
    A:
      $ref: '#/components/schemas/Absent'
"""


# --- reconnaissance par contenu -------------------------------------------------


def test_sniff_reconnait_la_cle_openapi_a_la_racine() -> None:
    assert sniff_contract("api/commandes.yaml", _CONTRAT_COMMANDES.encode()) == CONTRACT_OPENAPI
    assert sniff_contract("api/factures.json", _CONTRAT_FACTURES.encode()) == CONTRACT_OPENAPI


def test_sniff_ignore_une_cle_openapi_indentee_et_les_autres_fichiers() -> None:
    # `openapi:` sous `server:` n'est pas à la racine : ce n'est pas un contrat.
    assert sniff_contract("config/app.yaml", _CONFIG_YAML.encode()) is None
    assert sniff_contract("front/package.json", _PACKAGE_JSON.encode()) is None
    assert sniff_contract("ci.yml", b"stages: [build]\n") is None
    assert sniff_contract("bin.json", b"\xff\xfe\x00") is None


def test_sniff_signale_swagger_2() -> None:
    assert sniff_contract("old/swagger.yaml", _SWAGGER.encode()) == CONTRACT_SWAGGER
    assert sniff_contract("old/s.json", b'{"swagger": "2.0"}') == CONTRACT_SWAGGER


def test_sniff_garde_un_json_mal_forme_qui_porte_la_cle() -> None:
    # Illisible mais manifestement un contrat : retenu, pour être signalé en erreur.
    assert sniff_contract("api.json", b'{"openapi": "3.0.0", "info": ') == CONTRACT_OPENAPI


def test_repo_prefix_chemin_et_version() -> None:
    assert repo_prefix("services/commandes/openapi.yaml", "1.2.0") == (
        "services-commandes-openapi-1-2-0"
    )
    assert repo_prefix("openapi.json", "") == "openapi"


def test_inspect_contract_decrit_sans_ecrire() -> None:
    found = inspect_contract("api/commandes.yaml", _CONTRAT_COMMANDES.encode())
    assert found is not None
    assert (found.status, found.title, found.version, found.openapi) == (
        "supported",
        "API Commandes",
        "1.2.0",
        "3.0.3",
    )
    swagger = inspect_contract("old/swagger.yaml", _SWAGGER.encode())
    assert swagger is not None and swagger.status == "unsupported"
    assert (swagger.title, swagger.openapi) == ("Vieille API", "2.0")
    broken = inspect_contract("broken.yaml", _MAL_FORME.encode())
    assert broken is not None and (broken.status, broken.error) == ("invalid", "ref_missing")
    assert inspect_contract("front/package.json", _PACKAGE_JSON.encode()) is None


# --- registre ------------------------------------------------------------------


def test_openapi_est_choisissable_sur_une_source_git() -> None:
    assert "openapi" in {p.id for p in code_plugins()}
    [plugin] = select_code_plugins(["openapi"])
    assert "**/*.yaml" in plugin.globs()
    # Aucun conflit déclaré avec les parseurs de code : le contenu départage.
    assert conflicting_glob(["csharp", "openapi", "jsonschema-demo", "markdown"]) is None


# --- collecte ------------------------------------------------------------------


async def _collect(collector: SourceCollector, space: GitSpace, url: str):  # type: ignore[no-untyped-def]
    return await collector.collect(
        space=space,
        branch_slug="work/principale",
        branch_label="principale",
        space_username="x",
        space_token=None,
        source_slug="api",
        source_clone_url=url,
        source_branch="main",
        source_username="x",
        source_token=None,
        author_name="a@a",
        author_email="a@a",
        force=False,
        parsers=["csharp", "openapi"],
    )


def _repo_files() -> dict[str, str]:
    return {
        "src/Commande.cs": "namespace App; public class Ligne { public int Q { get; set; } }",
        "services/commandes/openapi.yaml": _CONTRAT_COMMANDES,
        "services/factures/openapi.json": _CONTRAT_FACTURES,
        "old/swagger.yaml": _SWAGGER,
        "config/app.yaml": _CONFIG_YAML,
        "front/package.json": _PACKAGE_JSON,
    }


async def test_collecte_code_et_contrats_ignore_la_configuration(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    url, _ = _make_bare_with(tmp_path, "src", _repo_files())
    report = await _collect(collector, space, url)
    assert report.errors == []
    raw = _raw(tmp_path, space)
    names = {p.name for p in raw.glob("*.schema.yaml")}
    # Deux objets `Commande` de deux contrats : deux schémas distincts.
    commandes = "services-commandes-openapi-1-2-0-commande.schema.yaml"
    factures = "services-factures-openapi-2-commande.schema.yaml"
    assert {"app-ligne.schema.yaml", commandes, factures} <= names
    schema = load_schema((raw / commandes).read_text(encoding="utf-8"))
    assert schema["x-source"]["file"] == "services/commandes/openapi.yaml"
    assert schema["x-used-by"] == ["POST /commandes"]
    other = load_schema((raw / factures).read_text(encoding="utf-8"))
    assert set(other["properties"]) == {"numero"}
    statuts = {f.path: f.statut for f in report.files}
    # Configuration, package.json et Swagger 2.0 : ignorés, pas en erreur.
    for path in ("config/app.yaml", "front/package.json", "old/swagger.yaml"):
        assert statuts[path] == "ignoré", path


async def test_contrat_modifie_seul_retransforme_puis_supprime(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    url, work = _make_bare_with(tmp_path, "src", _repo_files())
    await _collect(collector, space, url)
    changed = _CONTRAT_FACTURES.replace('"integer"', '"string"')
    (work / "services/factures/openapi.json").write_text(changed, encoding="utf-8")
    _push(work)
    report = await _collect(collector, space, url)
    assert report.read == 1  # seul le contrat modifié est relu
    assert report.modified == 1

    (work / "services/commandes/openapi.yaml").unlink()
    _push(work)
    report = await _collect(collector, space, url)
    assert report.deleted == 1
    raw = _raw(tmp_path, space)
    assert not (raw / "services-commandes-openapi-1-2-0-commande.schema.yaml").exists()
    assert (raw / "services-factures-openapi-2-commande.schema.yaml").exists()


async def test_contrat_mal_forme_signale_les_autres_continuent(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    files = {**_repo_files(), "broken/openapi.yaml": _MAL_FORME}
    url, _ = _make_bare_with(tmp_path, "src", files)
    report = await _collect(collector, space, url)
    assert [e.source for e in report.errors] == ["broken/openapi.yaml"]
    assert report.errors[0].cause == "syntax"
    raw = _raw(tmp_path, space)
    assert (raw / "services-commandes-openapi-1-2-0-commande.schema.yaml").exists()


async def test_rechercher_les_contrats_sans_rien_ecrire(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    url, _ = _make_bare_with(tmp_path, "src", _repo_files())
    found = await collector.find_contracts(
        space=space,
        source_slug="api",
        source_clone_url=url,
        source_branch="main",
        source_username="x",
        source_token=None,
    )
    assert [(c.path, c.status) for c in found] == [
        ("old/swagger.yaml", "unsupported"),
        ("services/commandes/openapi.yaml", "supported"),
        ("services/factures/openapi.json", "supported"),
    ]
    assert found[1].title == "API Commandes" and found[2].openapi == "3.1.0"
    # Aucune vue de brouillon : rien n'a été écrit.
    with pytest.raises(AssertionError, match="aucune vue"):
        _raw(tmp_path, space)


async def test_rechercher_respecte_les_exclusions(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    url, _ = _make_bare_with(tmp_path, "src", _repo_files())
    found = await collector.find_contracts(
        space=space,
        source_slug="api",
        source_clone_url=url,
        source_branch="main",
        source_username="x",
        source_token=None,
        exclusions=["services/factures", "old"],
    )
    assert [c.path for c in found] == ["services/commandes/openapi.yaml"]
