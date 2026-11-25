"""Modules, méthodes et points d'entrée TypeScript (epic « Cartographie applicative
dans raw », ticket f619f4a7) : un objet module par `package.json`, les méthodes des
classes exportées dans leur `.extend.yaml`, les fonctions exportées et les routes
dans le `.extend.yaml` du module, les points d'entrée NestJS et Express."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ossie_studio.models import GitSpace
from ossie_studio.parsers.application import module_key
from ossie_studio.parsers.conventions import load_schema, schema_file
from ossie_studio.parsers.ts_entrypoints import (
    Decorator,
    join_routes,
    literal,
    nest_entry_point,
    normalize,
)
from ossie_studio.parsers.typescript import TypeScriptParser, file_functions
from ossie_studio.sources.collect import SourceCollector
from ossie_studio.sources.explorer import clear_caches, collect_source_entries
from ossie_studio.sources.explorer_application import members_by_module
from ossie_studio.sources.npm_modules import npm_module_actions, read_npm_projects
from tests.test_collect_service import _base_branch, _make_bare_with, _push, _raw
from tests.test_collect_service import setup as setup  # fixture réutilisée


def _json(data: dict[str, Any]) -> bytes:
    return json.dumps(data).encode()


API = "packages/api/package.json"
DOMAIN = "packages/domain/package.json"
WEB = "packages/web/package.json"
MONOREPO = {
    "package.json": _json({"name": "boutique", "private": True, "workspaces": ["packages/*"]}),
    API: _json(
        {
            "name": "@boutique/api",
            "scripts": {"start": "node dist/main.js"},
            "dependencies": {
                "@nestjs/core": "^10",
                "@boutique/domain": "workspace:*",
                "lodash": "^4",
            },
            "devDependencies": {"typescript": "^5"},
            "engines": {"node": ">=20"},
        }
    ),
    DOMAIN: _json({"name": "@boutique/domain", "devDependencies": {"typescript": "^5"}}),
    WEB: _json(
        {
            "name": "@boutique/web",
            "dependencies": {"react": "^18", "@boutique/domain": "*", "boutique": "*"},
        }
    ),
}


def _modules(files: dict[str, bytes]) -> dict[str, dict[str, Any]]:
    step = npm_module_actions({}, [], [], read_npm_projects(files))
    return {w.path: load_schema(w.content) for w in step.writes}


# --- Critère 1 : monorepo à trois paquets --------------------------------------------------


def test_monorepo_trois_paquets() -> None:
    modules = _modules({**MONOREPO, "node_modules/x/package.json": _json({"name": "x"})})
    domain_id = schema_file(module_key(DOMAIN))
    # Trois modules ; la racine du monorepo (agrégateur) et node_modules n'en sont pas.
    assert set(modules) == {schema_file(module_key(p)) for p in (API, DOMAIN, WEB)}
    api = modules[schema_file(module_key(API))]
    assert (api["title"], api["x-language"]) == ("@boutique/api", "typescript")
    # lodash (externe) est ignoré ; seule la dépendance interne est reliée.
    assert api["x-module"] == {
        "project": API,
        "nature": "web",
        "targets": ["node20"],
        "dependencies": [{"project": DOMAIN, "module": domain_id}],
    }
    web = modules[schema_file(module_key(WEB))]
    # La dépendance vers la racine (agrégateur) n'est pas un lien de module.
    assert web["x-module"]["dependencies"] == [{"project": DOMAIN, "module": domain_id}]
    assert modules[domain_id]["x-module"]["nature"] == "library"


def test_package_illisible_et_racine_avec_du_code() -> None:
    projects = read_npm_projects({"casse/package.json": b'{"name": '})
    assert projects["casse/package.json"].error == "build_file_unreadable"
    with_code = _modules({**MONOREPO, "src/index.ts": b"export const x = 1"})
    assert schema_file(module_key("package.json")) in with_code


# --- Critères 2 et 4 : NestJS ---------------------------------------------------------------

CONTROLLER = b"""
@Controller('commandes')
export class CommandesController {
  constructor(private readonly service: CommandesService) {}

  /** Lit une commande. */
  @Get(':id')
  async lire(@Param('id') id: string, inclure?: boolean): Promise<Commande> {
    return this.service.lire(id)
  }

  @Post()
  creer(@Body() commande: Commande): Commande { return commande }

  @Get(ROUTES.TOUTES)
  toutes() {}

  @Cron('0 * * * *')
  nettoyer() {}

  @MessagePattern('commande.creee')
  reagir() {}

  static calculer(lignes: number[]): number { return 0 }
  protected aide() {}
  private cache(): void {}
  #secret() {}
  get total() { return 1 }
}

class Interne { utile() {} }
"""


def _methods(source: bytes) -> dict[str, dict[str, Any]]:
    [ctrl] = [o for o in TypeScriptParser().parse({"src/commandes.ts": source}) if o.extend]
    return {m["name"]: m for m in load_schema(ctrl.extend)["methods"]}


def test_controleur_nestjs() -> None:
    methods = _methods(CONTROLLER)
    lire = methods["lire"]
    assert lire["entryPoint"] == {"kind": "http", "verb": "GET", "route": "/commandes/{id}"}
    assert lire["async"] is True
    assert lire["returns"] == {
        "type": "Promise<Commande>",
        "schema": {"x-unresolved-type": "Commande"},
    }
    assert [(p["name"], p.get("optional", False)) for p in lire["parameters"]] == [
        ("id", False),
        ("inclure", True),
    ]
    assert lire["description"] == "Lit une commande."
    assert methods["creer"]["entryPoint"]["route"] == "/commandes"
    # Critère 4 : route non littérale → point d'entrée gardé, route inconnue.
    assert methods["toutes"]["entryPoint"] == {"kind": "http", "verb": "GET", "route": None}
    assert methods["nettoyer"]["entryPoint"] == {"kind": "task"}
    assert methods["reagir"]["entryPoint"] == {"kind": "message", "topic": "commande.creee"}
    assert methods["calculer"]["static"] is True
    assert methods["aide"]["visibility"] == "protected"
    # Ni privées, ni #privées, ni accesseurs, ni constructeur.
    assert set(methods) == {"lire", "creer", "toutes", "nettoyer", "reagir", "calculer", "aide"}


def test_classe_non_exportee_sans_fonctions() -> None:
    objects = {o.key: o for o in TypeScriptParser().parse({"src/commandes.ts": CONTROLLER})}
    assert objects["src-commandes-interne"].extend is None


# --- Critères 3 et 4 : Express et fonctions exportées ---------------------------------------

ROUTES = b"""
import express, { Router } from 'express'

/** Total d'une commande. */
export function total(lignes: Ligne[], remise = 0): number { return 0 }
export const vider = async (id: string): Promise<void> => {}
function interne() {}

const app = express()
const paniers = Router()
router.post('/paniers', creer)
app.get(ROUTES.TOUT, (req, res) => res.send())
paniers.delete('/paniers/:id', controleur.supprimer)
cache.get('cle', valeurParDefaut)
map.get(cle)
"""


def test_routes_express_et_fonctions_exportees() -> None:
    functions = {f["name"]: f for f in file_functions("src/routes.ts", ROUTES)}
    assert set(functions) == {"total", "vider", "creer", "GET ?", "controleur.supprimer"}
    assert functions["creer"]["entryPoint"] == {"kind": "http", "verb": "POST", "route": "/paniers"}
    assert functions["controleur.supprimer"]["entryPoint"]["route"] == "/paniers/{id}"
    # Route non littérale : gardée, route inconnue.
    assert functions["GET ?"]["entryPoint"] == {"kind": "http", "verb": "GET", "route": None}
    total = functions["total"]
    assert total["description"] == "Total d'une commande."
    assert total["returns"]["schema"] == {"type": "number"}
    assert [(p["name"], p.get("optional", False)) for p in total["parameters"]] == [
        ("lignes", False),
        ("remise", True),
    ]
    assert functions["vider"]["async"] is True and "returns" not in functions["vider"]
    assert functions["creer"]["source"] == {"file": "src/routes.ts", "line": 11}


def test_regles_pures() -> None:
    assert normalize("/commandes/:id/lignes/:n?") == "/commandes/{id}/lignes/{n}"
    assert join_routes("commandes", ":id") == "/commandes/{id}"
    assert literal("'x'") == "x" and literal("`x`") == "x"
    assert literal("`a${b}`") is None and literal("ROUTES.X") is None
    owner = [Decorator("Controller", ("{ path: 'paniers', version: '1' }",))]
    assert nest_entry_point([Decorator("Delete", ("':id'",))], owner) == {
        "kind": "http",
        "verb": "DELETE",
        "route": "/paniers/{id}",
    }
    assert nest_entry_point([Decorator("Get")], []) is None  # pas de @Controller


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
        parsers=["typescript"],
    )


async def test_collecte_modules_et_fonctions(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    files = {path: content.decode() for path, content in MONOREPO.items()}
    files["packages/api/src/commandes.controller.ts"] = CONTROLLER.decode()
    files["packages/api/src/routes.ts"] = ROUTES.decode()
    files["packages/domain/src/commande.ts"] = "export interface Commande { ref: string }\n"
    url, work = _make_bare_with(tmp_path, "src", files)
    report = await _collect(collector, space, url)
    assert report.errors == []
    raw = _raw(tmp_path, space)
    api_id = schema_file(module_key(API))
    assert (raw / api_id).is_file()
    # Les fonctions exportées et les routes vont dans le `.extend.yaml` du module.
    module_functions = load_schema((raw / f"{module_key(API)}.extend.yaml").read_text())
    names = {f["name"] for f in module_functions["functions"]}
    assert {"total", "vider", "creer", "controleur.supprimer"} <= names
    # Le type du retour, dans un autre paquet, est résolu par le socle.
    extend = load_schema(
        (raw / "packages-api-src-commandes-controller-commandescontroller.extend.yaml").read_text()
    )
    lire = next(m for m in extend["methods"] if m["name"] == "lire")
    assert lire["returns"]["schema"] == {
        "$ref": "packages-domain-src-commande-commande.schema.yaml"
    }
    clear_caches()
    members = members_by_module(collect_source_entries(raw.parents[2], "api"))
    assert {o.title for o in members[api_id]} == {"CommandesController", "Interne"}
    assert [o.title for o in members[schema_file(module_key(DOMAIN))]] == ["Commande"]
    # Sans changement : rien n'est réécrit.
    assert (await _collect(collector, space, url)).local_commit is None
    # Le paquet web retiré : son module part.
    (work / WEB).unlink()
    _push(work)
    await _collect(collector, space, url)
    assert not (raw / schema_file(module_key(WEB))).exists()
    assert (raw / api_id).is_file()


async def test_modules_seulement_avec_le_parseur_du_langage(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    """Un module de langage n'est produit que si le parseur de ce langage est choisi
    pour la source ; retiré du choix, ses modules partent."""
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    files = {path: content.decode() for path, content in MONOREPO.items()}
    files["packages/api/src/main.ts"] = "export function demarrer() {}\n"
    url, _work = _make_bare_with(tmp_path, "src", files)
    await _collect(collector, space, url)
    api_module = _raw(tmp_path, space) / schema_file(module_key(API))
    assert api_module.is_file()

    async def collect_with(parsers: list[str]):  # type: ignore[no-untyped-def]
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
            parsers=parsers,
        )

    report = await collect_with(["openapi"])
    assert not api_module.exists()
    assert {f.path: f.statut for f in report.files}[API] == "ignoré"
