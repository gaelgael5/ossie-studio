# Session du 28 novembre 2026 — Page de publication : progression, tags, filtre, bouton Publier (ticket 48618517)

Quatrième ticket de l'epic « Index des documents et validation avant publication »
(bc3ece1c), après l'index, la file et les règles par type de document. Gael :
« 48618517 et 06171fd3 ».

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui
> décide quand fusionner sur `dev`.

## Ce qui est fait

- `doc_index/state.py` : l'état de validation de la vue, lu dans l'index sans attendre.
- Routes `POST …/draft/validation/passes` et `GET …/draft/validation/state`.
- Index : comparaison disque / index et déclencheur de la dernière passe (migration
  `0057`).
- Conditions de publication lues dans le même état ; la publication refait une passe
  à jour avant de publier.
- MCP : `get_publish_readiness` = la page ; `get_validation_log` rend l'état de la page.
- Écran : progression en haut, puces de filtre avec comptes, recherche, tableau des
  fichiers avec problèmes en tags (dépliables, liens vers le document et son type),
  « déjà en erreur dans la version publiée », bloc « Ce qui bloque », encart « Règle
  d'or ». Le bandeau du brouillon montre une ligne (erreurs, à valider) et mène à la
  page.
- Tests : `tests/test_publication_page.py` (8 tests, un par critère) ; tests adaptés
  (conditions de publication après une passe, journal MCP).

Conventions : `docs/conventions-index-documents.md` (Page de publication et état de
validation).
