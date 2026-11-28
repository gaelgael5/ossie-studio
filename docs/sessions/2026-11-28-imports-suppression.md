# Session du 28 novembre 2026 — Imports : fusion seulement et stratégie de suppression (ticket 6d36073b)

Ticket docflow **6d36073b** (workspace `ossie-studio`, bloc `backlog`), traité avant
43478978 (« lier une propriété à un référentiel ») à la demande de Gael : « On le met en
règle dans l'import, comme pour les stratégies de suppression […] traite d'abord les
tickets de suppression à l'import. »

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui
> décide quand fusionner sur `dev`. Pas de PR sans sa demande.

## Ce qui change

- **Fusion seulement** : le mode « remplacement » disparaît (routes, écran). Une
  déclaration enregistrée en `mode: replace` se lit comme la stratégie `all`.
- **Stratégie de suppression** par import (`deletion` : `keep`, `created`, `all`), en haut
  du sous-onglet « Fichier d'exemple », aide à droite, enregistrée au choix
  (`PUT …/suppression`). L'essai suit la stratégie demandée, sinon celle enregistrée.
- **Provenance** : `<slug>.provenance.yaml` (clés créées par l'import), mise à jour à
  chaque import réussi (`imports/provenance.py`, `engine.created_after`).
- **Plan de suppression** (`engine.deletion_plan`) : un absent à supprimer encore utilisé
  dans l'état final est gardé (`target.in_use`, itéré jusqu'à stabilité). Cartographie :
  une relation qui reste vise l'objet ; suppressions par `space.delete_objects`.
- **Compte rendu** : `deleted`, `kept`, `kept_in_use`, absents gardés avec leur raison et
  leurs utilisations (liens vers les fiches).
- **Journal** : `deletion` et les nouveaux compteurs dans `audit_log` ; les éléments
  gardés car utilisés dans `import_kept` (migration 0053) ; `audit.service.record` rend
  l'identifiant de la ligne. `carto_import` rejoint les types de la page Historique.
- **Page « Éléments gardés car utilisés »** : `…/imports/<import>/executions/<id>` sous
  l'écran d'un type de cartographie ou d'un référentiel ; liens depuis le compte rendu et
  depuis la page Historique.

Conventions : `docs/conventions-imports.md`.
