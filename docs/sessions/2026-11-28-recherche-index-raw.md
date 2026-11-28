# Session du 28 novembre 2026 — Recherche par l'index (1/6) : schémas raw, assets, extensions, points d'entrée (ticket 4618ee38)

Premier des six tickets « Recherche par l'index » de l'epic « Index des documents et
validation avant publication ». Gael : « prends le ticket 1/6 des schémas raw » ; puis,
sur le choix entre l'index de l'Explorateur et l'index des documents : « oui » à tout
mettre dans l'index des documents (un seul index à terme).

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui
> décide quand fusionner sur `dev`.

## Ce qui est fait

- `sources/raw_facts.py` : faits d'un fichier de `sources/<source>/` (colonne `facts`,
  migration `0058`) et sources reconstruites depuis l'index.
- Explorateur, curation, aperçu d'un module : lecture des fichiers séparée du calcul
  (`build_source_entries`, `curation_objects`, `module_detail(load=…)`).
- Listes `schema_raw`, `schema_asset`, `extension_raw`, `point_entree` par l'index.
- Tests : `tests/test_index_search_raw.py` (17 comparaisons index / fichiers, avec
  refus nommés, sans lecture de fichier ; liste attendue ; brouillon ; commit extérieur).
- Ticket créé : « Un seul index : l'Explorateur raw lit l'index des documents, la table
  raw_explorer_index disparaît ».

Conventions : `docs/conventions-index-documents.md` (Recherche par l'index (1/6)).
