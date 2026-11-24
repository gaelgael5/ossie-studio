# Session du 24 novembre 2026 — Epic « Référentiels » (développement)

Epic docflow **dc0bba30** (workspace `ossie-studio`, bloc `backlog`) : modéliser, tenir et exposer des données de référence (API et MCP), contrôler plus tard leur respect dans raw.
Branche : **`feature/referentiels`**, partie de `dev` (au 24 novembre). `origin/dev` à intégrer par merge (jamais de rebase).
Session : https://claude.ai/code/session_01D6wr1MCdA8afEP3yrTDG2D

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui décide quand fusionner sur `dev`.

## Décisions de Gael (24 novembre)

- Le critère « branche racine en lecture seule » du ticket de format est **gardé tel quel** (« Non touches pas ») : écrire un référentiel quand la branche visée est la racine du dépôt est refusé (`branch_root_readonly`), même si la racine a été adoptée comme branche de base.
- Branche dédiée à l'epic : `feature/referentiels` (« si tu veux »).
- On commence par la base : le format (enabler e9a87d3c), avec ce que prévoit le ticket (modèle YAML, fichiers d'import `imports/<slug>.yaml|.jslt|.exemple.<csv|json>`). Les deux modes d'import (fusion par clé, remplacement) se traiteront avec le ticket d'import.

## Tickets

| Ticket | Statut |
|---|---|
| e9a87d3c — Format d'un référentiel dans l'espace Git (enabler) | En review |
| 2c321d50 — API de lecture des référentiels publiés | Prêt pour dev (suivant conseillé) |
| 829a86a7 — Page Référentiels et onglet Modèle | Prêt pour dev |
| fbe04555 — Onglet Valeurs | Prêt pour dev |
| cda9c43a — Onglet Imports | Prêt pour dev |
| a909dc66 — Importer des valeurs depuis un fichier (écran et web API) | Prêt pour dev |
| e65b590b — Service MCP de lecture des référentiels publiés | Prêt pour dev |
| e80f0cad — Clôture | avec Gael |

## Notes techniques (format)

- Code : `backend/src/ossie_studio/referentiels/` — `model.py` (modèle, `x-key`, types acceptés, YAML ordonné), `values.py` (validation `jsonschema` Draft 2020-12, objets fermés, chemins « clé › chemin », clé unique, JSON trié), `imports.py` (déclaration d'import), `repo.py` (lecture/écriture pures dans la vue), `service.py` (`ReferentielTarget` : écriture dans le brouillon de la personne, refus sur la racine), `errors.py` (`ReferentielError(code, status, problems, **detail)`, `Problem(path, code, detail)`).
- Les objets du modèle sont **fermés** : une propriété non déclarée dans une valeur est refusée (`value_unknown_property`).
- Formats `date` et `date-time` vérifiés par un vérificateur maison (celui de `jsonschema` ignore `date-time` sans `rfc3339-validator`).
- Les règles de validation du brouillon (`drafts/validation.py`) ne regardent que `sources/` et `governance/` : `referentiels/` n'est pas concerné (testé).
- Aucune migration. Prochaine migration libre sur `dev` : **0041** (vérifier `origin/dev` avant).
- Le Postgres de test s'arrête au redémarrage du conteneur : `sudo pg_ctlcluster 16 main start`.
