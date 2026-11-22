# Session du 22 novembre 2026 — Epic « Profils agents et accès MCP » (développement)

Branche : **`feature/profils-agents-mcp`**, poussée, `origin/dev` intégré (jusqu'à `6ac59d5`).
Session : https://claude.ai/code/session_01D6wr1MCdA8afEP3yrTDG2D

**Poussé sur `dev` le 22 novembre** à la demande de Gael (avance rapide depuis `6ac59d5`, sans pull request). Vérifié avant : une base déjà en 0035 passe proprement à 0037.

## Tickets livrés (docflow → « En review »)

| Ticket | Contenu |
|---|---|
| Enabler 9643b611 — Surface MCP et catalogue | `/mcp` (SDK `mcp` 2.3, sans état), garde Bearer, 401 conforme, catalogue des primitives, point de contrôle unique, 13 primitives de lecture |
| f3c0d54c — Profils agents | Page « Profils agents », profil livré « Lecture », option « toutes les lectures », suppression refusée si en service |
| 6af7e748 — Clés d'API | Page « Clés d'API », clé `osk_…` montrée une fois, garde anti-élévation |
| 3aeb9a97 — Connexion OAuth | Ossie-studio serveur d'autorisation (boîte à outils OAuth du SDK MCP), consentement avec choix du profil, « Mes connexions agents » |

**Reste** : les essais avec Claude Code (clé) et Claude (OAuth), **après le merge dans `dev`** ; la clôture de l'epic (5895d9b6) avec Gael.

## Règles posées

- **Clé d'API** : droits = son profil agent ; vue **publiée** seule ; les primitives du brouillon ne lui sont pas proposées.
- **OAuth** : droits = profil agent ∩ capacités de la personne, relus à chaque appel ; vue **brouillon** par défaut (publié + brouillon de la personne), éléments marqués `unpublished`. Une capacité gagnée sur un seul espace ne vaut que sur cet espace.
- **Garde anti-élévation** : émettre une clé, lui donner un profil, ou ajouter des primitives à un profil (création, modification, duplication) exige d'avoir soi-même les capacités supposées, sur tous les espaces.
- Profil « Lecture » : se duplique, **ne se modifie ni ne se supprime**.
- Supprimer un profil agent = le masquer (les clés et connexions révoquées gardent son nom).

## Primitives (toutes en lecture)

`list_spaces`, `list_versions`, `get_my_draft`*, `get_publish_readiness`*, `list_sources`, `list_raw_schemas`, `read_raw_schema`, `list_assets`, `read_asset`, `get_tri_rules`, `get_review`, `list_tags`, `list_classifications` (* : agent connecté pour une personne seulement).

## Pour les essais avec Claude (après le merge)

1. Déployer avec **`PUBLIC_BASE_URL` en HTTPS**, joignable depuis Internet (sans elle, l'OAuth répond 404 ; Claude exige HTTPS).
2. nginx relaie déjà `/mcp`, `/authorize`, `/token`, `/register`, `/revoke` et `/.well-known/oauth-*` (`frontend/nginx.conf`).
3. **Claude Code** : Réglages → Clés d'API → émettre une clé « Lecture » → copier la commande affichée :
   `claude mcp add --transport http ossie-studio <adresse>/mcp --header "Authorization: Bearer <clé>"`
4. **Claude** : ajouter un connecteur personnalisé avec l'adresse `<adresse>/mcp`, se connecter, choisir « Lecture ».
5. Demander de lister les sources d'un espace et de lire un asset.

## Migrations

`0033_agent_profiles`, `0034_api_keys`, `0036_oauth`, `0037_agent_profile_deleted`. La `0035` est à l'explorateur raw (autre session). Le runner joue toute migration absente, quel que soit son numéro : une base en 0035 reçoit bien 0033 et 0034. Prochaine migration libre : **0038**.

## Nouvelles capacités

« Profils agents », « Clés d'API », « Connexions d'agents » (consulter / modifier) : à cocher dans les profils utilisateurs qui en ont besoin (l'administrateur les a d'office).

## Notes techniques

- Code MCP : `backend/src/ossie_studio/mcp_surface/` (garde, catalogue, droits, lectures), OAuth : `backend/src/ossie_studio/oauth/`.
- Une primitive se déclare dans `catalog.py` (capacité supposée obligatoire, testée) et s'implémente dans `reads.py` / `primitives.py`.
- Le Postgres de test s'arrête au redémarrage du conteneur : `sudo pg_ctlcluster 16 main start`.
- Fin de session : backend 901 tests, frontend 155, `ruff` / `mypy` / `eslint` / `tsc` au vert.
