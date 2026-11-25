# Session du 20 au 22 novembre 2026 — Design (identité « Cartographe ») & préparation de l'epic Profils agents / MCP

Branche de reprise : **`feature/profils-agents-mcp`**, créée depuis `dev` (`7d51c0c`), poussée.
Session : https://claude.ai/code/session_01D6wr1MCdA8afEP3yrTDG2D

## Ce qui a été livré

### Design de l'interface (pull request gaelgael5/ossie-studio#1, fusionnée dans `dev`)
Aucun ticket docflow ne porte ce travail.

- **Socle** (`frontend/src/styles.css`) : tokens d'état complétés. Des pages référençaient des tokens jamais définis (`--ok`, `--ko`, `--link`…) : les anciens noms sont gardés en alias. Anneau de focus clavier unique, contrastes au seuil WCAG AA.
- **Identité « Cartographe »** : la couche sémantique est une carte. Polices IBM Plex Sans / Mono embarquées (`@fontsource`), palette nuit / sarcelle / turquoise, motif « station » sur l'entrée active de la nav et des onglets, lettrage en chasse fixe, trame de points, angles vifs. Composant `Brand` + favicon.
- **Migration de toutes les pages au socle** : composant `PageHeader`, plus aucun bouton, champ, tableau ou titre « brut ». Styles inline 234 → 84.
- **Garde-fous** (`frontend/src/styles.test.ts`) : échec si un token référencé n'est pas défini, si un couple texte/fond passe sous AA, ou si une page pose un élément sans classe du socle.
- **Corrections** : libellé des versions du plan de lignes (`RailMap`) qui était rogné ; noms accessibles (profil par utilisateur, paramètre de parsing) ; tableaux alignés sur la première ligne de texte.
- **Documentation** : `docs/identite-visuelle.md` (parti pris, table des classes, page de référence `ForgesPage.tsx`).

### Backlog docflow — clôtures (→ `done`)
Décidées par Gael le 22 novembre, sans dérouler une à une les questions des tickets de clôture.

- **Secrets** (70d32ab0) et ses 2 features.
- **Forges et connexions** (9a173e1d) et ses 5 features, dont « Connexions aux bases de données » qui était resté « En dev » alors que les trois moteurs sont livrés.
- **Langues** (8a2ef904) et ses 3 features.
- **Profils utilisateur et droits d'accès** (c621f948) et ses 11 features.

## Décisions prises avec Gael

- Identité visuelle : piste **B « Cartographe »**, retenue contre « Atelier » et « Signal ».
- **Explorateur raw** (ebf1dfa8) : **ne pas clore**, rien n'est développé. Un autre agent y travaille.
- **Travail en parallèle** : l'autre agent travaille sur la partie raw **directement sur `dev`** ; l'epic Profils agents se fait sur la branche `feature/profils-agents-mcp`.

## Point de reprise : epic « Profils agents et accès MCP » (11cec38d)

État : « Cadré », rien de commencé dans le code. Ordre fixé par l'epic :

1. **Enabler — Surface MCP et catalogue des primitives** (9643b611, « À faire »). Le ticket le classe « compliqué » : **plan à faire valider par Gael avant de coder**.
2. **Profils agents : les primitives MCP accessibles** (f3c0d54c, « Prêt pour dev »).
3. **Clés d'API de service avec choix d'un profil agent** (6af7e748, « Prêt pour dev »).
4. **Connexion OAuth d'un client MCP avec choix du profil agent**, épreuve avec Claude (3aeb9a97, « Prêt pour dev »).
5. **Clôture de l'epic** (5895d9b6), avec Gael.

**La règle qui tient tout** (corps de l'epic) :
- clé d'API : droits = le profil agent de la clé ;
- OAuth : droits = le profil agent choisi, **borné par les capacités de la personne** ;
- chaque primitive MCP déclare la capacité humaine qu'elle suppose.

**Première action** : relire dans docflow l'epic, puis l'enabler et ses critères d'acceptation, et proposer un plan à Gael.

### État du code au départ
- Aucune surface MCP, pas de SDK MCP dans `backend/pyproject.toml`. `authlib` est déjà là (client OIDC).
- Points d'appui : catalogue des capacités `backend/src/ossie_studio/authz/catalog.py` (`ALL_CAPABILITIES`, `SPACE_SCOPED`) ; `load_capabilities` et `load_capabilities_by_space` dans `backend/src/ossie_studio/profiles/service.py`.
- Dernière migration sur `dev` au départ : `0032_slug_accents.sql`.
- Frontend au départ : `tsc` au vert, 102 tests passent. Backend non relancé.

### Règles du travail en parallèle
- Rester sur `feature/profils-agents-mcp`. Intégrer `origin/dev` régulièrement par **merge**, jamais de rebase ni de `--force`.
- **Migrations** : l'autre agent en crée sur `dev`. Juste avant d'en créer une, vérifier le dernier numéro sur `origin/dev` ; avant la pull request, renuméroter si collision. Une migration arrivée sur `dev` est immuable.
- Ne pas toucher aux fichiers de la collecte ni de l'explorateur raw.
- En fin d'epic : pull request vers `dev`.

## Le reste du backlog (au 22 novembre)
- **À une clôture près** (tout le reste en review) : Accès à l'application, Espaces Git, Curation, Brouillon personnel et publication.
- **Une feature à développer chacun** : Parseurs (vue des plugins d'un espace), Sources (contrats OpenAPI dans une source Git), Gouvernance (classer les assets), Socle (journal d'audit).
- **Non démarrés** : Explorateur raw (autre agent), Profils agents (cette branche), Modèle sémantique Ossie (« À cadrer »).
- Les états docflow peuvent être en retard sur le code : vérifier dans le code avant de conclure.

## Notes techniques utiles
- Nouvelle page : suivre `docs/identite-visuelle.md` et imiter `ForgesPage.tsx`. `styles.test.ts` refuse un élément sans classe du socle.
- Contraintes permanentes : jamais de secret en clair ni dans un log ; jamais de `--force`.
