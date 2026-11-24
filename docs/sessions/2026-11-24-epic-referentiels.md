# Session du 24 novembre 2026 — Epic « Référentiels » (développement)

Epic docflow **dc0bba30** (workspace `ossie-studio`, bloc `backlog`) : modéliser, tenir et exposer des données de référence (API et MCP), contrôler plus tard leur respect dans raw.
Branche : **`feature/referentiels`**, partie de `dev` (au 24 novembre). `origin/dev` à intégrer par merge (jamais de rebase).
Session : https://claude.ai/code/session_01D6wr1MCdA8afEP3yrTDG2D

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui décide quand fusionner sur `dev`.

## Décisions de Gael (24 novembre)

- Le critère « branche racine en lecture seule » du ticket de format est **gardé tel quel** (« Non touches pas ») : écrire un référentiel quand la branche visée est la racine du dépôt est refusé (`branch_root_readonly`), même si la racine a été adoptée comme branche de base.
- Branche dédiée à l'epic : `feature/referentiels` (« si tu veux »).
- **Pas de sujet « branche »** (Gael : « arrete de me parler de branche ») : l'API et le MCP lisent ce qui est publié dans l'espace ; les modifications en cours restent dans le brouillon de chaque personne et partiront avec le reste de son travail.
- **Un seul droit pour l'API et le MCP** (« 1. oui ») : une clé d'API lit les référentiels si son profil agent contient les primitives de lecture ; les tickets API (2c321d50) et MCP (e65b590b) sont faits ensemble.
- On commence par la base : le format (enabler e9a87d3c), avec ce que prévoit le ticket (modèle YAML, fichiers d'import `imports/<slug>.yaml|.jslt|.exemple.<csv|json>`). Les deux modes d'import (fusion par clé, remplacement) se traiteront avec le ticket d'import.

## Tickets

| Ticket | Statut |
|---|---|
| e9a87d3c — Format d'un référentiel dans l'espace Git (enabler) | En review |
| 2c321d50 — API de lecture des référentiels publiés | En review (fait avec le MCP) |
| 829a86a7 — Page Référentiels et onglet Modèle | Prêt pour dev |
| fbe04555 — Onglet Valeurs | Prêt pour dev |
| cda9c43a — Onglet Imports | Prêt pour dev |
| a909dc66 — Importer des valeurs depuis un fichier (écran et web API) | Prêt pour dev |
| e65b590b — Service MCP de lecture des référentiels publiés | En review (fait avec l'API) |
| e80f0cad — Clôture | avec Gael |

## Notes techniques (format)

- Code : `backend/src/ossie_studio/referentiels/` — `model.py` (modèle, `x-key`, types acceptés, YAML ordonné), `values.py` (validation `jsonschema` Draft 2020-12, objets fermés, chemins « clé › chemin », clé unique, JSON trié), `imports.py` (déclaration d'import), `repo.py` (lecture/écriture pures dans la vue), `service.py` (`ReferentielTarget` : écriture dans le brouillon de la personne, refus sur la racine), `errors.py` (`ReferentielError(code, status, problems, **detail)`, `Problem(path, code, detail)`).
- Les objets du modèle sont **fermés** : une propriété non déclarée dans une valeur est refusée (`value_unknown_property`).
- Formats `date` et `date-time` vérifiés par un vérificateur maison (celui de `jsonschema` ignore `date-time` sans `rfc3339-validator`).
- Les règles de validation du brouillon (`drafts/validation.py`) ne regardent que `sources/` et `governance/` : `referentiels/` n'est pas concerné (testé).
- Aucune migration. Prochaine migration libre sur `dev` : **0041** (vérifier `origin/dev` avant).
- Le Postgres de test s'arrête au redémarrage du conteneur : `sudo pg_ctlcluster 16 main start`.

## Notes techniques (lecture publiée : API et MCP)

- Service commun : `referentiels/published.py` — `read_published` lit le contenu et le commit publié ensemble (copie de lecture sous verrou) ; `summaries`, `model_of`, `search_values` (filtres d'égalité typés sur les propriétés simples de premier niveau, tri par clé, pagination, `has_more`), `value_by_key` (clé composée complète, sinon `referentiel_key_incomplete` avec `expected` / `missing`).
- API : `api/referentiels_read.py`, `GET /api/spaces/{space_id}/referentiels`, `…/{name}/modele`, `…/{name}/valeurs?filter=prop:valeur&limit=&offset=`, `…/{name}/valeurs/cle?key=prop:valeur`. Accès : clé d'API (`Bearer osk_…`) dont le profil contient la primitive de la même lecture, ou personne connectée avec `spaces:read` sur l'espace. Sécurité « HTTPBearer » décrite dans le contrat OpenAPI.
- MCP : `mcp_surface/referentiels.py`, primitives `list_referentiels`, `read_referentiel_model`, `search_referentiel_values` (bornée à 100), `get_referentiel_value` ; domaine `referentiels`, capacité `spaces:read`, ouvertes aux clés.
- Codes d'erreur ajoutés : `referentiel_not_found`, `referentiel_unreadable`, `referentiel_filter_unknown`, `referentiel_key_incomplete`, `referentiel_value_not_found`.
- Frontend : après une récupération de `dev`, lancer `npm ci` (nouvelles dépendances, ex. BlockNote, mermaid).
