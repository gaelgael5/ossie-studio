# Session du 28 novembre 2026 — Recherche par l'index (2/6) : modèles et valeurs des référentiels (ticket c854ad1e)

Deuxième des six tickets « Recherche par l'index ». Gael : « prends le ticket 2/6 des
référentiels » ; puis « oui » à garder les valeurs entières dans l'index.

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui
> décide quand fusionner sur `dev`.

## Ce qui est fait

- `referentiels/indexed.py` : faits d'un fichier de référentiel et référentiels
  reconstruits depuis l'index.
- `referentiels/published.py` : lecture séparée du calcul.
- Listes `referentiel_modele` et `referentiel_valeur` par l'index.
- Tests : `tests/test_index_search_referentiels.py` (14 comparaisons index / fichiers,
  refus nommés compris, sans lecture de fichier ; listes attendues ; brouillon ; commit
  extérieur).

Conventions : `docs/conventions-index-documents.md` (Recherche par l'index (2/6)).
