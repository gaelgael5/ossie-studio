# Session du 28 novembre 2026 — Recherche par l'index (3/6) domaines et (4/6) sources, contrats, règles de tri (tickets fc5bb3b4, 3492625e)

Gael : « prends les tickets 3/6 et 4/6 et 5/6 ».

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui
> décide quand fusionner sur `dev`.

## Ce qui est fait

- 3/6 : `domains/indexed.py` ; `read_domain` → `domain_from`, `list_domains` →
  `domains_of` ; listes des domaines, datasets, relations et références par l'index.
  Tests : `tests/test_index_search_domains.py`.
- 4/6 : manifeste strict, index des contrats et empreintes des contrats dans l'index ;
  listes des sources, contrats et règles de tri par l'index. Tests :
  `tests/test_index_search_sources.py`.

Conventions : `docs/conventions-index-documents.md` (Recherche par l'index (3/6) et (4/6)).
