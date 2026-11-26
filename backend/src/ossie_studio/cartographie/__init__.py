"""Cartographie de l'espace (epic « Cartographie — types et objets ») : types
d'objets reposant sur ArchiMate 3.2, objets et relations, en YAML dans l'espace
Git sous `cartographie/`. On garde la sémantique d'ArchiMate, pas son format de
fichier.

- `catalog` : le catalogue ArchiMate livré avec l'application ;
- `model` : le format d'un type et d'un objet (lecture, contrôle, écriture) ;
- `space` : la cartographie d'une branche entière (lecture avec signalements,
  écritures refusées tant qu'une clé est en double) ;
- `history` : dates et auteurs tirés de l'historique Git ;
- `service` : lecture et écriture dans le brouillon de la personne.
- `diagram` : le format d'un modèle (diagramme) : modèle, règles, graphique, et le
  modèle hydraté que reçoit le composant `urban-archimate`.
"""
