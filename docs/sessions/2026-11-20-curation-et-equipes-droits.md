# Session du 20 novembre 2026 — Curation (fin) & Équipes porteuses de droits

Branche : `dev` (tout commité et poussé).
Session : https://claude.ai/code/session_01D6wr1MCdA8afEP3yrTDG2D

## Ce qui a été livré (tickets → `en_review`)

### Epic « Curation — retenir, enrichir et suivre les assets » (15394be1)
Les 5 features constructibles sont faites ; la **clôture** (7e8d5797) reste à traiter avec Gael + données réelles.

- **Enrichir un asset** (eabd92f0) — clés `x-` métier fermées (`x-businessName`, `x-description`, `x-concept`, `x-tags`), objet + champ.
- **Rafraîchir depuis le brut** (0cd6b2eb) — fusion structurelle ; structure du brut, **toutes** les `x-` conservées ; champ disparu gardé (« disparu »), objet disparu « orphelin » ; marquage **calculé à la volée**.
- **Revue des changements** (be32ceb6) — point de reprise `sources/<slug>/review.yaml` ; diff reprise→HEAD limité aux assets retenus + nouveaux objets ; décisions en **un commit** ; garde `review_stale` (collecte concurrente) et `review_branch_behind` ; historique Git d'un schéma raw.
- **Règles de tri par source** (f5309c8b) — section `tri` du manifeste ; cibles nom/type d'objet, nom de champ, chemin, schéma SQL ; motif glob/regex ; filtre la vue + la revue, ne supprime rien ; objet retenu jamais caché (conflit signalé).

### Epic « Profils utilisateur et droits d'accès » (c621f948) — évolution « les équipes portent des droits »
Revient **volontairement** sur deux décisions du 17 nov : « une seule équipe » et « équipe = affichage seul ». Corps de l'epic et ticket de clôture mis à jour.

- **F1 — Appartenance multiple** (16e60690) — table `user_teams` (N↔N, origine `oidc`/`manual`), reprise sans perte, `users.team_id`/`team_pinned` supprimés ; page Utilisateurs multi-équipes.
- **F2 — Une équipe porte un profil ; union** (c62e54a8) — `teams.profile_id` (FK `ON DELETE SET NULL`) ; `load_capabilities` = **union** profil perso + profils des équipes (admin si l'un l'est), au seul point relu par requête ; page **Équipes** (`/teams`) ; **droit fort `profiles:write`** pour associer un profil.
- **F3 — Synchro OIDC multi-équipes** (90d88bd4) — `apply_team_mapping` synchronise l'ensemble du claim (chaque valeur = une équipe) ; manuels préservés ; idempotent.

## Décisions structurantes prises avec Gael

- **Curation** : point de reprise par **fichier versionné** (pas un tag déplaçable) ; collecte concurrente → **refus `review_stale`, on rouvre** ; **un seul point de reprise global** (non-décidé = laissé tel quel) ; marquage disparu/orphelin **calculé à la volée**.
- **Équipes** : appartenance **multiple** ; capacités = **union** (l'équipe ajoute, jamais ne retire) ; associer un profil à une équipe = **droit fort** (une équipe « Administrateur » rendrait ses membres admins) ; rattachement **manuel** jamais retiré par l'OIDC ; profil d'équipe **global** (pas de portée par espace/source — sujet distinct, non tranché).

## Qualité
Back ~592 tests, front 30, `ruff`/`mypy`/`eslint`/`tsc` au vert à chaque commit. Migrations jusqu'à `0024_team_profile.sql`.

## Point de reprise (prochaine fois)

1. **Clôture de l'epic Curation** (7e8d5797) — vérification d'intention, **avec Gael** + données réelles.
2. **Clôture de l'epic Profils/Droits** (f3883c7c) — vérification d'intention, **avec Gael**. Deux questions : (a) membre d'une/plusieurs équipes portant un profil → **exactement** les bonnes capacités, retrait inclus ? (b) équipe « Administrateur » bien verrouillée derrière `profiles:write` ?
3. **Sujet ouvert non tranché** : cloisonner les droits **par espace/source** (hors des epics actuels).
4. Autres epics disponibles (non commencés) : Modèle sémantique Ossie, Explorateur raw, Profils agents & accès MCP, etc.

## Notes techniques utiles
- asyncpg rend les colonnes Postgres `"char"` en **bytes** (piège récurrent des parseurs catalogue).
- Postgres de test tombe parfois entre deux grosses suites (`ConnectionRefused 5432`) → `sudo pg_ctlcluster 16 main start` ; le test échoué repasse seul (transitoire).
- Contraintes permanentes : jamais de secret en clair/log ; push uniquement sur `dev` ; jamais de `--force`.
