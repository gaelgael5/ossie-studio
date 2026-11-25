"""Modules, méthodes et points d'entrée Kotlin / Java (epic « Cartographie
applicative dans raw », ticket e34e5c29) : un objet module par projet Gradle ou
Maven, les méthodes publiques dans le `.extend.yaml` des classes, les points
d'entrée Spring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ossie_studio.models import GitSpace
from ossie_studio.parsers.application import module_key
from ossie_studio.parsers.base import OutputAction
from ossie_studio.parsers.conventions import load_schema, schema_file
from ossie_studio.parsers.jvm_entrypoints import Annotation, entry_point, join_routes, literal
from ossie_studio.parsers.kotlin_java import KotlinJavaParser
from ossie_studio.sources.collect import SourceCollector
from ossie_studio.sources.explorer import clear_caches, collect_source_entries
from ossie_studio.sources.explorer_application import members_by_module
from ossie_studio.sources.jvm_modules import jvm_module_actions, read_jvm_projects
from tests.test_collect_service import _base_branch, _make_bare_with, _push, _raw
from tests.test_collect_service import setup as setup  # fixture réutilisée

SETTINGS = b"""rootProject.name = "boutique"
include(":api", ":domain")
include(":services:facturation")
// include(":ancien")
"""
ROOT_BUILD = b'plugins { id("org.springframework.boot") version "3.2.0" apply false }'
API_BUILD = b"""plugins { id("org.springframework.boot"); kotlin("jvm") }
dependencies {
    implementation(project(":domain"))
    implementation(projects.services.facturation)
    implementation("org.springframework.boot:spring-boot-starter-web")
}
kotlin { jvmToolchain(21) }
"""
DOMAIN_BUILD = b"java { toolchain { languageVersion.set(JavaLanguageVersion.of(17)) } }"
BILLING_BUILD = b"apply plugin: 'java'\ndependencies { implementation project(':domain') }\n"

GRADLE = {
    "settings.gradle.kts": SETTINGS,
    "build.gradle.kts": ROOT_BUILD,
    "api/build.gradle.kts": API_BUILD,
    "domain/build.gradle.kts": DOMAIN_BUILD,
    "services/facturation/build.gradle": BILLING_BUILD,
}


def _modules(files: dict[str, bytes]) -> dict[str, dict[str, Any]]:
    step = jvm_module_actions({}, [], [], read_jvm_projects(files))
    return {w.path: load_schema(w.content) for w in step.writes}


# --- Critère 1 : Gradle multi-modules ------------------------------------------------------


def test_gradle_multi_modules() -> None:
    modules = _modules({**GRADLE, "api/src/main/kotlin/App.kt": b"class App"})
    api = modules[schema_file(module_key("api/build.gradle.kts"))]
    domain_id = schema_file(module_key("domain/build.gradle.kts"))
    billing_id = schema_file(module_key("services/facturation/build.gradle"))
    # Un module par sous-projet ; la racine, sans code à elle, n'en est pas un.
    assert set(modules) == {schema_file(module_key("api/build.gradle.kts")), domain_id, billing_id}
    assert (api["title"], api["x-language"]) == ("api", "kotlin")
    assert api["x-module"] == {
        "project": "api/build.gradle.kts",
        "nature": "web",
        "targets": ["java21"],
        "dependencies": [
            {"project": "domain/build.gradle.kts", "module": domain_id},
            {"project": "services/facturation/build.gradle", "module": billing_id},
        ],
    }
    billing = modules[billing_id]
    assert (billing["title"], billing["x-module"]["nature"]) == ("facturation", "library")
    assert billing["x-module"]["dependencies"] == [
        {"project": "domain/build.gradle.kts", "module": domain_id}
    ]
    assert modules[domain_id]["x-module"]["targets"] == ["java17"]


def test_build_gradle_seul_et_racine_avec_du_code() -> None:
    # Sans `settings` : un seul projet. Une racine qui a son propre `src/` est un module.
    alone = _modules({"outil/build.gradle": b"apply plugin: 'application'"})
    assert list(alone) == [schema_file(module_key("outil/build.gradle"))]
    with_code = _modules({**GRADLE, "src/main/java/Racine.java": b"class Racine {}"})
    assert schema_file(module_key("build.gradle.kts")) in with_code


# --- Critère 4 : Maven, modules et dépendances internes ------------------------------------

PARENT = b"""<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>fr.boutique</groupId><artifactId>parent</artifactId><packaging>pom</packaging>
  <parent><groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId></parent>
  <properties><java.version>17</java.version></properties>
  <modules><module>web</module><module>coeur</module></modules>
</project>"""
WEB = b"""<project><parent><groupId>fr.boutique</groupId><artifactId>parent</artifactId></parent>
  <artifactId>web</artifactId>
  <dependencies>
    <dependency><groupId>${project.groupId}</groupId><artifactId>coeur</artifactId></dependency>
    <dependency><groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId></dependency>
  </dependencies>
  <build><plugins><plugin><artifactId>spring-boot-maven-plugin</artifactId></plugin></plugins></build>
</project>"""
CORE = b"""<project><parent><groupId>fr.boutique</groupId><artifactId>parent</artifactId></parent>
  <artifactId>coeur</artifactId></project>"""


def test_maven_modules_et_dependances() -> None:
    modules = _modules({"pom.xml": PARENT, "web/pom.xml": WEB, "coeur/pom.xml": CORE})
    core_id = schema_file(module_key("coeur/pom.xml"))
    # L'agrégateur (`packaging pom`) n'est pas un module.
    assert set(modules) == {schema_file(module_key("web/pom.xml")), core_id}
    web = modules[schema_file(module_key("web/pom.xml"))]
    assert web["x-module"] == {
        "project": "web/pom.xml",
        "nature": "web",
        "targets": ["java17"],  # hérité du parent
        "dependencies": [{"project": "coeur/pom.xml", "module": core_id}],
    }
    assert modules[core_id]["x-module"]["nature"] == "library"


def test_fichier_de_build_illisible_et_module_disparu() -> None:
    old_module = OutputAction(
        schema_file(module_key("ancien/pom.xml")),
        (
            "$id: module-ancien-pom-xml.schema.yaml\ntitle: ancien\nx-kind: module\n"
            "x-language: java\nx-source:\n- file: ancien/pom.xml\n"
            "x-module:\n  project: ancien/pom.xml\n  nature: library\n  dependencies: []\n"
        ),
    )
    broken = OutputAction(
        schema_file(module_key("casse/pom.xml")),
        old_module.content.replace("ancien", "casse"),
    )
    existing = {old_module.path: old_module.content, broken.path: broken.content}
    projects = read_jvm_projects({"casse/pom.xml": b"<project><artifactId>x</"})
    step = jvm_module_actions(existing, [], [], projects)
    assert step.errors == {"casse/pom.xml": "build_file_unreadable"}
    # Le module d'un pom disparu part ; celui d'un pom illisible reste.
    assert step.deletes == [old_module.path]


# --- Critères 2 et 3 : routes Spring, tâche, messages ---------------------------------------

CONTROLLER = b"""package fr.boutique.api;

@RestController
@RequestMapping("/commandes")
public class CommandesController {
    /** Lit une commande. */
    @GetMapping("/{id}")
    public ResponseEntity<Commande> lire(@PathVariable UUID id) { return null; }

    @PostMapping
    public Commande creer(@RequestBody Commande c) { return c; }

    @RequestMapping(value = "/purge", method = RequestMethod.DELETE)
    void purger() {}

    @GetMapping(Routes.TOUTES)
    public Flux<Commande> toutes() { return null; }

    @RequestMapping("/sans-verbe")
    public void sansVerbe() {}

    @Scheduled(cron = "0 * * * * *")
    public void nettoyer() {}

    @KafkaListener(topics = "commandes", groupId = "g")
    public void ecouter(String message) {}

    public static int calculer(List<Integer> lignes) { return 0; }

    private void cache() {}
}

@FeignClient("stock")
interface StockClient {
    @GetMapping("/stock/{id}")
    Stock lire(String id);
}
"""

KOTLIN = b"""package fr.boutique.api

@RestController
@RequestMapping(value = ["/paniers"])
class PaniersController(private val service: PanierService) {
    @GetMapping("/{id}")
    suspend fun lire(@PathVariable id: UUID, n: Int = 2): Mono<Panier>? = TODO()

    internal fun aide(): List<String> = listOf()

    private fun cache() {}

    @RabbitListener(queues = ["paniers"])
    fun ecouter(message: String) {}
}
"""


def _methods(files: dict[str, bytes]) -> dict[str, dict[str, dict[str, Any]]]:
    objects = KotlinJavaParser().parse(files)
    return {
        o.key: {m["name"]: m for m in load_schema(o.extend)["methods"]} for o in objects if o.extend
    }


def test_routes_spring_composees() -> None:
    methods = _methods({"api/src/main/java/CommandesController.java": CONTROLLER})
    ctrl = methods["fr-boutique-api-commandescontroller"]
    assert ctrl["lire"]["entryPoint"] == {"kind": "http", "verb": "GET", "route": "/commandes/{id}"}
    assert ctrl["creer"]["entryPoint"]["route"] == "/commandes"
    assert ctrl["purger"]["entryPoint"] == {
        "kind": "http",
        "verb": "DELETE",
        "route": "/commandes/purge",
    }
    assert ctrl["toutes"]["entryPoint"]["route"] is None  # constante : route inconnue
    assert "entryPoint" not in ctrl["sansVerbe"]  # @RequestMapping sans verbe
    # Retour déballé : ResponseEntity<Commande> → Commande ; Flux<T> → liste.
    assert ctrl["lire"]["returns"] == {
        "type": "ResponseEntity<Commande>",
        "schema": {"x-unresolved-type": "Commande"},
    }
    assert ctrl["toutes"]["returns"]["schema"]["type"] == "array"
    assert ctrl["lire"]["description"] == "Lit une commande."
    assert ctrl["lire"]["source"] == {
        "file": "api/src/main/java/CommandesController.java",
        "line": 8,
    }
    # Visibilités : paquet → internal, static, private jamais écrite.
    assert ctrl["purger"]["visibility"] == "internal"
    assert ctrl["calculer"]["static"] is True
    assert "cache" not in ctrl
    # Un client Feign porte @GetMapping mais n'est pas un point d'entrée.
    assert "entryPoint" not in methods["fr-boutique-api-stockclient"]["lire"]


def test_tache_et_consommateurs() -> None:
    ctrl = _methods({"CommandesController.java": CONTROLLER})["fr-boutique-api-commandescontroller"]
    assert ctrl["nettoyer"]["entryPoint"] == {"kind": "task"}
    assert ctrl["ecouter"]["entryPoint"] == {"kind": "message", "topic": "commandes"}


def test_kotlin() -> None:
    ctrl = _methods({"P.kt": KOTLIN})["fr-boutique-api-panierscontroller"]
    lire = ctrl["lire"]
    assert lire["entryPoint"] == {"kind": "http", "verb": "GET", "route": "/paniers/{id}"}
    assert lire["async"] is True  # suspend
    assert [(p["name"], p["type"], p.get("optional", False)) for p in lire["parameters"]] == [
        ("id", "UUID", False),
        ("n", "Int", True),
    ]
    assert lire["returns"]["schema"] == {"x-unresolved-type": "Panier"}
    assert ctrl["aide"]["visibility"] == "internal"
    assert ctrl["ecouter"]["entryPoint"] == {"kind": "message", "topic": "paniers"}
    assert "cache" not in ctrl


def test_regles_pures() -> None:
    assert literal('"/x"') == "/x"
    assert literal('{"/a", "/b"}') == "/a"
    assert literal("Routes.X") is None
    assert literal('"/a" + Routes.X') is None
    assert join_routes("/commandes/", "/{id}") == "/commandes/{id}"
    assert join_routes("", "") == "/"
    owner = [Annotation("RestController")]
    mapping = Annotation("RequestMapping", {"value": '"/x"', "method": "{RequestMethod.PUT}"})
    assert entry_point([mapping], owner) == {"kind": "http", "verb": "PUT", "route": "/x"}
    assert entry_point([Annotation("GetMapping", {}, ('"${chemin}"',))], owner) == {
        "kind": "http",
        "verb": "GET",
        "route": None,
    }


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
        parsers=["kotlin-java"],
    )


async def test_collecte_modules_et_fonctions(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    files = {path: content.decode() for path, content in GRADLE.items()}
    files["api/src/main/java/fr/boutique/api/CommandesController.java"] = CONTROLLER.decode()
    files["domain/src/main/java/fr/boutique/domain/Commande.java"] = (
        "package fr.boutique.domain;\npublic class Commande { public String ref; }\n"
    )
    url, work = _make_bare_with(tmp_path, "src", files)
    report = await _collect(collector, space, url)
    assert report.errors == []
    raw = _raw(tmp_path, space)
    api_id = schema_file(module_key("api/build.gradle.kts"))
    assert (raw / api_id).is_file()
    extend = load_schema((raw / "fr-boutique-api-commandescontroller.extend.yaml").read_text())
    lire = next(m for m in extend["methods"] if m["name"] == "lire")
    # Le type du retour, dans un autre module, est résolu par le socle.
    assert lire["returns"]["schema"] == {"$ref": "fr-boutique-domain-commande.schema.yaml"}
    # Chaque classe appartient au module de son projet.
    clear_caches()
    members = members_by_module(collect_source_entries(raw.parents[2], "api"))
    assert [o.title for o in members[api_id]] == ["CommandesController", "StockClient"]
    domain_id = schema_file(module_key("domain/build.gradle.kts"))
    assert [o.title for o in members[domain_id]] == ["Commande"]
    statuts = {f.path: f.statut for f in report.files}
    assert statuts["api/build.gradle.kts"] == "traité"
    # Sans changement : rien n'est réécrit.
    assert (await _collect(collector, space, url)).local_commit is None
    # Le sous-projet facturation retiré : son module part.
    (work / "services/facturation/build.gradle").unlink()
    _push(work)
    await _collect(collector, space, url)
    assert not (raw / schema_file(module_key("services/facturation/build.gradle"))).exists()
    assert (raw / api_id).is_file()
