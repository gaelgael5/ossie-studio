# ossie-studio

Construction d'une couche sémantique (Apache Ossie) à partir de sources
extraites (Snowflake, dépôts Git) : collecte brute, curation des assets,
relations, ontologie.

Le backlog du projet vit dans docflow, workspace `ossie-studio`.

## Pile

- **Backend** : Python 3.12+, FastAPI, asyncpg (SQL brut, requêtes
  paramétrées), pydantic v2 (`extra="forbid"`), structlog, géré avec uv sous
  `backend/`. Migrations en **fichiers SQL numérotés immuables** joués par un
  runner maison (table de suivi, empreintes). Pas d'ORM.
- **Frontend** : React + Vite, TypeScript strict, TanStack Query, i18n
  (fr/en), Vitest, sous `frontend/`.
- **Livraison** : `docker compose` (PostgreSQL, backend, frontend) et
  `dev-deploy.sh` idempotent.

## Deux volumes mappés

| Volume | Contenu | Si perdu |
|---|---|---|
| **travail** (`WORK_VOLUME_PATH`) | copies de travail des espaces Git, versionnées | seuls les commits non publiés sont perdus |
| **sources** (`SOURCES_VOLUME_PATH`) | code descendu des sources, jamais versionné | rien de définitif : tout se re-descend |

Les deux chemins sont obligatoires : sans eux, le backend refuse de démarrer.
Ils vivent hors de l'image et de la couche conteneur.

## Déploiement (dev/test)

```bash
./dev-deploy.sh dev
```

Idempotent : met à jour le dépôt, génère les secrets manquants dans `.env`
(jamais ceux déjà présents), construit et démarre la stack, joue les
migrations au démarrage du backend, puis attend `GET /api/health`.

## Développement local (sans Docker)

Prérequis : [uv](https://docs.astral.sh/uv/), Node 22, un PostgreSQL joignable.

```bash
cd backend && cp .env.example .env
# renseigner SESSION_SECRET et SECRETS_MASTER_KEY : openssl rand -hex 32
```

```bash
cd backend && uv run uvicorn ossie_studio.app:create_app --factory --reload
```

```bash
cd frontend && npm install && npm run dev
```

## Internationalisation (i18n)

L'interface s'affiche en **français** ou en **anglais** (i18next +
react-i18next, traductions embarquées). La langue se résout dans l'ordre :
choix mémorisé (localStorage) → langue du navigateur → anglais. Un sélecteur
de langue est présent sur la page, change la langue sans recharger. Les
dates suivent la langue courante (`Intl`).

Le **backend ne rédige aucun message affiché** : il renvoie des codes
d'erreur stables (`{"error": "<code>", "error_args": {...}}`) que l'interface
traduit. La liste des codes est partagée (`frontend/src/errorCodes.json`),
et des tests garantissent que chaque code a une traduction FR et EN.

Deux garde-fous CI : une chaîne visible écrite en dur dans un composant fait
échouer `eslint` (`i18next/no-literal-string`), et une clé présente dans une
langue mais absente de l'autre fait échouer les tests.

## Contrôle de santé

`GET /api/health` interroge réellement la base (et la version de schéma) et
les deux volumes : **200** quand tout répond et que les volumes sont
inscriptibles, **503** sinon, en nommant ce qui manque. Jamais de 200 de
complaisance.

## Architecture

Le backend est une **API JSON** (`/api/*`) sans rendu de pages ; toute
l'interface est une **SPA React** (sous `frontend/`) qui la consomme, plus
les redirections du flux OIDC (`/auth/oidc/*`). Le navigateur ne voit
qu'une origine : nginx (ou le proxy Vite en dev) relaie `/api` et `/auth`
vers le backend.

## Accès et administration

Le SPA route selon `GET /api/session` (`bootstrap_required`,
`authenticated`, `user`). Au premier démarrage sur une base vide, il mène à
l'écran de création du **premier administrateur**. Dès que ce compte existe,
cet écran n'est plus accessible. L'administrateur gère ensuite les
**secrets** (chiffrés AES-256-GCM), les **connecteurs OIDC** et les
**forges et connexions** (GitHub, Azure DevOps, GitLab) depuis l'interface.
Tous les écrans passent par i18n ; les erreurs viennent de l'API sous forme
de codes traduits côté interface.

### Forges et connexions

Une connexion à une forge Git (par jeton d'accès) est vérifiée **avant**
d'être enregistrée (appel réel à la forge) ; le jeton est stocké comme
secret chiffré (catégorie = forge), la connexion n'en garde que la
référence. Chaque forge est une **stratégie** derrière un même contrat
(`ForgeStrategy`) : vérifier l'identité, lister les dépôts lisibles (avec
pagination). Ajouter une forge ou un mode OAuth plus tard ne touche ni la
page ni les consommateurs.

### Espaces Git

Un **espace Git** rattache un dépôt (sous une connexion) où Ossie Studio
pourra écrire, pousser et taguer. La création **éprouve réellement les cinq
droits** par un essai git — lire/récupérer, créer une branche, pousser un
commit, poser un tag, supprimer la branche — avec une branche et un tag de
vérification `ossie-verify-<aléatoire>`, puis nettoie. Sur un dépôt vide,
`main` est posée (seul effet durable, annoncé). Un droit en échec nomme le
droit et sa cause ; un nettoyage raté affiche le nom exact à supprimer à la
main. Le jeton est remis à git par l'environnement (`GIT_ASKPASS`), jamais
dans l'URL, un argument ou un fichier. Supprimer une connexion utilisée par
un espace est refusé. La sonde est injectable, testée contre des dépôts bare
locaux ; l'épreuve contre les vraies forges (ou une Gitea jetable) est une
dépendance humaine (dépôts et jetons fournis hors conversation).

**Branches de travail** : chaque espace a un écran qui gère ses branches. La
**racine du dépôt reste en lecture seule** ; Ossie Studio n'écrit que dans
des branches `work/<slug>`. La **base** se tire de la racine (avec une
arborescence initiale `sources/` + description si absente) ; une **fille** se
tire de la tête de la base (pointeur, sans commit). Les copies de travail
sont persistantes, un répertoire par (espace, branche) sur le volume de
travail, réalignées sur la tête distante à chaque geste, avec un verrou par
répertoire. La **branche regardée est retenue par compte**.

## Tests et qualité

```bash
cd backend && uv run pytest -v        # nécessite un PostgreSQL (base jetable par test)
cd backend && uv run ruff check src/ tests/ && uv run mypy
cd frontend && npm run typecheck && npm run lint && npm test
```

Les tests backend créent une base jetable par test ; régler
`OSSIE_TEST_ADMIN_DSN` pour pointer un PostgreSQL d'admin (défaut :
`postgresql://ossie:ossie@127.0.0.1:5432/postgres`).
