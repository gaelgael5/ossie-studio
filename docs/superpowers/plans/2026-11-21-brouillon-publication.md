# Brouillon personnel et publication sous son identité — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** chaque utilisateur prépare ses modifications dans un brouillon personnel (en base, seulement ce qu'il a changé), fusionne facilement quand la branche a bougé, et publie en un commit sous son nom avec son propre jeton ; tout le travail, gouvernance comprise, vit dans Git.

**Architecture:** la copie Git par (espace, branche) devient une copie de lecture (jeton de l'espace, jamais de commit). Les modifications d'un utilisateur sont stockées en base (fichier complet + empreinte de base par objet). Lecture et écriture passent par une **vue éphémère** : un worktree Git temporaire, posé sur la copie de lecture, où le brouillon est appliqué. Il réutilise tel quel le contrat `read_fn(Path)` / `write_fn(Path)` des 22 points d'appel actuels. La publication pose un verrou consultatif PostgreSQL par branche, construit un commit dans un worktree temporaire et pousse avec le jeton personnel.

**Tech Stack:** Python 3.12, FastAPI, asyncpg (SQL brut), pydantic v2, structlog, git CLI ; React + TypeScript strict, TanStack Query, i18next, Vitest.

**Spec:** epic docflow `ossie-studio` « Brouillon personnel et publication sous son identité — le travail vit dans Git » (e5e72d1a-13e2-4365-89cf-3214fdc68f3e) et ses features ; feature « Droits par espace » (f3b15e37) ; feature « Connexion : état "jamais testé" » (d06a44bd) ; règle `docs/regle-git-source-de-verite.md`.

## Décision d'architecture à valider : la vue éphémère

Le brouillon durable ne contient **que les objets modifiés**, en base, comme demandé. Mais les 22 lectures et écritures actuelles (`read_branch`, `commit_to_branch`) travaillent sur un **répertoire** (`pathlib`, `glob`, parseurs tree-sitter). Pour qu'un utilisateur lise « la branche avec son brouillon par-dessus », il faut un répertoire qui montre cette superposition.

- **Retenu : vue éphémère.** Pour un utilisateur qui **a un brouillon et qui est actif**, on crée un `git worktree` détaché sur la tête de la copie de lecture. Il partage l'historique de celle-ci, donc seul le checkout prend de la place. On y applique le brouillon, et on le réutilise tant que ni le brouillon ni la tête publiée ne changent. On le **supprime après 30 minutes d'inactivité**, à la publication et à l'abandon du brouillon. Un utilisateur sans brouillon lit la copie de lecture : aucun coût supplémentaire.
  - L'espace occupé est donc borné par le **nombre d'utilisateurs en train de travailler**, pas par le nombre de brouillons. La vue n'est jamais une source de vérité : elle se reconstruit à partir de la base.
- **Écarté : un système de fichiers virtuel**, c'est-à-dire réécrire toutes les lectures sur une abstraction d'arbre. C'est fidèle à la lettre « rien que le delta », mais il faudrait réécrire les 22 points d'appel et tous les parseurs qui lisent des fichiers. Le risque de régression est élevé pour un gain disque seulement transitoire.

**C'est l'écart à valider :** une copie de travail temporaire existe pendant que quelqu'un travaille, puis elle disparaît.

## Global Constraints

- Migrations SQL numérotées immuables, à partir de `0025_` ; une migration jouée ne se modifie jamais.
- Requêtes paramétrées uniquement ; une transaction par écriture logique.
- pydantic v2 avec `extra="forbid"` sur chaque corps d'API.
- Aucun secret (jeton, valeur chiffrée, code) dans un log, une réponse ou une configuration git ; le jeton passe par l'askpass existant (`spaces/gitcli.py`).
- Tout nouveau code d'erreur est ajouté dans `backend/src/ossie_studio/errors.py` **et** `frontend/src/errorCodes.json` (test `test_errors.py`).
- Front : tout appel API par TanStack Query ; tout libellé en i18n fr **et** en ; tests Vitest en `describe`/`it` ; nouveaux fichiers ≤ 300 lignes.
- Commits préfixés `ossie: ` pour ce qu'écrit l'application dans le dépôt d'un espace (existant).
- Jamais de `push --force`, jamais de commit de fusion.
- Vérifications à chaque tâche : `ruff check src tests`, `mypy src`, `pytest` (PostgreSQL de test dans Docker sur `test1`, auth scram), `npx tsc --noEmit`, `npx eslint src/`, `npx vitest run`.
- Livraison : push sur `dev`, puis `./dev-deploy.sh` sur `test1`, puis lecture des journaux réels, à la fin de chaque groupe de tâches (après T3, T6, T10, T13).

## Review Focus

1. **Espace ou branche supprimés alors que des brouillons existent** : les brouillons, décisions et vues sont supprimés en cascade, sans erreur → test dans T4.
2. **Chemin hors dépôt dans un brouillon** (`../`, `.git/…`, chemin absolu) produit par une écriture : refusé, rien n'est enregistré → test dans T4.
3. **Deux onglets du même utilisateur qui écrivent en même temps** : les écritures sont sérialisées par le verrou de la vue, sans aucune modification perdue → test dans T5.
4. **Jeton personnel révoqué sur la forge entre le test et la publication** : push refusé pour une raison d'accès, verrou levé, brouillon intact, erreur nommée qui renvoie vers « Saisir le secret » → test dans T10.
5. **Collecte qui produit un très gros objet raw** (plusieurs Mo) : il est stocké et superposé sans erreur jusqu'à 20 Mo par objet ; au-delà, refus nommé `draft_object_too_large` → test dans T4.

---

### Task 1 : Connexion — état « jamais testé » quand le secret change

**Files:**
- Modify: `backend/src/ossie_studio/secretstore/service.py` (mise à jour de la valeur d'un secret)
- Modify: `backend/src/ossie_studio/api/secrets.py`
- Modify: `frontend/src/pages/ForgesPage.tsx`, `frontend/src/pages/DatabasesSection.tsx`, `frontend/src/locales/{fr,en}.ts`
- Test: `backend/tests/test_secret_usages.py`

**Interfaces:**
- Produces: `async def db_reset_status_for_secret(conn: asyncpg.Connection, secret_slug: str) -> int` dans `secretstore/usages.py`. Elle passe `last_status='never_tested'` et efface `last_checked_at` et `last_error_code` sur `connections` et `db_connections` ; elle rend le nombre de lignes touchées. T3 la réutilise pour les identifiants personnels.

- [ ] **Step 1 :** tests `test_changer_la_valeur_remet_les_connexions_a_jamais_teste` (deux connexions, une forge et une base, ont `last_status == "never_tested"` et `last_checked_at is None`), `test_changer_le_libelle_ne_touche_pas_l_etat`, `test_secret_inutilise_sans_effet`.
- [ ] **Step 2 :** les lancer → échec.
- [ ] **Step 3 :** appeler `db_reset_status_for_secret` dans la même transaction que la mise à jour de la valeur, uniquement si la valeur change. Front : badge « jamais testé » (`connectionNeverTested`).
- [ ] **Step 4 :** tests verts + vitest.
- [ ] **Step 5 :** commit `Connexions : état « jamais testé » quand la valeur du secret change`.

### Task 2 : Droits par espace

**Files:**
- Create: `backend/src/ossie_studio/db/migrations/0025_space_team_profiles.sql`. Table `space_team_profiles(space_id → git_spaces ON DELETE CASCADE, team_id → teams ON DELETE CASCADE, profile_id → profiles ON DELETE CASCADE, PRIMARY KEY(space_id, team_id))`.
- Modify: `backend/src/ossie_studio/authz/catalog.py`. Ajoute `SPACE_SCOPED: frozenset[str]` : `spaces:read|branch|publish|version` et tous les `sources:*`, `curation:*`, `governance:*`.
- Modify: `backend/src/ossie_studio/profiles/service.py`, `backend/src/ossie_studio/api/common.py`, `api/spaces.py`, `api/branches.py`, `api/sources.py`, `api/curation.py`
- Create: `backend/src/ossie_studio/api/space_teams.py` (routes `GET/PUT/DELETE /api/spaces/{space_id}/teams[/{team_id}]`, garde `profiles:write`)
- Create: `frontend/src/pages/SpaceTeamsSection.tsx` (dans l'écran de l'espace)
- Test: `backend/tests/test_space_rights.py`, `frontend/src/pages/SpaceTeamsSection.test.tsx`

**Interfaces:**
- Produces: `async def load_capabilities(conn, user, space_id: uuid.UUID | None = None) -> set[str]`. Sans `space_id`, le résultat est identique à aujourd'hui. Avec `space_id`, on ajoute les capacités `SPACE_SCOPED` des profils des équipes de l'utilisateur rattachées à cet espace ; un profil administrateur dans un rattachement vaut `SPACE_SCOPED`.
- Produces: `def require_space_capability(capability: str) -> Callable[..., Awaitable[User]]` dans `api/common.py`. Elle lit `space_id` dans le chemin et répond 401, 403 ou 404 (`space_not_found`). Les alias `SpacesRead`, `SpacesPublish`, `SourcesDeclare`, etc. des routes sous `/api/spaces/{space_id}/…` basculent vers elle.
- Produces: `async def db_user_spaces_with(conn, user, capability) -> list[uuid.UUID] | None` (None signifie tous les espaces, quand le profil de base suffit).
- Produces (accroche pour T3) : chaque création ou modification de rattachement appelle `ensure_credentials_for_space_team(conn, space_id, team_id)`. T3 fournit cette fonction ; d'ici là, c'est une fonction vide documentée.

- [ ] **Step 1 :** tests (critères 1 à 9 de la feature) : `test_equipe_rattachee_donne_publish_sur_cet_espace_seulement` (200 sur E1, 403 sur E2), `test_profils_differents_par_espace`, `test_liste_des_espaces_filtree_sans_spaces_read_de_base`, `test_rattachement_admin_ne_donne_aucune_capacite_globale` (`secrets:read` reste 403), `test_retrait_effectif_a_la_requete_suivante`, `test_capacite_globale_du_profil_rattache_ignoree`, `test_suppression_equipe_ou_espace_supprime_rattachements`, `test_gerer_rattachements_sans_profiles_write_403`. Toute la suite existante reste verte (non-régression).
- [ ] **Step 2 :** les lancer → échec.
- [ ] **Step 3 :** migration, catalogue, `load_capabilities` (requête UNION ALL, troisième branche via `space_team_profiles`), garde, filtrage de `GET /api/spaces`, routes de rattachement. Front : section « Équipes de l'espace » (équipe, profil, retirer) et droits par espace sur la fiche utilisateur.
- [ ] **Step 4 :** pytest complet + vitest.
- [ ] **Step 5 :** commit `Droits par espace : une équipe rattachée à un espace avec un profil`.

### Task 3 : Identifiant personnel par forge

**Files:**
- Create: `backend/src/ossie_studio/db/migrations/0026_personal_credentials.sql`. Table `personal_credentials(id, user_id → users ON DELETE CASCADE, connection_id → connections ON DELETE CASCADE, value_encrypted bytea NULL, status text NOT NULL` dans `('a_saisir','jamais_teste','valide','refuse')`, `forge_login text, error_code text, checked_at timestamptz, UNIQUE(user_id, connection_id))`.
- Create: `backend/src/ossie_studio/credentials/service.py`, `backend/src/ossie_studio/api/credentials.py` (préfixe `/api/me/credentials`)
- Modify: `backend/src/ossie_studio/forges/base.py`, `github.py`, `gitlab.py`, `azure.py`
- Create: `frontend/src/components/CredentialBlock.tsx`
- Test: `backend/tests/test_personal_credentials.py`, `backend/tests/fake_forge.py` (permissions de dépôt), `frontend/src/components/CredentialBlock.test.tsx`

**Interfaces:**
- Produces: `async def ensure_personal_credential(conn, user_id, connection_id) -> PersonalCredential` (idempotente, état initial `a_saisir`) et `async def ensure_credentials_for_space_team(conn, space_id, team_id) -> None`. Cette dernière crée l'identifiant pour chaque membre de l'équipe dès que le profil rattaché porte une capacité d'écriture déclinable.
- Produces: `async def resolve_personal_token(conn, crypto, user_id, connection_id) -> Secret | None`. Elle rend None si l'état n'est pas `valide`. Utilisée par T10.
- Produces sur `ForgeStrategy` : `async def verify_write(self, client, url: str, repo: str, token: str) -> WriteCheck`, avec `WriteCheck(login: str, can_write: bool | None)` (`None` signifie non vérifiable). GitHub : `GET /repos/{owner}/{repo}` → `permissions.push`. GitLab : `GET /projects/:path` → niveau d'accès ≥ 30. Azure : `None`.
- Routes : `GET /api/me/credentials?space_id=` (crée au besoin, renvoie l'état et le login, jamais la valeur), `PUT /api/me/credentials/{id}` (valeur → `jamais_teste`), `POST /api/me/credentials/{id}/test`.

- [ ] **Step 1 :** tests (critères 1 à 9 de la feature) : `test_creation_au_premier_besoin_sans_doublon`, `test_ensure_idempotent`, `test_meme_connexion_meme_identifiant_pour_deux_espaces`, `test_tester_valide_avec_droit_ecriture` (login affiché), `test_tester_jeton_rejete`, `test_tester_sans_droit_ecriture_refuse`, `test_forge_injoignable_etat_inchange`, `test_changer_le_secret_remet_jamais_teste`, `test_un_admin_ne_voit_pas_l_identifiant_d_un_autre` (404), `test_valeur_jamais_dans_reponse_ni_logs`, `test_suppression_utilisateur_supprime_identifiants`, `test_rattachement_equipe_cree_les_identifiants`.
- [ ] **Step 2 :** les lancer → échec.
- [ ] **Step 3 :** implémenter avec `CryptoService` pour le chiffrement. Le faux GitHub expose `permissions.push` selon le jeton. Front : bloc « Saisir le secret » / « Tester le secret », avec l'état et le login.
- [ ] **Step 4 :** suites vertes.
- [ ] **Step 5 :** commit `Identifiant personnel par forge : saisir, tester, état explicite`. **Livraison sur test1.**

### Task 4 : Brouillon — stockage et capture des écritures

**Files:**
- Create: `backend/src/ossie_studio/db/migrations/0027_drafts.sql`
  - `drafts(id, user_id → users CASCADE, space_id → git_spaces CASCADE, branch_id → space_branches CASCADE, base_commit text, revision int NOT NULL DEFAULT 0, updated_at, UNIQUE(user_id, branch_id))`.
  - `draft_objects(draft_id → drafts CASCADE, path text, base_blob text NULL, content bytea NULL, deleted bool, PRIMARY KEY(draft_id, path))`.
  - `draft_decisions(draft_id CASCADE, kind text` dans `('impact_verified','warning_accepted'), key text, fingerprint text, PRIMARY KEY(draft_id, kind, key))`.
- Create: `backend/src/ossie_studio/drafts/store.py`
- Test: `backend/tests/test_draft_store.py`

**Interfaces:**
- Produces: `@dataclass DraftObject(path: str, base_blob: str | None, content: bytes | None, deleted: bool)`.
- Produces dans `drafts/store.py` :
  - `async def db_get_draft(conn, user_id, branch_id) -> Draft | None` ;
  - `async def db_apply_changes(conn, user_id, space_id, branch_id, base_commit: str, changes: list[DraftChange]) -> Draft`. Pour chaque `DraftChange(path, base_blob, content | None)` : la première modification fige `base_blob`, une re-modification le garde, un contenu égal à la base retire l'objet, un brouillon vide est supprimé. Elle incrémente `revision`.
  - `async def db_list_objects(conn, draft_id) -> list[DraftObject]` ;
  - `async def db_rebase_object(conn, draft_id, path, new_base_blob, content | None) -> None` (utilisée par T7) ;
  - `async def db_delete_draft(conn, draft_id) -> None`.
- Produces: `def validate_draft_path(path: str) -> str`. Elle lève `DraftPathError` sur un chemin absolu, `..` ou `.git/`. Limite `DRAFT_OBJECT_MAX_BYTES = 20 * 1024 * 1024` → `draft_object_too_large`.

- [ ] **Step 1 :** tests `test_premiere_modif_fige_la_base`, `test_remodif_garde_la_base_d_origine`, `test_retour_au_contenu_publie_retire_l_objet`, `test_brouillon_vide_supprime`, `test_suppression_puis_recreation`, `test_chemin_hors_depot_refuse` (`../x`, `/etc/x`, `.git/config`), `test_objet_trop_gros_refuse`, `test_objet_de_5_mo_accepte`, `test_suppression_espace_ou_branche_cascade`, `test_suppression_utilisateur_cascade`.
- [ ] **Step 2 :** les lancer → échec.
- [ ] **Step 3 :** implémenter.
- [ ] **Step 4 :** tests verts.
- [ ] **Step 5 :** commit `Brouillon : stockage des objets modifiés et de leur base`.

### Task 5 : Brouillon — vue éphémère, bascule des lectures et écritures

**Files:**
- Create: `backend/src/ossie_studio/drafts/views.py`
- Modify: `backend/src/ossie_studio/spaces/workdir.py` (la copie devient copie de lecture ; ajout du worktree détaché ; `commit_to_branch`, `list_unpublished` et `publish` sont retirés à la fin de la tâche)
- Modify: les 22 points d'appel (`sources/service.py`, `sources/collect.py`, `sources/download.py`, `api/sources.py`, `api/curation.py`, `api/branches.py`)
- Modify: `backend/src/ossie_studio/app.py` (tâche périodique de purge des vues)
- Modify: `frontend/src/pages/SpaceDetailPage.tsx` (« Brouillon : N objets modifiés » remplace la liste des commits non publiés)
- Test: `backend/tests/test_draft_views.py` ; adapter `test_workdir.py`, `test_sources.py`, `test_curation.py`, `test_collect_service.py`, `test_branches.py`

**Interfaces:**
- Consumes: T4 (`db_get_draft`, `db_apply_changes`, `db_list_objects`), T2 (`require_space_capability`).
- Produces: `@dataclass BranchContext(space: GitSpace, branch_slug: str, branch_id: uuid.UUID, username: str, token: str | None, user: User)`, construit par `async def branch_context(conn, crypto, space_id, branch_id, user) -> BranchContext` (remplace `space_credentials`).
- Produces: `class DraftViews` (instance unique dans `app.state.draft_views`) :
  - `async def read(self, conn, ctx: BranchContext, read_fn: Callable[[Path], T], *, mode: Literal["merged","published"] = "merged") -> T`. Sans brouillon, ou en mode `published`, la lecture se fait sur la copie de lecture alignée ; sinon, sur la vue de l'utilisateur.
  - `async def write(self, conn, ctx: BranchContext, write_fn: Callable[[Path], None]) -> int`. Elle s'exécute dans la vue de l'utilisateur, créée si besoin, sous un verrou par (utilisateur, branche). Elle capte `git status --porcelain` plus `git rev-parse HEAD:<path>` pour la base, appelle `db_apply_changes`, et rend le nombre d'objets du brouillon.
  - `async def discard_view(self, user_id, branch_id) -> None` et `async def purge_idle(self, max_idle_seconds: int = 1800) -> int`.
- Vue : `<root>/<space_id>/<slug>.views/<user_id>`, worktree détaché. Un témoin `.ossie-view` contient `(head, draft_revision)`. Si le témoin diffère, on remet la vue sur la tête (`reset --hard`) et on réapplique le brouillon.
- Transition : au premier alignement, si la copie a des commits locaux en avance sur `origin/<ref>`, on fait `reset --hard origin/<ref>` et on écrit le log `legacy_local_commits_discarded(space_id, ref, count)`.
- Retour à une version (`POST …/versions/{n}/restore`) : il passe par `write`, en écrivant l'arbre de la version dans la vue.

- [ ] **Step 1 :** tests `test_ecriture_va_au_brouillon_sans_commit` (tête distante inchangée, tête de la copie de lecture égale à la distante), `test_collecte_ecrit_dans_le_brouillon_du_lanceur`, `test_auteur_voit_ses_modifs_un_autre_non`, `test_creer_modifier_supprimer_superposes`, `test_deux_ecritures_concurrentes_meme_utilisateur_sans_perte` (Review Focus 3), `test_ecriture_sans_capacite_sur_l_espace_403_avec_rattachement_200`, `test_restore_version_va_au_brouillon`, `test_commits_locaux_herites_jetes_et_journalises`, `test_vue_purgee_apres_inactivite_puis_reconstruite_a_l_identique`, `test_brouillon_de_500_objets_lecture` (mesure du temps rapportée dans le commit). Adapter les tests existants qui attendaient un commit local : ils attendent maintenant un objet de brouillon.
- [ ] **Step 2 :** les lancer → échec.
- [ ] **Step 3 :** implémenter `DraftViews`, puis basculer les 22 points d'appel un fichier à la fois, en gardant la suite verte à chaque fichier, et enfin retirer `commit_to_branch`, `list_unpublished` et `publish` ainsi que les routes `GET …/unpublished` et `POST …/publish`. Front : compteur du brouillon.
- [ ] **Step 4 :** pytest complet + vitest.
- [ ] **Step 5 :** commit `Brouillon : vue éphémère, toutes les écritures y vont`.

### Task 6 : Trois vues et signal de changement

**Files:**
- Create: `backend/src/ossie_studio/api/drafts.py` (préfixe `/api/spaces/{space_id}/branches/{branch_id}/draft`)
- Create: `frontend/src/components/DraftViewSwitch.tsx`, `frontend/src/draft.ts` (hooks)
- Modify: les écrans de lecture de branche (`SourcesPage.tsx`, `SourceDetailPage.tsx`, la page de curation) : paramètre `view` transmis aux routes de lecture
- Test: `backend/tests/test_draft_api.py`, `frontend/src/components/DraftViewSwitch.test.tsx`

**Interfaces:**
- Consumes: T5 (`DraftViews.read(mode=…)`).
- Produces: `GET …/draft` → `{objects: [{path, state: "created"|"modified"|"deleted", changed_upstream: bool}], changed_upstream_count, revision}`. `changed_upstream` est vrai quand le blob actuel du chemin dans la copie de lecture diffère de `base_blob` (y compris quand il a été supprimé côté publié).
- Produces: un paramètre de requête `view=published|draft|merged` sur les routes de lecture de branche. `draft` ne garde que les chemins du brouillon.
- Produces (front) : `useDraft(spaceId, branchId)` et `<DraftViewSwitch value onChange />`, choix mémorisé dans `localStorage` (protégé par try/catch).

- [ ] **Step 1 :** tests (critères 1 à 6 de la feature), dont `test_objet_publie_hors_brouillon_ne_signale_rien` et `test_suppression_cote_publie_signalee`.
- [ ] **Step 2 :** les lancer → échec.
- [ ] **Step 3 :** implémenter.
- [ ] **Step 4 :** suites vertes.
- [ ] **Step 5 :** commit `Brouillon : trois vues et signal de changement côté publié`. **Livraison sur test1.**

### Task 7 : Fenêtre de fusion par propriétés

**Files:**
- Create: `backend/src/ossie_studio/drafts/merge.py`
- Modify: `backend/src/ossie_studio/api/drafts.py` (routes de fusion)
- Create: `frontend/src/components/MergeDialog.tsx`, `frontend/src/components/PropertyDiff.tsx`
- Test: `backend/tests/test_draft_merge.py`, `frontend/src/components/MergeDialog.test.tsx`

**Interfaces:**
- Produces: `def merge3(base: object, theirs: object, mine: object) -> MergeResult`, avec `MergeResult(merged: object, conflicts: list[Conflict])` et `Conflict(pointer: str, base, theirs, mine)`. Fusion récursive par clé pour les objets ; **listes et scalaires traités comme des valeurs atomiques** ; une clé ajoutée d'un seul côté est gardée, une clé supprimée d'un côté et inchangée de l'autre est supprimée.
- Produces: `def merge_object(path: str, base: bytes | None, theirs: bytes | None, mine: bytes | None) -> ObjectMerge`, avec `ObjectMerge(kind: "auto"|"conflict"|"text", result: bytes | None, conflicts, deleted_side: "theirs"|"mine"|None)`. Les objets connus (`.yaml`, `.yml`, `.json` sous `sources/` et `governance/`) sont fusionnés par propriétés ; les autres passent par `git merge-file -p --diff3` (kind `text`).
- Routes :
  - `GET …/draft/merge?path=` → les trois versions plus le résultat proposé, en lecture seule si aucun conflit ;
  - `POST …/draft/merge` avec `{path, resolution: {pointer: "theirs"|"mine"|{"value": …}}, delete: bool | null}` → `db_rebase_object(…, new_base_blob=<blob publié actuel>)` ;
  - `POST …/draft/automerge` → applique toutes les fusions sans conflit, rend la liste des chemins encore en conflit. T10 l'appelle.

- [ ] **Step 1 :** tests sur `merge3` (propriétés différentes → auto ; même propriété → conflit avec pointeur `/properties/x/type` ; liste modifiée des deux côtés → conflit atomique ; suppression d'un côté sans modification de l'autre), puis tests d'API (critères 1 à 8 de la feature) : `test_supprime_cote_publie_modifie_brouillon_deux_options`, `test_cree_des_deux_cotes_meme_chemin`, `test_fichier_inconnu_repli_texte`, `test_apres_reconciliation_plus_signale`, `test_fermer_sans_valider_brouillon_inchange`.
- [ ] **Step 2 :** les lancer → échec.
- [ ] **Step 3 :** implémenter. Front : publié à gauche, brouillon à droite, propriétés en conflit mises en évidence, choix publié / sien / saisie libre ; repli texte en trois colonnes.
- [ ] **Step 4 :** suites vertes.
- [ ] **Step 5 :** commit `Brouillon : fusion à trois par propriétés`.

### Task 8 : Dépendances et section « Impacts »

**Files:**
- Create: `backend/src/ossie_studio/drafts/impacts.py`
- Modify: `backend/src/ossie_studio/api/drafts.py`
- Create: `frontend/src/components/ImpactsSection.tsx`
- Test: `backend/tests/test_draft_impacts.py`, `frontend/src/components/ImpactsSection.test.tsx`

**Interfaces:**
- Produces: `class DependencyRule(Protocol): name: str; def dependents(self, view: Path, changed: set[str]) -> dict[str, set[str]]` (objet dépendant → objets lus qui ont changé). Règles : `RawAssetRule` (même nom de fichier sous `sources/<slug>/assets/` ↔ `raw/`), `RefRule` (index inverse des `$ref` construit en un seul parcours). `DEPENDENCY_RULES: list[DependencyRule]` est extensible ; T12 y ajoute `GovernanceAssetsRule`.
- Produces: `async def compute_impacts(conn, views, ctx) -> list[Impact]`, avec `Impact(path, reads: list[str], rule, verified: bool)`. Un impact est « vérifié » s'il existe une décision `impact_verified` dont `fingerprint` est égal au hash des blobs actuels des objets lus.
- Routes : `GET …/draft/impacts`, `POST …/draft/impacts/verify` avec `{path}`.

- [ ] **Step 1 :** tests (critères 1 à 7 de la feature), dont `test_verifie_redevient_actif_si_l_objet_lu_change` et `test_collecte_500_objets_temps` (mesure rapportée).
- [ ] **Step 2 :** les lancer → échec.
- [ ] **Step 3 :** implémenter.
- [ ] **Step 4 :** suites vertes.
- [ ] **Step 5 :** commit `Brouillon : impacts des objets modifiés (raw → asset, $ref)`.

### Task 9 : Tests de validation et journal

**Files:**
- Create: `backend/src/ossie_studio/drafts/validation.py`
- Modify: `backend/src/ossie_studio/api/drafts.py`
- Create: `frontend/src/components/ValidationLog.tsx`
- Test: `backend/tests/test_draft_validation.py`, `frontend/src/components/ValidationLog.test.tsx`

**Interfaces:**
- Produces: `class ValidationRule(Protocol): name: str; def check(self, view: Path) -> list[Finding]`, avec `Finding(level: "error"|"warning", path, rule, message_code, args)`. Règles : `ParseableRule`, `SourceManifestRule`, `BrokenRefRule`, `OrphanAssetRule` (avertissement). `VALIDATION_RULES` est extensible ; T12 y ajoute les règles de gouvernance.
- Produces: `async def validation_log(conn, views, ctx) -> list[LoggedFinding]`, avec `LoggedFinding(Finding, preexisting: bool, accepted: bool)`. `preexisting` est vrai quand le même `(rule, path, message_code)` existe déjà dans la vue publiée ; le résultat publié est mis en cache par tête de branche.
- Produces: `def blocking(findings) -> list[LoggedFinding]`, qui rend les erreurs non préexistantes.
- Routes : `GET …/draft/validation`, `POST …/draft/validation/accept` avec `{rule, path}` (avertissements seulement).

- [ ] **Step 1 :** tests (critères 1 à 8 de la feature), dont `test_suppression_d_une_cible_ref_erreur_liee_au_brouillon` et `test_erreur_preexistante_non_bloquante`.
- [ ] **Step 2 :** les lancer → échec.
- [ ] **Step 3 :** implémenter.
- [ ] **Step 4 :** suites vertes.
- [ ] **Step 5 :** commit `Brouillon : tests de validation et journal`.

### Task 10 : Écran et procédure de publication

**Files:**
- Create: `backend/src/ossie_studio/drafts/publish.py`
- Modify: `backend/src/ossie_studio/spaces/workdir.py` (worktree temporaire de publication ; push avec un jeton donné)
- Modify: `backend/src/ossie_studio/oidc/service.py` (met à jour `display_name` à chaque connexion OIDC, pas seulement à la création)
- Modify: `backend/src/ossie_studio/api/drafts.py`
- Create: `frontend/src/pages/PublishPage.tsx` (route `/spaces/:spaceId/branches/:branchId/publish`)
- Test: `backend/tests/test_draft_publish.py`, `frontend/src/pages/PublishPage.test.tsx`

**Interfaces:**
- Consumes: T2 (`spaces:publish` sur l'espace), T3 (`resolve_personal_token`), T4/T5 (brouillon, vues), T7 (`automerge`), T8 (`compute_impacts`), T9 (`blocking`).
- Produces: `async def publish_readiness(conn, views, ctx) -> Readiness`, avec `Readiness(ok: bool, reasons: list[str])`. Raisons : `no_publish_right`, `conflicts`, `impacts_pending`, `validation_errors`, `credential_missing`, `credential_not_valid`, `publish_in_progress`, `empty_draft`.
- Produces: `async def publish_draft(conn, pool, views, ctx, comment: str) -> PublishResult`. Étapes :
  1. `pg_try_advisory_lock(hashtextextended('publish:'||branch_id, 0))` sur une connexion dédiée tenue pendant toute la procédure ; si le verrou est pris → `publish_in_progress`.
  2. Fetch et revalidation.
  3. Worktree détaché sur `origin/<ref>` → écriture des objets → commit (`GIT_AUTHOR_NAME = display_name or email`, `GIT_AUTHOR_EMAIL = email`, même valeur pour le committer). Le message est `ossie: <commentaire>`, suivi des lignes `Impact vérifié : <path>` et `Avertissement accepté : <rule> <path>`.
  4. Push `HEAD:refs/heads/<ref>` avec le jeton personnel.
  5. Si le push est refusé en non fast-forward : fetch ; si aucun objet du brouillon n'a bougé, on reconstruit et on repousse (au plus 3 fois) ; sinon `branch_changed_conflict`.
  6. En cas de succès : suppression du worktree, `db_delete_draft`, `discard_view`, alignement de la copie de lecture.
  7. `finally` : suppression du worktree, déverrouillage.
- Routes : `GET …/draft/readiness`, `POST …/draft/publish` avec `{comment}` (commentaire non vide → sinon `invalid_request` avec `comment_required`).

- [ ] **Step 1 :** tests (critères 1 à 13 de la feature), avec un dépôt nu local comme forge et le faux GitHub pour l'accès : `test_publication_un_commit_auteur_utilisateur_jeton_personnel`, une série `test_bouton_inactif_raison_<raison>` (une par raison, plus le refus de l'appel direct), `test_publish_par_rattachement_e1_pas_e2`, `test_erreur_preexistante_ne_bloque_pas`, `test_publications_concurrentes_second_publication_en_cours`, `test_objet_bouge_entre_ecran_et_clic`, `test_push_exterieur_autre_objet_reconstruit`, `test_push_exterieur_meme_objet_renvoi_fusion`, `test_processus_interrompu_verrou_relache_brouillon_intact` (exception injectée après le commit et avant le push), `test_message_contient_decisions`, `test_commentaire_vide_refuse`, `test_nom_oidc_ou_email`, `test_jeton_revoque_entre_test_et_publication` (Review Focus 4).
- [ ] **Step 2 :** les lancer → échec.
- [ ] **Step 3 :** implémenter. Front : liste des objets (clic → `MergeDialog`), impacts, journal, commentaire, bouton avec ses raisons et lien « Saisir le secret » (`CredentialBlock`).
- [ ] **Step 4 :** suites vertes.
- [ ] **Step 5 :** commit `Publication : un brouillon, un commit, sous son identité`. **Livraison sur test1** (scénario mono-utilisateur ; le jeton personnel et le dépôt de test viennent plus tard, décision de Gael).

### Task 11 : Rafraîchissement des écrans

**Files:**
- Modify: `backend/src/ossie_studio/api/branches.py` (route `GET …/branches/{branch_id}/head`), `spaces/workdir.py` (délai minimal de 10 s entre deux fetchs d'une même copie)
- Create: `frontend/src/useBranchHead.ts`
- Test: `backend/tests/test_branch_head.py`, `frontend/src/useBranchHead.test.tsx`

**Interfaces:**
- Produces: `GET …/head` → `{sha}`. Le fetch est limité à un toutes les 10 s par copie ; entre deux, on rend la valeur en cache.
- Produces: `useBranchHead(spaceId, branchId)` : `refetchInterval` de 30 s et `refetchOnWindowFocus`. Si le sha change, les requêtes de la branche sont invalidées, sauf les formulaires en cours d'édition (leur état local n'est pas réinitialisé).

- [ ] **Step 1 :** tests (critères 1 à 5 de la feature).
- [ ] **Step 2 :** les lancer → échec.
- [ ] **Step 3 :** implémenter.
- [ ] **Step 4 :** suites vertes.
- [ ] **Step 5 :** commit `Écrans : rafraîchis quand la branche publiée change`.

### Task 12 : Gouvernance dans le Git de l'espace

**Files:**
- Create: `backend/src/ossie_studio/governance/repo.py` (lecture et écriture de `governance/tags.yaml`, `governance/certifications.yaml`, `governance/classifications/<scheme_id>.yaml`, au format de `yaml_io.py`)
- Modify: `api/governance.py`, `api/classifications.py` (nouveau préfixe `/api/spaces/{space_id}/branches/{branch_id}/governance/…` ; lectures via `DraftViews.read`, écritures via `DraftViews.write`)
- Create: `backend/src/ossie_studio/db/migrations/0028_drop_governance_tables.sql`
- Modify: `drafts/impacts.py` (`GovernanceAssetsRule`), `drafts/validation.py` (unicités : niveau `short_name` par schéma, nom de schéma, id de tag, nom de certification)
- Modify: les pages Gouvernance du front, désormais dans le contexte d'un espace et d'une branche (sélecteur d'espace existant `workspace.tsx`)
- Test: `backend/tests/test_governance_*.py` (réécrits sur le dépôt), `frontend` (tests des pages adaptés)

**Interfaces:**
- Consumes: T5, T8, T9.
- Produces: `def read_governance(view: Path) -> Governance` et `def write_governance(view: Path, gov: Governance) -> None`. L'import et l'export YAML réutilisent `yaml_io.py`.

- [ ] **Step 1 :** tests (critères 1 à 8 de la feature), dont `test_deux_espaces_gouvernances_independantes`, `test_nom_court_en_double_erreur_bloquante`, `test_renommer_niveau_impacte_les_assets`, `test_tables_supprimees_application_demarre`.
- [ ] **Step 2 :** les lancer → échec.
- [ ] **Step 3 :** implémenter, puis supprimer le code base de données de la gouvernance (`governance/service.py`, `classifications_service.py` côté SQL).
- [ ] **Step 4 :** suites vertes.
- [ ] **Step 5 :** commit `Gouvernance : dans le dépôt de l'espace, tables supprimées`.

### Task 13 : Supprimer l'historique des collectes en base

**Files:**
- Create: `backend/src/ossie_studio/db/migrations/0029_drop_source_collections.sql`
- Modify: `api/sources.py` (retirer `db_record_collection` ; `GET …/{slug}/collections` lit `git log -- sources/<slug>/` sur la copie de lecture), `sources/collections_service.py` (supprimé ou réduit), `spaces/workdir.py` (`log_for_path`)
- Modify: `frontend/src/pages/SourceDetailPage.tsx` (historique : sha court, auteur, date, message)
- Test: `backend/tests/test_sources.py`

**Interfaces:**
- Produces: `async def log_for_path(self, space_id, slug, repo_url, username, token, rel: str, limit: int = 100) -> list[CommitInfo]`.

- [ ] **Step 1 :** tests `test_table_supprimee_collecte_rend_son_rapport`, `test_historique_git_du_repertoire_de_la_source`, `test_source_jamais_publiee_historique_vide`, `test_scan_files_intact`.
- [ ] **Step 2 :** les lancer → échec.
- [ ] **Step 3 :** implémenter.
- [ ] **Step 4 :** suites vertes.
- [ ] **Step 5 :** commit `Sources : l'historique des collectes vient de Git`. **Livraison sur test1.**

### Task 14 : Clôtures (avec Gael)

- [ ] Passer les tickets livrés à « En review » dans docflow, avec le sha de leur commit.
- [ ] Ticket « Clôture de l'epic — vérification d'intention » (epic Brouillon), puis clôture de l'epic Profils : **avec Gael**, jamais seul. Les écarts produisent de nouveaux tickets.
