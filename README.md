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
# renseigner SESSION_SECRET : openssl rand -hex 32
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
