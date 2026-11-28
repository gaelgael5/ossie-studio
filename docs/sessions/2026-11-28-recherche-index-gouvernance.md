# Recherche par l'index (5/6) : gouvernance — ticket bb279dd6

## Ce qui change

`list_documents` lit l'index, sans parcourir le dépôt, pour `tag`, `classification`,
`certification`, `regle_tag`, `regle_classification` et `profil_relation`.

## Comment

- L'index garde le texte des fichiers de gouvernance que les listes lisent
  (`governance/files.py`) : fichiers courts ; les retraits de classification ne sont pas
  gardés.
- Les lecteurs de gouvernance lisent par un accès commun (`DiskFiles` ou `IndexFiles`) :
  la même règle pour les fichiers et pour l'index.
- Règles et profil fournis par Ossie : ajoutés par le même code, au même rang.
- `INDEXER_VERSION` 8 : les contenus déjà indexés sont réindexés à la lecture suivante.

## Tests

`tests/test_index_search_governance.py` : même réponse qu'avec les fichiers (14 cas,
lectures de fichiers comptées), fichiers illisibles, tag enregistré dans le brouillon,
fichier ajouté par un commit extérieur, accès par l'index identique au disque.
