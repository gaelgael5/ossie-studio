"""Méthodes publiques et points d'entrée C# (epic « Cartographie applicative dans
raw », ticket f1cc03c4) : le parseur écrit le `<clé>.extend.yaml` de chaque type
(méthodes, actions de contrôleur, service d'arrière-plan) ; les points d'entrée
minimal API vont dans le `.extend.yaml` du module ; le `.schema.yaml` ne change
pas."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ossie_studio.models import GitSpace
from ossie_studio.parsers.application import module_key
from ossie_studio.parsers.conventions import load_schema
from ossie_studio.parsers.csharp import CSharpParser, minimal_api
from ossie_studio.parsers.csharp_entrypoints import controller_route, literal
from ossie_studio.sources.collect import SourceCollector
from tests.test_collect_service import _base_branch, _collect, _make_bare_with, _push, _raw
from tests.test_collect_service import setup as setup  # fixture réutilisée

CONTROLLER = """namespace Boutique.Api.Controllers;

[ApiController]
[Route("api/[controller]")]
public class CommandesController : ControllerBase
{
    /// <summary>Lit une commande.</summary>
    [HttpGet("{id}")]
    public async Task<ActionResult<Commande>> Lire(Guid id) => null;

    [HttpPut("{id}/statut")]
    public IActionResult Changer(Guid id, string statut, bool notifier = false) => Ok();

    [HttpGet(Routes.Toutes)]
    public List<Commande> Toutes() => null;

    [HttpDelete("~/admin/purge")]
    public void Purger() { }

    internal static int Calculer(List<int> lignes) => 0;

    private void Cache() { }
}
"""

COMMANDE = "namespace Boutique.Api;\npublic class Commande { public Guid Id { get; set; } }\n"


def _extend(path: str, source: str) -> dict[str, dict[str, Any]]:
    """Clé → document `.extend.yaml` des objets d'un fichier."""
    objects = CSharpParser().parse({path: source.encode()})
    return {o.key: load_schema(o.extend) for o in objects if o.extend}


def _methods(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["name"]: m for m in doc.get("methods", [])}


# --- Critère 1 : action de contrôleur ------------------------------------------------------


def test_action_de_controleur() -> None:
    doc = _extend("src/Boutique.Api/Controllers/CommandesController.cs", CONTROLLER)[
        "boutique-api-controllers-commandescontroller"
    ]
    lire = _methods(doc)["Lire"]
    assert lire["entryPoint"] == {"kind": "http", "verb": "GET", "route": "/api/commandes/{id}"}
    assert lire["parameters"] == [
        {"name": "id", "type": "Guid", "schema": {"type": "string", "format": "uuid"}}
    ]
    assert (lire["async"], lire["description"]) == (True, "Lit une commande.")
    # Task<ActionResult<Commande>> : le vrai résultat est Commande.
    assert lire["returns"]["schema"] == {"x-unresolved-type": "Commande"}
    assert lire["source"] == {
        "file": "src/Boutique.Api/Controllers/CommandesController.cs",
        "line": 9,
    }
    changer = _methods(doc)["Changer"]
    assert changer["entryPoint"]["route"] == "/api/commandes/{id}/statut"
    assert [p.get("optional", False) for p in changer["parameters"]] == [False, False, True]
    # Gabarit qui commence par « ~/ » : il remplace la route de la classe.
    assert _methods(doc)["Purger"]["entryPoint"]["route"] == "/admin/purge"


def test_routes_jetons_et_litteraux() -> None:
    assert controller_route("api/[controller]", "[action]/{id}", controller="CommandesController",
                            action="Lire") == "/api/commandes/lire/{id}"  # fmt: skip
    assert controller_route(None, None, controller="X", action="Y") is None
    assert literal('@"a""b"') == 'a"b'
    assert literal('$"/x/{v}"') is None


# --- Critère 2 : minimal API, préfixe de MapGroup --------------------------------------------


def test_minimal_api_avec_map_group() -> None:
    program = (
        "var app = builder.Build();\n"
        'app.MapGroup("/paniers").MapDelete("/{id}", (Guid id) => Results.Ok());\n'
        'var api = app.MapGroup("/api");\n'
        'var v1 = api.MapGroup("v1");\n'
        'v1.MapGet("/commandes/{id}", Commandes.Lire).WithName("lire");\n'
        'app.MapPost($"/dyn/{x}", Faire);\n'
    )
    functions = {f["name"]: f for f in minimal_api("src/Boutique.Api/Program.cs", program.encode())}
    assert functions["DELETE /paniers/{id}"]["entryPoint"] == {
        "kind": "http",
        "verb": "DELETE",
        "route": "/paniers/{id}",
    }
    assert functions["DELETE /paniers/{id}"]["parameters"][0]["type"] == "Guid"
    assert functions["Lire"]["entryPoint"]["route"] == "/api/v1/commandes/{id}"
    # Critère 7 : route impossible à calculer, point d'entrée gardé.
    assert functions["Faire"]["entryPoint"]["route"] is None


# --- Critère 3 : service d'arrière-plan ----------------------------------------------------


def test_service_d_arriere_plan() -> None:
    source = (
        "namespace App;\n"
        "public class Nettoyage : BackgroundService {\n"
        "  protected override Task ExecuteAsync(CancellationToken jeton) => Task.CompletedTask;\n"
        "}\n"
    )
    doc = _extend("Nettoyage.cs", source)["app-nettoyage"]
    assert doc["entryPoint"] == {"kind": "task"}
    assert _methods(doc)["ExecuteAsync"]["visibility"] == "protected"


# --- Critère 4 : visibilités ---------------------------------------------------------------


def test_private_absente_internal_avec_sa_visibilite() -> None:
    doc = _extend("C.cs", CONTROLLER)["boutique-api-controllers-commandescontroller"]
    methods = _methods(doc)
    assert "Cache" not in methods
    assert (methods["Calculer"]["visibility"], methods["Calculer"]["static"]) == ("internal", True)
    assert "entryPoint" not in methods["Calculer"]
    # Membre d'interface sans mot-clé : public.
    interface = _extend("I.cs", "namespace App; public interface IDepot { Commande Trouver(); }")
    assert _methods(interface["app-idepot"])["Trouver"]["visibility"] == "public"


# --- Critère 5 : le .schema.yaml ne change pas -----------------------------------------------


def test_le_schema_de_la_classe_ignore_les_methodes() -> None:
    with_methods = "namespace App; public class A { public int N { get; set; } public void F() {} }"
    without = "namespace App; public class A { public int N { get; set; } }"
    [a1] = CSharpParser().parse({"a.cs": with_methods.encode()})
    [a2] = CSharpParser().parse({"a.cs": without.encode()})
    assert a1.schema == a2.schema
    assert a1.extend is not None and a2.extend is None


# --- Critère 6 : fichier incomplet ---------------------------------------------------------


def test_fichier_incomplet_methodes_lisibles_ecrites() -> None:
    broken = (
        "namespace App;\n"
        "public class A {\n"
        "  public int Bonne(int x) => x;\n"
        "  public int Cassee(int x { return x; }\n"
        '  public string Autre() => "";\n'
        "}\n"
    )
    names = set(_methods(_extend("a.cs", broken)["app-a"]))
    assert {"Bonne", "Autre"} <= names
    assert "Cassee" not in names


# --- À la collecte : rangement, résolution, incrémental --------------------------------------

API = "src/Boutique.Api/Boutique.Api.csproj"
PROGRAM = "src/Boutique.Api/Program.cs"


async def test_collecte_fonctions_du_module_et_des_classes(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    src, work = _make_bare_with(
        tmp_path,
        "src",
        {
            API: '<Project Sdk="Microsoft.NET.Sdk.Web"></Project>',
            "src/Boutique.Api/Controllers/CommandesController.cs": CONTROLLER,
            "src/Boutique.Api/Commande.cs": COMMANDE,
            PROGRAM: 'app.MapGroup("/paniers").MapDelete("/{id}", Vider);',
        },
    )
    await _collect(collector, space, src)
    raw = _raw(tmp_path, space)
    controller = load_schema(
        (raw / "boutique-api-controllers-commandescontroller.extend.yaml").read_text("utf-8")
    )
    # Le type du retour, dans un autre fichier, est résolu par le socle.
    lire = _methods(controller)["Lire"]
    assert lire["returns"]["schema"] == {"$ref": "boutique-api-commande.schema.yaml"}
    # La classe Commande n'a pas de méthode : pas de .extend.yaml.
    assert not (raw / "boutique-api-commande.extend.yaml").exists()
    # Les fonctions minimal API dans le .extend.yaml du module.
    module_extend = raw / f"{module_key(API)}.extend.yaml"
    functions = load_schema(module_extend.read_text("utf-8"))["functions"]
    assert [(f["name"], f["entryPoint"]["route"]) for f in functions] == [
        ("Vider", "/paniers/{id}")
    ]
    # Collecte sans changement : rien n'est réécrit.
    assert (await _collect(collector, space, src)).local_commit is None
    # Program.cs supprimé : le module n'a plus de fonction libre.
    (work / PROGRAM).unlink()
    # Le contrôleur perd toutes ses méthodes : son .extend.yaml part, son schéma reste.
    (work / "src/Boutique.Api/Controllers/CommandesController.cs").write_text(
        "namespace Boutique.Api.Controllers;\npublic class CommandesController { }\n",
        encoding="utf-8",
    )
    _push(work)
    await _collect(collector, space, src)
    assert not module_extend.exists()
    assert not (raw / "boutique-api-controllers-commandescontroller.extend.yaml").exists()
    assert (raw / "boutique-api-controllers-commandescontroller.schema.yaml").is_file()
