"""Modules, méthodes et points d'entrée Python (epic « Cartographie applicative
dans raw », ticket 3343afe4) : un objet module par `pyproject.toml` (à défaut
`setup.cfg` / `setup.py`), les méthodes publiques des classes dans leur
`.extend.yaml`, les fonctions publiques et points d'entrée (FastAPI, Flask, Celery)
dans le `.extend.yaml` du module."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ossie_studio.models import GitSpace
from ossie_studio.parsers.application import module_key
from ossie_studio.parsers.conventions import load_schema, schema_file
from ossie_studio.parsers.py_entrypoints import Decorator, Router, entry_points, normalize
from ossie_studio.parsers.python import PythonParser, file_functions
from ossie_studio.sources.collect import SourceCollector
from ossie_studio.sources.explorer import clear_caches, collect_source_entries
from ossie_studio.sources.explorer_application import members_by_module
from ossie_studio.sources.python_modules import python_module_actions, read_python_projects
from tests.test_collect_service import _base_branch, _make_bare_with, _push, _raw
from tests.test_collect_service import setup as setup  # fixture réutilisée

API = "services/api/pyproject.toml"
DOMAIN = "services/domain/pyproject.toml"
PROJECTS = {
    API: b"""[project]
name = "boutique-api"
requires-python = ">=3.12"
dependencies = ["fastapi>=0.110", "requests"]

[tool.uv.sources]
boutique-domain = { path = "../domain" }
""",
    DOMAIN: b'[project]\nname = "boutique_domain"\n',
}


def _modules(files: dict[str, bytes]) -> dict[str, dict[str, Any]]:
    step = python_module_actions({}, [], [], read_python_projects(files))
    return {w.path: load_schema(w.content) for w in step.writes}


# --- Critère 1 : deux projets reliés par chemin ---------------------------------------------


def test_deux_projets_relies_par_chemin() -> None:
    modules = _modules(PROJECTS)
    domain_id = schema_file(module_key(DOMAIN))
    assert set(modules) == {schema_file(module_key(API)), domain_id}
    api = modules[schema_file(module_key(API))]
    assert (api["title"], api["x-language"]) == ("boutique-api", "python")
    # `requests` (externe) est ignoré ; le chemin `../domain` relie le module.
    assert api["x-module"] == {
        "project": API,
        "nature": "web",
        "targets": ["python3.12"],
        "dependencies": [{"project": DOMAIN, "module": domain_id}],
    }
    assert modules[domain_id]["x-module"]["nature"] == "library"


def test_dependance_par_nom_setup_cfg_setup_py_et_agregateur() -> None:
    files = {
        "pyproject.toml": b'[tool.uv.workspace]\nmembers = ["libs/*"]\n',
        "libs/outils/setup.cfg": b"[metadata]\nname = Outils\n[options]\ninstall_requires =\n"
        b"    celery>=5\n",
        "libs/vieux/setup.py": b'setup(name="vieux", install_requires=["outils"],\n'
        b'      entry_points={"console_scripts": ["v=v:main"]})\n',
        "casse/pyproject.toml": b"[project\nname=",
    }
    projects = read_python_projects(files)
    # La racine de l'espace de travail uv, sans projet à elle, n'est pas un module.
    assert "pyproject.toml" not in projects
    assert projects["casse/pyproject.toml"].error == "build_file_unreadable"
    assert projects["libs/outils/setup.cfg"].nature == "worker"
    vieux = projects["libs/vieux/setup.py"]
    # Nom normalisé (PEP 503) : « outils » répond à « Outils ».
    assert (vieux.nature, vieux.references) == ("application", ["libs/outils/setup.cfg"])


# --- Critères 2 à 4 : FastAPI, Celery, méthodes privées -------------------------------------

APP = b'''from fastapi import APIRouter
from celery import Celery, shared_task

router = APIRouter(prefix="/commandes")
bp = Blueprint("paniers", __name__, url_prefix="/paniers")
celery_app = Celery("boutique")


@router.get("/{id}")
async def lire(id: UUID, inclure: bool = False) -> Commande:
    """Lit une commande."""


@router.post(PREFIXE + "/brouillons")
def creer(commande: "Commande") -> None: ...


@bp.route("/<int:panier_id>", methods=["GET", "DELETE"])
def panier(panier_id): ...


@shared_task(bind=True)
def purger(self, avant: str): ...


@celery_app.task
def relancer(): ...


@cache.get("cle")
def calculer_total(lignes: list[int]) -> int: ...


def _interne(): ...


class ServiceCommandes:
    """Service des commandes."""

    def __init__(self, depot): ...

    def lire(self, id: str) -> "Commande": ...

    @staticmethod
    def arrondir(montant: float) -> float: ...

    @property
    def total(self) -> int: ...

    def _privee(self): ...


class _Cache:
    def vider(self): ...
'''


def _functions() -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for f in file_functions("app/api.py", APP):
        out.setdefault(f["name"], []).append(f)
    return out


def test_fastapi_flask_celery() -> None:
    functions = _functions()
    [lire] = functions["lire"]
    assert lire["entryPoint"] == {"kind": "http", "verb": "GET", "route": "/commandes/{id}"}
    assert (lire["async"], lire["description"]) == (True, "Lit une commande.")
    assert [(p["name"], p.get("optional", False)) for p in lire["parameters"]] == [
        ("id", False),
        ("inclure", True),
    ]
    # Route non littérale : point d'entrée gardé, route inconnue.
    assert functions["creer"][0]["entryPoint"] == {"kind": "http", "verb": "POST", "route": None}
    # Flask : un point d'entrée par verbe, `<int:panier_id>` → `{panier_id}`.
    assert [f["entryPoint"] for f in functions["panier"]] == [
        {"kind": "http", "verb": "GET", "route": "/paniers/{panier_id}"},
        {"kind": "http", "verb": "DELETE", "route": "/paniers/{panier_id}"},
    ]
    # Critère 3 : tâches Celery ; tâche liée, sans son premier paramètre.
    [purger] = functions["purger"]
    assert purger["entryPoint"] == {"kind": "task"}
    assert [p["name"] for p in purger["parameters"]] == ["avant"]
    assert functions["relancer"][0]["entryPoint"] == {"kind": "task"}
    # `cache.get` n'est pas un routeur ; la fonction reste, publique, sans point d'entrée.
    assert "entryPoint" not in functions["calculer_total"][0]
    assert "_interne" not in functions


def test_methodes_publiques_et_objet_de_service() -> None:
    objects = {o.key: o for o in PythonParser().parse({"app/api.py": APP})}
    service = objects["app-api-servicecommandes"]
    methods = {m["name"]: m for m in load_schema(service.extend)["methods"]}
    # Critère 4 : `_privee` (et `__init__`, l'accesseur) ne sont pas écrites.
    assert set(methods) == {"lire", "arrondir"}
    assert methods["lire"]["parameters"] == [
        {"name": "id", "type": "str", "schema": {"type": "string"}}
    ]
    assert methods["arrondir"]["static"] is True
    assert "app-api-cache" not in objects  # classe interne : pas d'objet


def test_regles_pures() -> None:
    assert normalize("/x/<int:id>/<nom>") == "/x/{id}/{nom}"
    routers = {"api": Router("APIRouter", None)}  # préfixe non littéral
    assert entry_points(Decorator("api", "get", ('"/x"',)), routers) == [
        {"kind": "http", "verb": "GET", "route": None}
    ]
    assert entry_points(Decorator("app", "route", ('"/x"',)), {}) == [
        {"kind": "http", "verb": "GET", "route": "/x"}
    ]
    assert entry_points(Decorator("client", "get", ('"/x"',)), {}) == []


# --- Collecte de bout en bout --------------------------------------------------------------


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
        parsers=["python"],
    )


async def test_collecte_modules_et_fonctions(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    files = {path: content.decode() for path, content in PROJECTS.items()}
    files["services/api/src/boutique_api/routes.py"] = APP.decode()
    files["services/domain/src/boutique_domain/commande.py"] = (
        "from dataclasses import dataclass\n\n@dataclass\nclass Commande:\n    ref: str\n"
    )
    url, work = _make_bare_with(tmp_path, "src", files)
    report = await _collect(collector, space, url)
    assert report.errors == []
    raw = _raw(tmp_path, space)
    api_id = schema_file(module_key(API))
    # Les fonctions publiques et les points d'entrée vont dans le `.extend.yaml` du module.
    module_functions = load_schema((raw / f"{module_key(API)}.extend.yaml").read_text())
    names = {f["name"] for f in module_functions["functions"]}
    assert {"lire", "creer", "panier", "purger", "relancer", "calculer_total"} <= names
    lire = next(f for f in module_functions["functions"] if f["name"] == "lire")
    # Le type du retour, dans l'autre projet, est résolu par le socle.
    assert lire["returns"]["schema"] == {
        "$ref": "services-domain-src-boutique-domain-commande-commande.schema.yaml"
    }
    clear_caches()
    members = members_by_module(collect_source_entries(raw.parents[2], "api"))
    assert [o.title for o in members[api_id]] == ["ServiceCommandes"]
    assert [o.title for o in members[schema_file(module_key(DOMAIN))]] == ["Commande"]
    # Sans changement : rien n'est réécrit.
    assert (await _collect(collector, space, url)).local_commit is None
    # Le projet domaine retiré : son module part.
    (work / DOMAIN).unlink()
    _push(work)
    await _collect(collector, space, url)
    assert not (raw / schema_file(module_key(DOMAIN))).exists()
    assert (raw / api_id).is_file()
