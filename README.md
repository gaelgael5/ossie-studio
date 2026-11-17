# ossie-studio

Construction d'une couche sémantique (Apache Ossie) à partir de sources
extraites (Snowflake, dépôts Git) : collecte brute, curation des assets,
relations, ontologie.

Le backlog du projet vit dans docflow, workspace `ossie-studio`.

## Démarrage rapide (dev)

Prérequis : [uv](https://docs.astral.sh/uv/), Docker (pour PostgreSQL).

```bash
docker compose up -d db
```

```bash
cd backend && cp .env.example .env
# renseigner SESSION_SECRET et SECRETS_MASTER_KEY : openssl rand -hex 32
```

```bash
cd backend && uv sync
```

```bash
cd backend && uv run uvicorn ossie_studio.app:create_app --factory --reload
```

Au premier démarrage sur une base vide, l'application présente l'écran de
création du **premier administrateur** (email + mot de passe locaux). Dès
que ce compte existe, l'écran disparaît définitivement.

### Secrets

L'administrateur gère les secrets de l'application depuis `/secrets` :
chaque secret a un slug (identité immuable), un libellé, une catégorie
(`github` pour l'instant) et une valeur **chiffrée en base** (AES-256-GCM).
La valeur n'est jamais renvoyée au navigateur ni journalisée ; le reste de
l'application la récupère côté serveur par son slug, via le type opaque
`SecretValue` (`.reveal()` au point d'injection uniquement).

### Connecteurs OIDC

L'administrateur déclare des fournisseurs d'identité OIDC depuis
`/oidc/connectors` (ajouter / supprimer). Chaque connecteur apparaît comme
un bouton sur l'écran de connexion, à côté du formulaire local. Flux
Authorization Code + PKCE ; l'identité est ancrée sur le couple
`(issuer, sub)`, jamais sur l'email. Au premier login d'un couple inconnu,
le compte est retrouvé par **email vérifié** (l'administrateur créé en local
garde ainsi son rôle en revenant par OIDC) ; email absent ou non vérifié →
connexion refusée. Le `client_secret` est chiffré via le même service que
les secrets. Aucun nom de fournisseur n'apparaît dans le code : n'importe
quel IdP conforme fonctionne.

### État du schéma et migrations

Le schéma reste réconcilié de façon additive au démarrage
(`metadata.create_all`), source unique de vérité. `password_hash` est
nullable (un compte provisionné par OIDC n'a pas de mot de passe local).
Aucune instance n'étant encore déployée, aucune migration numérotée n'est
nécessaire ; l'outillage Alembic sera introduit au premier changement de
schéma sur une base **déjà en production**.

## Tests et qualité

```bash
cd backend && uv run pytest -v
```

```bash
cd backend && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
```

```bash
cd backend && uv run mypy
```

Les tests tournent sur une base SQLite jetable par test ; la production
vise PostgreSQL.

## État du schéma

Le schéma est réconcilié de façon **additive** au démarrage
(`metadata.create_all`). Aucune migration numérotée n'existe encore :
l'outillage de migration sera introduit au premier changement non additif
(renommage ou suppression).
