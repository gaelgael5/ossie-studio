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
**secrets** (chiffrés AES-256-GCM) et les **connecteurs OIDC** depuis
l'interface. Tous les écrans passent par i18n ; les erreurs viennent de
l'API sous forme de codes traduits côté interface.

## Tests et qualité

```bash
cd backend && uv run pytest -v        # nécessite un PostgreSQL (base jetable par test)
cd backend && uv run ruff check src/ tests/ && uv run mypy
cd frontend && npm run typecheck && npm run lint && npm test
```

Les tests backend créent une base jetable par test ; régler
`OSSIE_TEST_ADMIN_DSN` pour pointer un PostgreSQL d'admin (défaut :
`postgresql://ossie:ossie@127.0.0.1:5432/postgres`).
