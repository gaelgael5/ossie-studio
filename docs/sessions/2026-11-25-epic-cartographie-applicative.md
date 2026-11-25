# Session du 25 novembre 2026 — Epic « Cartographie applicative dans raw » (développement)

Epic docflow **5f729d5e** (workspace `ossie-studio`, bloc `backlog`) : la couche **Application** d'ArchiMate en matière brute dans raw — modules, dépendances, méthodes publiques, points d'entrée, lien au contrat OpenAPI, vue « Application » de l'Explorateur, primitives MCP.
Branche : **`feature/cartographie-applicative`**, partie de `dev` (`c6ded6f`, 25 novembre). `origin/dev` à intégrer par merge (jamais de rebase).
Session : https://claude.ai/code/session_01D6wr1MCdA8afEP3yrTDG2D

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui décide quand fusionner sur `dev`.

## Décisions de Gael (25 novembre)

- PR [gaelgael5/ossie-studio#5](https://github.com/gaelgael5/ossie-studio/pull/5) ouverte vers `dev` à la demande de Gael (8 tickets + trois défauts venus de `dev` corrigés) ; la suite (Kotlin / Java) se fait sur la branche **`feature/cartographie-kotlin-java`** pour ne pas modifier la PR en cours.
- PR #5 fusionnée par Gael (8 tickets sur `dev`, vérifié). Kotlin / Java, TypeScript et Python restent sur `feature/cartographie-kotlin-java` jusqu'à la prochaine PR.
- Plan du ticket TypeScript (f619f4a7) validé (« oui go », puis « continue les dev ») ; limite acceptée : le préfixe d'un `app.use('/api', router)` posé dans un autre fichier n'est pas recomposé.
- Plan du ticket Kotlin / Java (e34e5c29) validé (« oui go »), agrégateurs exclus des modules.
- Plan du ticket 7 (déplacer les ancres, a1fd0663) validé (« continue ») avec deux ajustements proposés : (1) les poignées s'affichent pour qui a `sources:collect`, quelle que soit la branche — le critère 6 « branche racine en lecture seule » est dépassé par la règle du bug e7deb2b9 (on écrit dans le brouillon de la personne, racine comprise) ; (2) « remettre l'ancre calculée » remet l'entrée à `auto` aux deux bouts au lieu de la supprimer (sinon la collecte la recréerait et le critère 3 tomberait).
- Plan du ticket 5 (projet SQL Server comme module) validé (« oui go ») ; une procédure mal formée est signalée **dans le compte rendu de collecte et dans le journal** (« 1. oui »).
- Plan du ticket 4 (lien au contrat OpenAPI) validé (« Oui go »).
- Plan du ticket 8 (primitives MCP) validé (« Oui go »).
- Plan du ticket 6 (vue « Application ») validé (« oui, vas-y »), y compris la rangée à part, en bas, pour les projets de test.
- Plan du ticket 3 (méthodes et points d'entrée C#) validé (« oui, vas-y »).
- Plan du ticket 2 (modules C#) : présenté ; Gael : « reprends la branche dev avant de démarrer » (démarrage validé).
- Plan de l'enabler validé (« 1. oui … 3. ok 4. ok 5. ok ») : **module = un `<clé>.schema.yaml` avec `x-kind: module`**, `<clé>.extend.yaml` pour les fonctions, `<clé>.view.yaml` mis à jour par ajouts et suppressions, conventions + JSON Schema + tests.
- **Appartenance d'un objet à un module** : calculée à la lecture (fichier de projet le plus proche en remontant le chemin du fichier d'origine, règle du compilateur .NET), **pas écrite** dans le module. Gael a demandé de quel calcul il s'agissait ; l'explication lui a été donnée, la liste écrite reste possible s'il la préfère.

## Tickets

| Ticket | Statut |
|---|---|
| 6c97ad98 — Format raw des modules, des fonctions et du fichier `.view` (enabler) | En review |
| e0fa8519 — Modules C# (`.csproj` → objets module) | En review |
| f1cc03c4 — Méthodes publiques et points d'entrée C# | En review |
| 90ed0967 — Relier les points d'entrée au contrat OpenAPI | En review |
| 30c55418 — Projet SQL Server comme module | En review |
| 0fa620be — Explorateur raw : objets module et vue « Application » | En review |
| a1fd0663 — Déplacer les ancres des connecteurs (`.view`) | En review |
| 72ce32b3 — Primitives MCP : modules, fonctions, points d'entrée | En review |
| e34e5c29 — Kotlin / Java | En review (branche `feature/cartographie-kotlin-java`) |
| f619f4a7 — TypeScript | En review (branche `feature/cartographie-kotlin-java`) |
| 3343afe4 — Python | Prêt pour dev |
| 3191b4a4 — Clôture (avec Gael, jamais seul) | — |

Ordre prévu par l'epic : 1, puis 2 et 3, puis 6 et 8 ; ensuite 4, 5, 7 ; les autres langages à la suite.

## Notes techniques (format, enabler 6c97ad98)

- Code : `backend/src/ossie_studio/parsers/application.py` — clés (`module_key` tirée du **chemin** du fichier de projet : `module-src-boutique-api-boutique-api`), `module_schema`, `validate_module`, `module_links`, `module_of` (appartenance), `dump_functions` / `normalize_functions` / `validate_functions` (ordre déterministe : nom, types des paramètres, ligne), `view_links` (valeurs par défaut), `sync_view` (ajouts et suppressions, jamais d'écrasement d'un fichier illisible).
- JSON Schema de validation : `backend/src/ossie_studio/parsers/formats/module.schema.json`, `functions.schema.json`, `view.schema.json`.
- Validateur commun (`parsers/conventions.validate_schema`) : refuse `x-methods`, `x-functions`, `x-entry-point(s)` dans un schéma (`functions_in_schema`) ; valide un module par son JSON Schema (`module_invalid`) ; un module n'a pas de visibilité.
- Collecte : étape du socle `sources/module_views.sync_module_views`, après la résolution des références (`sources/collect.py`) : `.view` créé pour un module nouveau, entrées des liens tenues à jour, `.view` d'un module disparu retiré, rien réécrit sans changement.
- L'Explorateur ne liste que les `.schema.yaml` : `.extend.yaml` et `.view.yaml` de raw n'y apparaissent pas (testé).
- Libellés des nouvelles causes d'erreur de collecte (`convention_functions_in_schema`, `_module_invalid`, `_functions_invalid`, `_view_invalid`) dans `frontend/src/locales`.
- Conventions : section « Cartographie applicative : modules, fonctions, vue » de `docs/conventions-schemas-generes.md`, un exemple par forme (les exemples sont validés par les tests).
- Tests : `backend/tests/test_application_formats.py` (unitaires, 7 critères) et `test_application_collect.py` (collecte de bout en bout avec le parseur de démonstration).
- Le Postgres de test s'arrête au redémarrage du conteneur : `sudo pg_ctlcluster 16 main start`.

## Notes techniques (modules C#, e0fa8519)

- **Une seule lecture des `.csproj`** : `sources/schema_resolve.read_project_map` décrit maintenant aussi chaque projet (`CsProject` : SDK, `OutputType`, cibles, projet de test, références normalisées depuis la racine, références hors de la source comprises ; `error` si illisible). La carte des homonymes (`refs`) est inchangée. La collecte la lit une fois et la passe à la résolution et à l'étape des modules.
- Étape du socle `sources/csharp_modules.csharp_module_actions`, juste après les parseurs : un objet module par `.csproj` (`module_schema`, langage `csharp`), écrit seulement si son contenu change ; le module d'un `.csproj` disparu est retiré ; un `.csproj` illisible (pas UTF-8, ou pas d'élément `<Project>`) est en erreur dans le compte rendu (`csproj_unreadable`) et son module précédent est laissé tel quel. Le `.csproj` apparaît « traité » / « inchangé » dans le compte rendu.
- Nature (`nature_of`) : test (`IsTestProject` ou paquet `Microsoft.NET.Test.Sdk`, xunit, NUnit, MSTest), puis web (`Microsoft.NET.Sdk.Web`, Blazor WebAssembly), worker (`Microsoft.NET.Sdk.Worker`), console (`OutputType` `Exe` / `WinExe`), sinon bibliothèque.
- Un module n'est jamais la cible d'une référence de type (`SchemaIndex` l'ignore) : un projet « Domain » ne répond pas à une classe `Domain`.
- Tests : `backend/tests/test_csharp_modules.py` (six projets : natures, dépendances internes et externe, appartenance d'une classe, compte rendu, incrémental, projet modifié puis supprimé, `.csproj` illisible, source sans `.csproj`).

## Notes techniques (méthodes et points d'entrée C#, f1cc03c4)

- Parseur `parsers/csharp.py` (version **1.4** : toutes les sources C# sont relues une fois) : méthodes `public` / `internal` / `protected` (membre d'interface sans mot-clé : public ; `private` non écrite ; méthode illisible laissée de côté et signalée au journal `csharp_method_unreadable`) ; paramètres (type tel qu'écrit + schéma, facultatif si valeur par défaut), retour (`Task<T>`, `ValueTask<T>`, `ActionResult<T>` déballés ; `IAsyncEnumerable<T>` → liste), `static`, `async`, doc, attributs, fichier et ligne du nom.
- Points d'entrée (`parsers/csharp_entrypoints.py`, fonctions pures) : contrôleur = base `ControllerBase` / `Controller` ou `[ApiController]` ; actions `[HttpGet]`… ; route = `[Route]` de la classe + gabarit (un gabarit en `/` ou `~/` remplace celui de la classe ; `[controller]`, `[action]` en minuscules) ; gabarit non littéral → `route: null`. `BackgroundService` / `IHostedService` → `entryPoint: {kind: task}` sur la classe.
- Minimal API (`minimal_api`) : `MapGet` / `MapPost` / `MapPut` / `MapPatch` / `MapDelete`, préfixes `MapGroup` directs ou par variable locale ; nom = gestionnaire (groupe de méthodes) sinon « VERBE route ». Index du plugin `functions-csharp.yaml` (par fichier, empreinte), rangé par module par le socle (`sources/module_functions.py`).
- Moteur incrémental (`sources/collect_core.py`) : `ParsedObject.extend` écrit `<clé>.extend.yaml` à côté du schéma, suivi dans le manifeste ; une sortie qu'une source relue ne produit plus (classe retirée du fichier, classe sans méthode) est supprimée ; les compteurs ne comptent que les schémas.
- Résolution (`schema_resolve.resolve_source_schemas`) : les types des `.extend.yaml` se résolvent comme les champs, dans le contexte de l'objet qui les porte.
- Tests : `backend/tests/test_csharp_functions.py` (critères 1 à 7, collecte de bout en bout : retour résolu, fonctions du module, incrémental, suppressions).

## Notes techniques (vue « Application » de l'Explorateur, 0fa620be)

- Index de l'Explorateur (format **13**, reconstruit une fois) : `ExplorerObject.module` (`ModuleFacts` : projet, nature, langage, cibles, dépendances) et `ExplorerObject.functions` (`FunctionCounts` : méthodes publiques, points d'entrée, routes HTTP, fonctions libres, lus dans le `.extend.yaml`, avec cache par fichier). Un changement de `.extend.yaml` change la signature de raw, donc reconstruit l'index.
- `sources/explorer_application.py` : `application_view` (modules, dépendances dans les deux sens, compteurs : classes rattachées par la règle du projet le plus proche — objets d'un projet SQL —, points d'entrée, routes HTTP, méthodes publiques ; contrats de la source) et `module_detail` (aperçu : points d'entrée avec propriétaire et état au contrat, `null` = non comparé tant que le ticket 4 n'est pas livré ; méthodes publiques par classe).
- Routes : `GET …/raw-explorer/{source}/application` (sur l'index, sans relire la branche — c'est la base des primitives du ticket 8) et `GET …/raw-explorer/{source}/module?file=` (lit les `.extend.yaml` du module et de ses objets) ; `…/tree` rend aussi `modules` (fichier, titre, dossier) pour le menu de gauche. Droits : `sources:read`.
- Écran : `pages/RawApplication.tsx` (disposition calculée `applicationLayout` : colonnes par plus long chemin de dépendances, ordre alphabétique, projets de test et contrats sur une rangée à part en bas ; SVG maison, zoom +/−/Ajuster ; clic sur un bloc → aperçu ; « n classes » → dossier du module dans le menu de gauche), `pages/RawModulePreview.tsx` (aperçu), `RawExplorer.tsx` (4ᵉ mode « Application », grisé sans module ; objets `MOD` dans l'arbre, dont le clic ouvre la vue centrée sur le module ; paramètres d'adresse `view=application&module=`).
- Pas encore vérifié dans un vrai navigateur.

## Notes techniques (primitives MCP, 72ce32b3)

- `mcp_surface/application.py` : `list_modules`, `get_module` (module par chemin ou titre), `get_functions` (méthodes d'un objet, avec visibilité — les méthodes `private` ne sont pas collectées), `list_entry_points` (filtre `contract_status` = `linked` | `absent` ; un point d'entrée non encore comparé vaut `not_compared` et ne répond à aucun filtre). Domaine `sources`, lecture, capacité `sources:read`, `idempotent`.
- Même vérité que l'écran : `application_view` / `module_detail` sur l'index de l'Explorateur (en base pour une personne, en mémoire pour une clé d'API, via `space_entries`) ; seuls les `.extend.yaml` utiles sont lus dans la vue (`BranchView.read`, une lecture groupée).
- Erreurs nommées : `module_not_found`, `schema_not_found`, `source_not_found` ; source sans module → liste vide. Listes bornées à 200, `truncated: true` au-delà.
- Libellés des primitives (écran des profils agents) dans `frontend/src/locales`.
- Tests : `backend/tests/test_mcp_application.py` (six modules, dépendances dans les deux sens, module, fonctions, routes absentes du contrat, erreurs, troncature, vue brouillon par OAuth et vue publiée par clé d'API).

## Notes techniques (lien au contrat OpenAPI, 90ed0967)

- Étape du socle `sources/contract_links.sync_contract_links`, juste après la résolution des types (`sources/collect.py`) : elle voit le `raw/` final (copies `contracts/…/contrat.full.openapi.yaml` listées par `contracts-index.yaml`, et tous les `.extend.yaml`) ; elle écrit `contract` dans chaque point d'entrée HTTP, et ne réécrit un `.extend.yaml` que si cet état change (l'écriture du parseur est complétée sur place). Le contrat n'est jamais touché.
- Règle de comparaison (`route_key`, `server_prefixes`, `operation_index`) : même verbe ; paramètres par position (`{id}`, `{commandeId}`, `{id:guid}`, `{id?}` se valent) ; casse et barre finale ignorées ; chemin des `servers` (variable du chemin remplacée par son défaut, variable de l'hôte sans effet) pris avec et sans préfixe. Une opération de deux contrats est listée deux fois. `file` = chemin de la copie sous `contracts/` (le même que la vue « Application »).
- Non comparé (pas de `contract`) : route `null`, point non HTTP, source sans contrat (l'état d'une collecte précédente est retiré).
- Le contrat seul change : le code n'est pas relu, l'état des `.extend.yaml` suit (testé).
- Index de l'Explorateur (format **14**) : `FunctionCounts.linked` (points reliés) et `operations` (opérations reliées : fichier, verbe, chemin). Vue « Application » : `counts.linked` par module, `contracts[].linked` (opérations distinctes reliées).
- Écran : bloc du contrat « x / y opérations reliées au code » ; aperçu d'un module « n / m route(s) au contrat » (routes comparées).
- Primitives : le filtre `contract_status` de `list_entry_points` répond maintenant sur les données collectées.
- Conventions : sous-section « Point d'entrée et contrat OpenAPI » (exemple vérifié par le test : c'est ce que produit le socle).
- Tests : `backend/tests/test_contract_links.py` (règles, écriture seulement au changement, collecte de bout en bout C# + contrat : relié, absent, route inconnue, minimal API, contrat modifié seul, contrat retiré), `test_explorer_application.py` (compteurs), `RawApplication.test.tsx`.

## Notes techniques (projet SQL Server comme module, 30c55418)

- Parseur `parsers/sqlserver_project.py` (version **1.2** : les sources déjà collectées reçoivent leur module à la collecte suivante) : `sqlproj_module` — un objet module par `.sqlproj` lisible (nature `sql`, langage `tsql`, cible tirée de `<DSP>`, dépendances = `ProjectReference` normalisées depuis la racine, reliées au module si le projet est dans la source). Le module suit le projet par l'index du parseur (sorties).
- Procédures et fonctions : `parsers/tsql_routines.py`, petit lecteur d'**en-tête** (jetons à la demande, commentaires, `[ident]`, chaînes `N'…'`) — `CREATE [OR ALTER] PROC|PROCEDURE|FUNCTION`, nom qualifié, paramètres (`@p [AS] type [VARYING] [NULL] [= défaut] [OUT|OUTPUT|READONLY]`), `RETURNS type | TABLE | @t TABLE (…)`. Le corps n'est jamais lu : sqlglot échouait sur `@x AS INT`, `RETURNS @t TABLE` et produisait de faux `sql_unrecognized` sur les fonctions (vérifié contre l'ancienne version : ils disparaissent).
- `routine_function` : format des fonctions de raw (`annotations` PROCEDURE/FUNCTION, `visibility: public`, paramètres avec `@`, type tel qu'écrit + `schema` via `type_fragment`, `returns` pour une fonction). Index `functions-sqlserver-projet.yaml` (langage `tsql`), rangé par le socle (`sources/module_functions`) ; gardé vide quand les procédures disparaissent (le socle retire alors le `.extend.yaml` du module).
- En-tête illisible : `sql_routine_unreadable` dans le compte rendu (avec la ligne) + journal `sqlserver_routine_unreadable` ; les autres sont écrites.
- Schémas des tables et des vues : identiques octet pour octet à la version précédente du parseur (comparaison faite sur le projet de test : 11 schémas identiques, seul le module s'ajoute).
- Aperçu d'un module (`module_detail`) : `functions` (fonctions propres du module, avec signature) ; écran « Procédures et fonctions · n » pour un projet SQL (« Fonctions du module · n » sinon), section « Méthodes publiques » masquée quand un projet SQL n'en a pas.
- Tests : `backend/tests/test_sqlserver_modules.py` (critères 1 à 3, `ProjectReference`, exemple des conventions, collecte de bout en bout : fonctions rangées, onze objets rattachés, incrémental, procédures retirées), `test_sqlserver_project.py` (version, sorties), `test_explorer_application.py`, `RawApplication.test.tsx`.

## Notes techniques (ancres des liens, a1fd0663)

- Index de l'Explorateur (format **15**) : `ModuleFacts.anchors` — ancres choisies des liens du module, lues dans son `.view.yaml` (`explorer.view_anchors`, `parsers.application.custom_links` : seuls les liens dont une ancre n'est pas `auto`). Une écriture du `.view` change la signature de raw, donc reconstruit l'index. La vue « Application » rend `anchors` par module (`{module d'arrivée: {from, to}}`), toujours sans relire la branche.
- Écriture : `PUT …/raw-explorer/{source}/module/link` (`module`, `target`, `from`, `to` ; ancre `auto` ou `{side, at}`, `at` entre 0 et 1), capacité `sources:collect`, dans le brouillon de la personne (`commit_to_branch`) ; `parsers.application.set_view_link` ne change que l'entrée du lien (fichier absent : créé avec cette entrée ; illisible : refus `view_unreadable`, 409, jamais écrasé). Lien que le module n'a pas : `module_link_not_found` (404).
- Écran (`pages/RawApplication.tsx`) : géométrie exportée (`ANCHOR_SLOTS` — milieu de chaque côté et deux points de plus en haut et en bas —, `anchorPoint`, `autoAnchors`, `linkAnchors`, `linkPath` : même tracé qu'avant pour les ancres calculées). Clic sur un lien → mis en avant, barre « Lien A → B » ; avec le droit : poignées aux extrémités (glisser, capture à 18 px d'une ancre du même bloc, sinon retour), ancres en petits cercles (cliquables, clavier compris), bouton « Ancre calculée ». Rendu immédiat (choix gardé jusqu'au rechargement de la vue), annulé si l'écriture échoue. Échap ou clic dans le vide : désélection. Blocs fixes.
- Collecte : inchangée — `sync_module_views` garde les ancres, retire l'entrée d'un lien disparu (testé avec des ancres choisies).
- Tests : `backend/tests/test_module_anchors.py` (critères 1 à 6, erreurs, une seule entrée change) ; `RawApplication.test.tsx` (géométrie, ancre appliquée à l'ouverture, clic sur une ancre, retour à l'ancre calculée, glisser-lâcher avec géométrie SVG simulée, sans droit). Pas encore vérifié dans un vrai navigateur.

## Notes techniques (Kotlin / Java, e34e5c29)

- Branche `feature/cartographie-kotlin-java`, partie de `feature/cartographie-applicative` (PR #5 ouverte) : à remettre à jour depuis `dev` une fois la PR fusionnée.
- Modules : `sources/jvm_modules.py` (`read_jvm_projects`, `jvm_module_actions`), étape du socle juste après les modules C# (`collect.py`). Gradle lu en texte (commentaires retirés : `include(...)`, `project(":x").projectDir`, `project(":x")`, `projects.x`, plugins Spring Boot / Kotlin hors `apply false`, toolchain / compatibilité Java) ; Maven en XML (defusedxml, espaces de noms ignorés, `groupId` hérité, `${project.groupId}`, propriétés de version Java héritées du parent présent dans la source). Agrégateurs exclus (racine Gradle sans `src/`, `packaging pom`). Retrait du module d'un fichier de build disparu limité aux modules `java` / `kotlin` dont le projet est un `build.gradle(.kts)` ou un `pom.xml`.
- Méthodes : `parsers/kotlin_java.py` (version **1.2**) — `_java_methods`, `_kotlin_methods`, `_java_result_ref` / `_kotlin_result_ref` (enveloppes déballées), `_function_entry` (même forme que C#) ; `ParsedObject.extend`.
- Points d'entrée : `parsers/jvm_entrypoints.py` (fonctions pures : `Annotation` aux arguments tels qu'écrits, `literal`, `join_routes`, `entry_point`). `@RequestMapping` de méthode sans `method` non retenu (le format exige un verbe).
- Le lien au contrat OpenAPI (ticket 4) s'applique sans changement aux routes Spring.
- Tests : `backend/tests/test_jvm_cartography.py` (critères 1 à 4, racine avec code, build seul, fichier illisible et module disparu, Kotlin, Feign exclu, règles pures, collecte de bout en bout : modules, retour résolu dans un autre module, appartenance des classes, incrémental, sous-projet retiré).

## Notes techniques (TypeScript, f619f4a7)

- Écriture commune des modules tirés des fichiers de projet : `sources/project_modules.py` (`BuildProject`, `project_module_actions(…, owns)`) ; Gradle / Maven et npm l'utilisent (Python suivra).
- Modules npm : `sources/npm_modules.py` (`read_npm_projects`, `npm_module_actions`), étape du socle après Gradle / Maven (`collect.py`).
- Parseur `parsers/typescript.py` (version **1.2**) : `_methods` (décorateurs qui précèdent la méthode dans le corps de la classe ; JSDoc avant les décorateurs, `_member_doc`), `_class_decorators` (ceux de la classe et de son `export`), `_exported_functions`, `_routes` (`_created_routers` : variables créées par une fabrique de routeur), `file_functions`, `_functions_index` (comme la minimal API C#, empreinte par fichier).
- Points d'entrée : `parsers/ts_entrypoints.py` (fonctions pures : `literal`, `normalize`, `join_routes`, `nest_entry_point`, `is_router`, `router_entry_point`).
- **Règle (décidée en cours de ticket)** : un module de langage n'est produit que si le parseur de ce langage est choisi pour la source (`collect.py`, `selected`) ; retiré du choix, ses modules partent (l'étape tourne sans projet). Appliquée aux trois étapes C#, Kotlin / Java et npm (le `.sqlproj` l'était déjà par construction). Révélé par `test_openapi_repo` : un `package.json` d'une source C# + OpenAPI devenait un module.
- Tests : `backend/tests/test_ts_cartography.py` (critères 1 à 4, paquet illisible, racine avec code, classe non exportée, règles pures, collecte de bout en bout : modules, fonctions du module, retour résolu dans un autre paquet, appartenance, incrémental, paquet retiré).
