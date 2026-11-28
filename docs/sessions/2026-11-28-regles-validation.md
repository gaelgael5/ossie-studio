# Session du 28 novembre 2026 — Règles de validation par type de document et règle d'or (ticket d7139b17)

Troisième ticket de l'epic « Index des documents et validation avant publication »
(bc3ece1c), après l'index (15863091) et la file (6b171b74). Gael : « prends le ticket
des règles de validation ».

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui
> décide quand fusionner sur `dev`.

## Ce qui est fait

- Registre : `RuleDeclaration` et `validation` sur chaque type de document.
- `doc_index/rules.py` : niveaux par type, document seul, références, règles
  `carto` et `referentiel` ; `ParseableRule.check_file`.
- Passes : appliquent les déclarations ; `Validation.ensure` (passe à jour avant de
  lire un journal).
- Journal lu dans l'index (`preparation.draft_validation`) ; conditions de publication
  `validation_errors` (règle d'or) et `validation_pending`.
- Retrait de l'acceptation des avertissements : route, primitive, message du commit,
  écran ; migration `0056`.
- Écran : le journal affiche « bloquant » / « informatif » et mène au document en cause.
- Tests : `tests/test_validation_rules.py` ; tests existants adaptés à la règle d'or.

Conventions : `docs/conventions-index-documents.md` (Règles de validation…).
