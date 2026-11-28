# Session du 28 novembre 2026 — File de validation sérialisée (enabler 6b171b74)

Deuxième ticket de l'epic « Index des documents et validation avant publication »
(bc3ece1c), après l'index (15863091). Gael : « oui » pour le prendre.

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui
> décide quand fusionner sur `dev`. Pas de PR sans sa demande.

## Ce qui est fait

- Migration `0055_validation_queue.sql` : `validation_requests` (une demande en
  attente par vue, une seule passe en cours) ; `validated_at` / `validation_error`
  sur les vues de l'index.
- `doc_index/queue.py` : demander, prendre, progresser, terminer, reprendre une passe
  interrompue, lire l'état.
- `doc_index/passes.py` : la passe (comparaison, document seul réutilisé par
  empreinte, règles entre fichiers, états) et le travailleur (une passe à la fois,
  demande périodique).
- `DraftWorkdir.run_in_view` : exécuter dans la vue d'une personne.
- Déclencheurs : import, collecte, lot `write_documents`, page de publication,
  publication. Réglage `validation_interval_minutes` (page « Paramètres », unité
  minutes). Route `GET …/draft/validation/file`.
- Tests : `tests/test_validation_queue.py` (les 7 critères et une vraie passe).

Conventions : `docs/conventions-index-documents.md` (La file de validation).
