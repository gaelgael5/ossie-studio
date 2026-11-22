# Session du 22 novembre 2026 — Epic « Primitives MCP d'écriture » (développement)

Epic docflow **e1b0be34** (workspace `ossie-studio`, bloc `backlog`) : l'agent travaille dans le brouillon de la personne.
Branche : **`feature/profils-agents-mcp`** (même branche que l'epic de lecture), `origin/dev` intégré régulièrement par merge (jamais de rebase).
Session : https://claude.ai/code/session_01D6wr1MCdA8afEP3yrTDG2D

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui décide quand fusionner sur `dev`. Pas de pull request par MCP.

## Règles de l'epic

- Toute écriture est **réservée à un agent connecté pour une personne** (OAuth) : jamais proposée à une clé d'API (`person_only` au catalogue, testé), et refusée côté code sans personne (`draft_requires_person`).
- Toute écriture va **dans le brouillon de la personne** (`DraftWorkdir.commit_to_branch`), jamais dans un commit. La réponse donne `revision`, `changed_paths`, et l'objet après écriture.
- **Même service que l'écran** : une seule règle métier (`sources/curation_actions.py`, `governance/actions.py`). Exception assumée : les sources (option 1, ticket de dette 1920df67).
- Refus nommés, **sans écriture**. L'agent est plus strict que l'écran quand c'est utile (champ inconnu → `field_not_found`, décisions de revue vérifiées, contrat jamais remplacé).
- Modifications **partielles** pour l'agent (seuls les champs donnés changent) ; l'écran, lui, remplace l'objet.
- Suppressions annoncées **destructives** au client MCP (il demande confirmation à la personne).
- Pas de profil d'écriture livré : l'administrateur compose sur « Profils agents ».

## Tickets

| Ticket | Statut | Primitives |
|---|---|---|
| efea9234 — Curation | En review | `retain_asset`, `remove_asset`, `enrich_asset`, `refresh_assets` |
| e70ca5de — Règles de tri et revue | En review | `set_tri_rules`, `apply_review` |
| 4e73ac74 — Sources | En review | `declare_source`, `update_source`, `delete_source`, `collect_source`, `get_collect_status`, `list_contracts`, `deposit_contract`, `remove_contract` |
| efebe98d — Gouvernance | En review | `create/update/delete_tag`, `create/update/delete_classification`, `create/update/delete_level`, `reorder_levels`, `create/update/delete_certification`, + lecture `list_certifications` |
| f1b04d25 — Préparer la publication | À faire | |
| ac1229f0 — Publication du brouillon par l'agent | À faire | (confirmée par Gael) |
| 092528df — Clôture | À faire | avec Gael |
| Enabler 1920df67 — Dette : service partagé des sources (option 2) | Plus tard | seulement avec l'accord de Gael |

## Notes techniques

- Code : `backend/src/ossie_studio/mcp_surface/` — `writes.py` (curation, tri, revue ; `open_for_write`, `write_target`, `written_payload`), `source_writes.py`, `governance_writes.py` ; registre `primitives.py` ; catalogue `catalog.py`.
- Gouvernance : `governance/actions.py` porte la logique d'écriture (refus `GovernanceRefused(code, status, **detail)`) ; les routes de l'écran (`api/governance.py`, `api/classifications.py`) l'appellent via `GovCtx.write`, qui rend le résultat et traduit les refus en `ApiError`.
- Corrigé au passage (écran) : réordonner les niveaux avec un doublon est maintenant refusé (`level_order_invalid`) ; un nom de schéma dont on ne peut déduire aucun identifiant est refusé (`scheme_id_invalid`) au lieu d'un 404.
- Libellés des primitives : `mcpPrim.<id>` dans `frontend/src/locales/{fr,en}.ts`.
- Aucune migration dans cet epic. Prochaine migration libre : **0038**.
- Le Postgres de test s'arrête au redémarrage du conteneur : `sudo pg_ctlcluster 16 main start`.
