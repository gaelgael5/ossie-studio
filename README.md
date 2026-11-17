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

## Contrôle de santé

`GET /api/health` interroge réellement la base (et la version de schéma) et
les deux volumes : **200** quand tout répond et que les volumes sont
inscriptibles, **503** sinon, en nommant ce qui manque. Jamais de 200 de
complaisance.

## Accès et administration

Au premier démarrage sur une base vide, l'application présente l'écran de
création du **premier administrateur** (email + mot de passe locaux). Dès
que ce compte existe, l'écran disparaît. L'administrateur gère ensuite les
**secrets** (`/secrets`, chiffrés AES-256-GCM) et les **connecteurs OIDC**
(`/oidc/connectors`).

## Tests et qualité

```bash
cd backend && uv run pytest -v        # nécessite un PostgreSQL (base jetable par test)
cd backend && uv run ruff check src/ tests/ && uv run mypy
cd frontend && npm run typecheck && npm run lint && npm test
```

Les tests backend créent une base jetable par test ; régler
`OSSIE_TEST_ADMIN_DSN` pour pointer un PostgreSQL d'admin (défaut :
`postgresql://ossie:ossie@127.0.0.1:5432/postgres`).
