# Session du 25 novembre 2026 — Epic « Cartographie applicative dans raw » (développement)

Epic docflow **5f729d5e** (workspace `ossie-studio`, bloc `backlog`) : la couche **Application** d'ArchiMate en matière brute dans raw — modules, dépendances, méthodes publiques, points d'entrée, lien au contrat OpenAPI, vue « Application » de l'Explorateur, primitives MCP.
Branche : **`feature/cartographie-applicative`**, partie de `dev` (`c6ded6f`, 25 novembre). `origin/dev` à intégrer par merge (jamais de rebase).
Session : https://claude.ai/code/session_01D6wr1MCdA8afEP3yrTDG2D

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui décide quand fusionner sur `dev`.

## Décisions de Gael (25 novembre)

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
| 90ed0967 — Relier les points d'entrée au contrat OpenAPI | Prêt pour dev |
| 30c55418 — Projet SQL Server comme module | Prêt pour dev |
| 0fa620be — Explorateur raw : objets module et vue « Application » | Prêt pour dev |
| a1fd0663 — Déplacer les ancres des connecteurs (`.view`) | Prêt pour dev |
| 72ce32b3 — Primitives MCP : modules, fonctions, points d'entrée | Prêt pour dev |
| e34e5c29 / f619f4a7 / 3343afe4 — Kotlin-Java / TypeScript / Python | Prêt pour dev |
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
