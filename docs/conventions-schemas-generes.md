# Conventions des schémas générés

Ce document fixe **ce que produit** tout parseur de code et tout extracteur SQL,
pour que les schémas d'un fichier C# et d'une table Snowflake se lisent, se
comparent et se relient de la même façon. Un **validateur partagé**
(`ossie_studio.parsers.conventions.validate_schema`) l'applique à chaque schéma
produit : un schéma non conforme fait échouer la lecture du fichier concerné (il
est signalé dans le compte rendu, jamais écrit).

## Forme générale

- **JSON Schema 2020-12**, **un fichier par objet** (classe, record, struct,
  enum, interface, table, vue), **écrit en YAML** : `<clé>.schema.yaml` dans
  `raw/` (et l'asset de même nom dans `assets/`). Le suffixe double évite la
  collision avec les index YAML de `raw/` (`manifest.yaml`, `docs-index.yaml`).
  Écriture déterministe : clés triées, blocs, textes multilignes en `|`.
- **Identité stable** : `$id` dérivé de la source et du nom qualifié de l'objet
  (espace de noms / module + nom ; schéma + table). Le nom de fichier en dérive.
  Jamais de dépendance à l'ordre de lecture.
- `title` = nom de l'objet ; `description` = commentaire de documentation (doc
  comment, docstring, commentaire de colonne) quand il existe, au niveau de
  l'objet et de chaque champ.
- **Toute clef non standard commence par `x-`** (métadonnées d'origine) ; toute
  clef de noyau commence par `$`. Une clef non standard sans `x-` est refusée.
- **Sortie déterministe** : clefs triées, propriétés dans un ordre stable, aucune
  donnée volatile, fin de ligne (`\n`) et encodage (UTF-8) fixés. Deux lectures
  du même original produisent les mêmes octets (`dump_schema`).

## Champs et types

- **Propriétés** : une par champ ou propriété lu, avec un type JSON Schema.
  Chaque parseur publie sa **table de correspondance** des types de son langage
  vers JSON Schema (`type`, `format`).
- **Obligatoire / facultatif** : non nullable → dans `required` ; nullable → hors
  de `required`.
- **Collections** → `array` + `items`. **Dictionnaires** → `object` +
  `additionalProperties`.
- **Énumérations** → `enum` avec les valeurs.

Exemple (énumération) :

```yaml
$id: app.statut.schema.yaml
enum:
- ouvert
- fermé
title: Statut
x-kind: enum
x-language: csharp
x-visibility: public
```

## Références et héritage

- **Référence à un autre objet** → `$ref` vers le `$id` de l'autre schéma.
- **Clef étrangère SQL** → `x-references` sur la colonne (la relation vit sur le
  champ et disparaît avec lui).
- **Héritage** → `allOf` + `$ref` vers le parent, **sans aplatir** les propriétés
  héritées : un schéma ne dépend ainsi que de son propre fichier, ce qui garde la
  collecte incrémentale exacte.
- **Type non résolu** (bibliothèque externe, type absent de la source) →
  `x-unresolved-type` avec le nom tel qu'écrit, sans `$ref` cassé.

Exemple (héritage) :

```yaml
$id: app.commande-web.schema.yaml
allOf:
- $ref: app.commande.schema.yaml
properties:
  origine:
    type: string
    x-visibility: public
title: CommandeWeb
x-language: csharp
x-visibility: public
```

## Visibilité (code uniquement)

`x-visibility` est **obligatoire** sur tout objet issu d'un parseur de **code**
(repéré par la présence de `x-language`) et sur chacun de ses champs, avec une
valeur normalisée parmi `public`, `internal`, `protected`, `private`.
`x-visibility-source` garde le mot-clef d'origine tel qu'écrit
(`protected internal`, `package-private`…). La visibilité est **absente** pour les
tables SQL, les schémas de contrats et les objets module (`x-kind: module`).

Les parseurs **lisent tous les types**, quelle que soit leur visibilité : on veut
voir un objet `internal` ou `private`, pas le perdre.

| Langage | Mot-clef d'origine | `x-visibility` |
|---|---|---|
| C# | `public` | `public` |
| C# | `internal`, `file`, absence de mot-clef sur un type de premier niveau | `internal` |
| C# | `protected`, `protected internal`, `private protected` | `protected` |
| C# | `private`, absence de mot-clef sur un membre | `private` |
| Java | `public` / `protected` / `private` | même valeur |
| Java | absence de mot-clef (paquet) | `internal` (`x-visibility-source: package-private`) |
| Kotlin | `public` ou absence / `internal` / `protected` / `private` | même valeur (`public` par défaut) |
| TypeScript | type exporté du module | `public` |
| TypeScript | type non exporté | `internal` |
| TypeScript | membre `public` / `protected` / `private` ou `#nom` | même valeur (`private` pour `#nom`) |
| Python | nom sans tiret bas | `public` |
| Python | nom commençant par `_` ou `__` | `private` (`x-visibility-source: convention`) |

## Métadonnées d'origine

Sous des clefs `x-` : `x-source` (fichier, ligne ou objet de catalogue),
`x-kind` (class, record, enum, table, view, materialized-view, json-content…),
`x-namespace` (espace de noms d'origine : namespace C#, paquet Java/Kotlin,
module TypeScript ou Python ; absent pour une table ou un contrat),
`x-language` ou `x-engine`, et
`x-annotations` (attributs, décorateurs, annotations : `[Table]`, `@Entity`,
`@Column`, `Field(...)`…). Ces annotations serviront à relier une classe à sa
table.

## Enrichissement humain (assets) : `<clé>.extend.yaml`

Le travail humain d'un asset curé **n'est pas écrit dans son schéma** : il vit
dans le fichier voisin `sources/<slug>/assets/<clé>.extend.yaml` (ticket
7a284e55). Raison (Gael, 23/11/2026) : « garder les standards non altérés » — le
`<clé>.schema.yaml` reste un JSON Schema relisible par n'importe quel outil, et le
rafraîchissement depuis le brut ne touche jamais au `.extend.yaml`.

```yaml
object:
  businessName: Commande client   # nom métier
  description: |                  # description métier (≠ `description` du code)
    …
  concept: Vente                  # concept métier rattaché (texte libre)
  tags: [rgpd]                    # étiquettes (liste)
fields:
  montant:
    businessName: Montant TTC
```

- liste **fermée** de clés ; une entrée vide n'est pas écrite, un fichier sans
  entrée est supprimé ; écriture déterministe (clés triées) ;
- la classification et la certification s'y ajouteront sous `object` /
  `fields.<nom>` ;
- un asset écrit avant ce ticket peut encore porter `x-businessName`,
  `x-description`, `x-concept`, `x-tags` dans son schéma : ces clés sont lues, puis
  déplacées dans `.extend.yaml` à la prochaine écriture de l'asset.

Dans le schéma de l'asset, toute clé (standard, ou `x-` venue du parseur comme
`x-source`, `x-annotations`) est en lecture seule côté curation. `x-origin` donne
le chemin du schéma brut d'origine.

## Cartographie applicative : modules, fonctions, vue

Epic « Cartographie applicative dans raw » (enabler 6c97ad98, décisions de Gael
du 25/11/2026) : la couche **Application** d'ArchiMate, en matière brute. Trois
formes, chacune avec son **JSON Schema de validation**
(`backend/src/ossie_studio/parsers/formats/`) et l'écrivain déterministe des
schémas. Code : `ossie_studio.parsers.application`.

### Objet module : `module-<chemin>.schema.yaml`

Un **module** = un fichier de projet (`.csproj`, `.sqlproj`, module Gradle ou
Maven, `package.json`, `pyproject.toml`). C'est un objet raw **comme les autres**
(il apparaît dans l'Explorateur, la recherche, la lecture MCP), repéré par
`x-kind: module`, rangé dans le dossier de son fichier de projet (`x-source`).
Sa clé dérive du **chemin** du fichier de projet (unique dans la source, même pour
deux projets homonymes). Pas de visibilité.

```yaml
$id: module-src-boutique-api-boutique-api.schema.yaml
title: Boutique.Api
x-kind: module
x-language: csharp
x-module:
  dependencies:
  - module: module-src-boutique-domain-boutique-domain.schema.yaml
    project: src/Boutique.Domain/Boutique.Domain.csproj
  - project: ../Partage/Outils.csproj
  nature: web
  project: src/Boutique.Api/Boutique.Api.csproj
  targets:
  - net8.0
x-source:
- file: src/Boutique.Api/Boutique.Api.csproj
```

- `nature` : `library`, `web`, `worker`, `console`, `test`, `sql`, `application`
  (application d'un autre langage : Spring Boot, Node, FastAPI…) ;
- une dépendance porte le chemin du projet référencé, et `module` (le `$id` de son
  module) quand ce projet est dans la source ; sans `module`, elle est **externe,
  non résolue** ;
- **appartenance** : un objet raw appartient au module dont le fichier de projet
  est **le plus proche en remontant** l'arborescence de son fichier d'origine (la
  règle du compilateur .NET). Elle n'est **pas écrite** (le module serait réécrit
  à chaque classe ajoutée) : l'Explorateur et les primitives l'appliquent à la
  lecture (`module_of`).

### Fonctions : `<clé>.extend.yaml` (dans `raw/`)

Écrit par le **parseur**, à côté du schéma de la classe (ses méthodes) ou du
module (ses **fonctions libres** : Python, TypeScript, minimal API C#),
**régénéré à chaque collecte**. Le `<clé>.schema.yaml` d'une classe reste du JSON
Schema standard : il ne contient **aucune méthode** (le validateur refuse
`x-methods`, `x-functions`, `x-entry-point`, `x-entry-points`). Ce n'est pas un
objet de l'Explorateur. À ne pas confondre avec le `<clé>.extend.yaml` d'un
**asset** (travail humain, dans `assets/`) : la curation ne recopie jamais celui
de `raw/`.

```yaml
methods:
- async: true
  entryPoint:
    kind: http
    route: /api/commandes/{id}
    verb: GET
  name: Lire
  parameters:
  - name: id
    schema:
      format: uuid
      type: string
    type: Guid
  returns:
    schema:
      $ref: boutique-api-commande.schema.yaml
    type: Commande
  source:
    file: src/Boutique.Api/Controllers/CommandesController.cs
    line: 18
  static: false
  visibility: public
- name: Calculer
  parameters:
  - name: lignes
    schema:
      items:
        x-unresolved-type: LigneExterne
      type: array
    type: List<LigneExterne>
  visibility: internal
```

- **méthodes** : nom, paramètres (`type` tel qu'écrit, `schema` son équivalent JSON
  Schema comme pour un champ : `$ref` vers l'objet raw quand il est résolu,
  `x-unresolved-type` sinon), retour, `visibility` (mêmes valeurs que les champs),
  `static`, `async`, emplacement (`source`) ; les méthodes `private` ne sont pas
  écrites ;
- **point d'entrée** (`entryPoint`, sur une méthode, une fonction, ou sur la classe
  elle-même pour un service d'arrière-plan) : `kind` (`http`, `task`, `message`),
  `verb` et `route` pour HTTP (`route: null` = route impossible à calculer),
  `contract` une fois rapproché des contrats OpenAPI de la source (`status` :
  `linked` avec les `operations`, ou `absent`) ;
- ordre déterministe (nom, types des paramètres, ligne) : deux collectes
  identiques donnent les mêmes octets.
- **fonctions libres** : un parseur ne connaît pas les modules ; il les note par
  fichier d'origine dans son index `functions-<parseur>.yaml` (par exemple
  `functions-csharp.yaml` pour la minimal API), et le socle les range dans le
  `.extend.yaml` du module qui contient le fichier (même langage).

### Point d'entrée et contrat OpenAPI

Rempli par le **socle** à la collecte (ticket 90ed0967), après les parseurs : chaque
point d'entrée HTTP dont la route est connue est comparé aux opérations des
contrats OpenAPI **de la même source** (copies `raw/contracts/…`). Le contrat
n'est jamais modifié. `file` est le chemin de la copie sous `contracts/`.

```yaml
methods:
- entryPoint:
    contract:
      status: absent
    kind: http
    route: /api/commandes/{id}/statut
    verb: PUT
  name: Changer
  visibility: public
- entryPoint:
    contract:
      operations:
      - file: api/contrat.full.openapi.yaml
        operationId: lireCommande
        path: /commandes/{commandeId}
        verb: GET
      status: linked
    kind: http
    route: /api/commandes/{id}
    verb: GET
  name: Lire
  visibility: public
```

- **même verbe, même route** : les paramètres se comparent par position (`{id}`,
  `{commandeId}`, `{id:guid}`, `{id?}` se valent), sans tenir compte de la casse ni
  de la barre finale ;
- le **chemin des `servers`** du contrat compte : avec `servers: [{url:
  https://boutique.example/api}]`, l'opération `/commandes/{commandeId}` répond à la
  route `/api/commandes/{id}` comme à `/commandes/{id}` ;
- une opération trouvée dans deux contrats est listée deux fois (une par contrat) ;
- **non comparé** (pas de `contract`) : route inconnue (`route: null`), point
  d'entrée non HTTP, source sans contrat — un état d'une collecte précédente est
  alors retiré ;
- le `.extend.yaml` n'est réécrit que si l'état au contrat change.

### Quels modules pour une source

Un module de langage n'est produit que si le **parseur de ce langage est choisi
pour la source** : `csharp` (`.csproj`), `kotlin-java` (Gradle, Maven),
`typescript` (`package.json`), `python` (`pyproject.toml`, `setup.cfg`,
`setup.py`), `sqlserver-projet` (`.sqlproj`). Une source
« contrats OpenAPI » n'a donc pas de module npm même si son dépôt contient un
`package.json`. Retirer un parseur du choix retire ses modules à la collecte
suivante.

### Kotlin / Java : modules Gradle et Maven, méthodes, points d'entrée Spring

Ticket e34e5c29, sur le modèle C# (les fichiers de build sont lus, jamais
exécutés) :

- **modules** (étape du socle `sources/jvm_modules.py`) : un par sous-projet
  Gradle déclaré dans `settings.gradle(.kts)` qui a son `build.gradle(.kts)`, un
  par build Gradle sans `settings`, un par `pom.xml` qui n'est pas un agrégateur
  (`packaging pom`) ; la racine d'un build multi-projets n'est un module que si
  elle a son propre `src/`. Dépendances : `project(":x")`, `projects.x`, et côté
  Maven une dépendance vers le `groupId:artifactId` d'un autre pom de la source.
  Nature : Spring Boot → `web` (avec `spring-boot-starter-web` / `webflux`) ou
  `application`, sinon `library`. Cible : `java17`… Langage : `kotlin` (plugin
  Kotlin ou fichiers `.kt`) ou `java`. Fichier de build illisible :
  `build_file_unreadable` dans le compte rendu, module précédent gardé ;
- **méthodes** (parseur `kotlin-java` 1.2) : `public`, `protected`, visibilité de
  paquet (`internal`) en Java, `internal` en Kotlin ; jamais `private`. Retour
  déballé (`ResponseEntity<T>`, `Mono<T>`, `Optional<T>`… → `T` ; `Flux<T>` →
  liste) ; `suspend` → `async` ; paramètre Kotlin avec valeur par défaut →
  `optional` ;
- **points d'entrée** : HTTP dans une classe `@RestController` / `@Controller`
  seulement (un client Feign n'en est pas un) — `@GetMapping`… et
  `@RequestMapping(method = …)`, route = chemin de la classe + chemin de la
  méthode, `route: null` pour une constante ou `${…}` ; `@Scheduled` → `task` ;
  `@KafkaListener` / `@RabbitListener` → `message` avec le sujet.

### TypeScript : modules npm, méthodes, fonctions, points d'entrée NestJS et Express

Ticket f619f4a7, sur le même modèle :

- **modules** (étape du socle `sources/npm_modules.py`, écriture commune
  `sources/project_modules.py`) : un par `package.json` hors `node_modules/` ; la
  racine d'un monorepo (`workspaces`, `pnpm-workspace.yaml`, `lerna.json`) est un
  agrégateur, pas un module, sauf si elle a son propre `src/`. Dépendances : les
  noms des `dependencies` / `devDependencies` / `peerDependencies` /
  `optionalDependencies` qui sont ceux d'un autre paquet de la source (les
  paquets npm externes sont ignorés). Nature : framework serveur ou front dans
  `dependencies` → `web`, script `start` ou `bin` → `application`, sinon
  `library`. Langage `typescript` ou `javascript` ; cible `node20`… ;
- **méthodes** (parseur `typescript` 1.2) des classes **exportées** : jamais
  `private`, `#privées`, accesseurs ni constructeur ; `Promise<T>` → `T` et
  `async` ; paramètre `?` ou avec valeur par défaut → `optional` ;
- **fonctions exportées** (`export function`, `export const f = () => …`) et
  **routes Express** : notées dans l'index `functions-typescript.yaml`, rangées
  par le socle dans le `.extend.yaml` du module ; une route est nommée d'après son
  gestionnaire, sinon « VERBE route » ;
- **points d'entrée** : NestJS (`@Controller('commandes')` + `@Get(':id')` →
  `GET /commandes/{id}` ; `@Cron` / `@Interval` / `@Timeout` → `task` ;
  `@MessagePattern` / `@EventPattern` → `message`) ; Express et routeurs
  semblables (`router.post('/paniers', creer)`) sur un objet nommé comme un
  routeur ou créé par `express()`, `Router()`, `fastify()`, `new Hono()`… Les
  `:id` deviennent `{id}` ; une route non littérale garde son point d'entrée avec
  `route: null`. Le préfixe d'un `app.use('/api', router)` posé dans un autre
  fichier n'est pas recomposé (limite connue).

### Python : modules pyproject, méthodes, fonctions, points d'entrée FastAPI, Flask, Celery

Ticket 3343afe4, sur le même modèle :

- **modules** (étape du socle `sources/python_modules.py`) : un projet par
  dossier — `pyproject.toml`, sinon `setup.cfg`, sinon `setup.py` (lu comme du
  texte, jamais exécuté) ; la racine d'un espace de travail uv sans projet à elle
  est un agrégateur. Dépendances par nom (normalisé PEP 503 : `My_Pkg` = `my-pkg`)
  ou par chemin (sources uv et Poetry `{ path = "../x" }`) ; paquets externes
  ignorés. Nature : FastAPI, Flask, Django… → `web`, Celery… → `worker`, script
  déclaré → `application`, sinon `library`. Cible `python3.12`… ;
- **objets** : en plus des classes de données, **toute classe publique qui a des
  méthodes publiques** a son objet (`x-kind: class`), pour porter ses méthodes ;
- **méthodes** (parseur `python` 1.2) : nom sans `_` initial (ni `__init__`, ni
  `_privee`), hors `@property` ; `self` / `cls` omis ; `@staticmethod` /
  `@classmethod` → `static` ;
- **fonctions publiques de module** et **points d'entrée** : notés dans l'index
  `functions-python.yaml`, rangés par le socle dans le `.extend.yaml` du module.
  FastAPI (`@router.get("/{id}")` avec le `prefix` de l'`APIRouter(...)` du
  fichier ; `api_route(methods=[...])`), Flask (`@app.route(..., methods=[...])`,
  GET par défaut, `@bp.get`, `url_prefix` du `Blueprint(...)` ; `<int:id>` →
  `{id}`), Celery (`@shared_task`, `@app.task` → `task` ; `bind=True` : premier
  paramètre omis). Une route à plusieurs verbes donne une entrée par verbe ; une
  route non littérale garde son point d'entrée avec `route: null` ; seul un objet
  créé dans le fichier par `FastAPI()`, `APIRouter()`, `Flask()`, `Blueprint()`,
  `Celery()` ou nommé comme une application ou un routeur compte. Le préfixe d'un
  `include_router(..., prefix=...)` posé dans un autre fichier n'est pas recomposé.

### Projet SQL Server : module `sql`, procédures et fonctions

Ticket 30c55418. Le parseur `sqlserver-projet` produit un **objet module** par
`.sqlproj` : nature `sql`, langage `tsql`, cible lue dans `<DSP>` (`Sql160`…),
dépendances = `ProjectReference` (reliées au module quand le projet est dans la
source). Les tables et les vues lui appartiennent par la règle du projet le plus
proche ; leurs schémas ne changent pas.

Les **procédures stockées** et **fonctions** T-SQL sont les fonctions du module :
le parseur les note par fichier d'origine dans `functions-sqlserver-projet.yaml`,
le socle les range dans le `.extend.yaml` du module. Seul l'**en-tête** est lu
(nom qualifié `schéma.nom`, paramètres, `RETURNS`), jamais le corps.

```yaml
functions:
- annotations:
  - PROCEDURE
  name: dbo.LireCommande
  parameters:
  - name: '@Id'
    schema:
      format: uuid
      type: string
      x-db-type: UNIQUEIDENTIFIER
    type: UNIQUEIDENTIFIER
  - name: '@Statut'
    optional: true
    schema:
      maxLength: 50
      type: string
      x-db-type: NVARCHAR(50)
    type: NVARCHAR(50) OUTPUT
  source:
    file: db/Boutique.Database/Programmabilite/LireCommande.sql
    line: 1
  visibility: public
```

- `annotations` : `PROCEDURE` ou `FUNCTION` ; `visibility` : toujours `public` ;
- paramètre : nom avec son `@`, type tel qu'écrit (suivi de `OUTPUT` / `READONLY`),
  `schema` sa forme JSON Schema (comme une colonne), `optional` s'il a une valeur
  par défaut ;
- fonction : `returns.type` (type rendu, ou `TABLE` pour une fonction table) ;
- en-tête illisible : la procédure est laissée de côté et signalée dans le compte
  rendu de collecte (`sql_routine_unreadable`) et dans le journal ; les autres
  sont écrites.

### Vue d'un module : `module-<chemin>.view.yaml`

La **personnalisation de l'affichage** du module dans la vue « Application » :
pour commencer, les **ancres des connecteurs** de chaque lien qui part du module
(indexé par le `$id` du module d'arrivée).

```yaml
links:
  module-src-boutique-domain-boutique-domain.schema.yaml:
    from: auto
    to:
      at: 0.5
      side: top
  module-src-boutique-infra-boutique-infra.schema.yaml:
    from: auto
    to: auto
```

- `auto` = ancre calculée par la vue ; sinon `side` (`top`, `right`, `bottom`,
  `left`) et `at` (position le long du côté, de 0 à 1) ;
- **jamais régénéré en entier** : à la collecte, les entrées d'un lien disparu
  sont retirées, celles d'un lien nouveau ajoutées avec `auto`, les autres
  gardées telles quelles ; le `.view` d'un module disparu est retiré avec lui ;
  un `.view` illisible n'est jamais écrasé ;
- un `.view` absent n'empêche rien : la vue prend les valeurs par défaut ;
- écrit dans le brouillon de la personne quand elle déplace une ancre à l'écran
  (ticket a1fd0663, capacité `sources:collect`) : **seule l'entrée du lien
  change** ; « Ancre calculée » remet l'entrée à `from: auto, to: auto` (elle
  n'est pas supprimée : la collecte suivante la recréerait et changerait le
  fichier).

## Exception : `raw/docs/`

Le validateur des conventions **ne s'applique pas** à `sources/<slug>/raw/docs/`
ni à `docs-index.yaml` : les documents (Markdown…) sont recopiés tels quels et ne
sont pas des schémas.
