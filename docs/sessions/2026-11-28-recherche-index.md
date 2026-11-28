# Session du 28 novembre 2026 — La recherche passe par l'index (ticket 06171fd3)

Cinquième ticket de l'epic « Index des documents et validation avant publication »
(bc3ece1c). Gael : « 48618517 et 06171fd3 ».

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui
> décide quand fusionner sur `dev`.

## Ce qui est fait

- `doc_index/search.py` : lecture de l'index d'une vue (documents d'un type, recherche
  inverse sur les références, identité partielle).
- `DraftWorkdir.indexed_view` : la vue lue, comparée de nouveau à Git seulement si la
  tête a bougé (ou si l'extraction a changé de version).
- Index de la cartographie : types et objets lus par le service de la cartographie
  (`INDEXER_VERSION` 2) ; `cartographie/indexed.py` les range comme `Cartographie`.
- `list_documents` : `carto_type` et `carto_objet` listés par l'index (champ
  `index_lister` du registre).
- Suppression d'un objet de cartographie visé par une relation : refus avec les
  relations tirées de l'index.
- `read_document` : identité partielle complétée par l'index ; `identity_ambiguous`.
- Écran des types de cartographie : lu dans l'index.
- Index corrigé : documents rangés en `jsonb` natif ; contenus d'une ancienne version
  de l'extraction réindexés sur place.
- Tests : `tests/test_index_search.py`.

## Reste ouvert (à décider avec Gael)

- L'écran des objets de cartographie garde sa lecture des fichiers (avec son cache
  par empreinte) : il affiche des propriétés et la documentation, absentes de l'index.
- Les autres types de document gardent leur liste par les fichiers.

Conventions : `docs/conventions-index-documents.md` (La recherche passe par l'index).
