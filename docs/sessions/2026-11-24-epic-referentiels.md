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
- **Droits** (« oui vas-y ») : capacités **« Référentiels : consulter / modifier »** (`referentiels:read` / `referentiels:write`, par espace) ; l'API de lecture publiée et les primitives MCP utilisent `referentiels:read`.
- **Éditeur de Miles et React 19** (« B, vas-y ») : `jsonjoy-builder` exige React 19 (ossie-studio est en 18.3) ; on embarque une **copie corrigée** (`frontend/vendor/jsonjoy-builder-1.0.4-89529b6-react18.tgz`, deux `Context.Provider`) et le passage en React 19 est un ticket à part : **c3e24590** (socle technique, à planifier avec Gael et l'autre agent).
- **Supprimer une valeur** (« oui, vas-y ») : bouton « Supprimer » dans le panneau ; la ligne est marquée « supprimée » jusqu'à « Enregistrer » ; « Annuler les modifications » la fait revenir.
- **Imports** (« 1. oui 2. oui, vas-y ») : une ligne du CSV (ou un élément d'un JSON tableau) = une valeur ; un JSON objet = une ligne. « Date du dernier import » : « jamais » jusqu'au ticket d'import.
- **API des référentiels en lecture seule** (Gael : « Les api sur les référentiels sont en lecture seules ») : l'import de valeurs se fait **à l'écran seulement**, par une personne connectée ; une clé d'API n'importe rien (refus 401, testé). La « web API » d'import du ticket a909dc66 est abandonnée.
- **Méthode** (Gael, 24 novembre) : « traite tous les sujets sur lesquels tu es autonome d'une traite ; à chaque passe récupère la branche dev sur ta branche ».
- On commence par la base : le format (enabler e9a87d3c), avec ce que prévoit le ticket (modèle YAML, fichiers d'import `imports/<slug>.yaml|.jslt|.exemple.<csv|json>`). Les deux modes d'import (fusion par clé, remplacement) se traiteront avec le ticket d'import.

## Tickets

| Ticket | Statut |
|---|---|
| e9a87d3c — Format d'un référentiel dans l'espace Git (enabler) | En review |
| 2c321d50 — API de lecture des référentiels publiés | En review (fait avec le MCP) |
| 829a86a7 — Page Référentiels et onglet Modèle | En review |
| fbe04555 — Onglet Valeurs | En review |
| cda9c43a — Onglet Imports | En review |
| a909dc66 — Importer des valeurs depuis un fichier (à l'écran ; API en lecture seule) | En review |
| e65b590b — Service MCP de lecture des référentiels publiés | En review (fait avec l'API) |
| 46cf70a6 — Thème sombre pour tous les éditeurs Monaco | En review |
| 8269c642 — Lier une propriété d'asset à une propriété de référentiel | En review |
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

## Notes techniques (écran Référentiels et onglet Modèle)

- Backend : `api/referentiels.py` (`/api/spaces/{s}/branches/{b}/referentiels` : liste avec « non publié » et `readonly` sur la racine, création libellé + slug figé avec un modèle de départ à clé `code`, lecture, `modele/apercu`, `PUT modele`, `historique`) ; `referentiels/model_change.py` (propriétés retirées ôtées des valeurs, puis validation ; aperçu : valeurs restant valides, propriétés retirées et nombre de valeurs touchées, problèmes, doublons de clé).
- Journal d'audit : `audit/git_history.referentiel_events` (type `referentiel`, depuis les commits publiés qui touchent `referentiels/`), comme la gouvernance.
- Frontend : `pages/ReferentielsPage.tsx` ; `referentiels/` = éditeur repris de Miles (`SchemaEditor.tsx`, `YamlEditor.tsx`, `monaco.ts`, `yaml.worker.ts`) + `modelRules.ts` (lecture YAML, clé, garde). Monaco tenu en `0.52.2` (0.53 casse monaco-yaml 5.5), chargé à la demande ; Vite `worker.format = 'es'`. **Un seul réglage de Monaco** pour toute l'application (`frontend/src/monaco/setup.ts`, avec `yaml.worker.ts`) : la visionneuse de code arrivée de `dev` (raw) et les éditeurs des référentiels le partagent (`MonacoEnvironment` et le chargeur sont globaux).
- Écart assumé avec le ticket : JSONJoy ne permet pas de marquer « CLÉ » ni de verrouiller « requis » par propriété ; la mention est dans le bandeau de la clé et une garde refuse de rendre facultative ou de supprimer une propriété de la clé (le serveur revérifie).

## Notes techniques (onglet Valeurs)

- Backend : `GET …/referentiels/{nom}/valeurs`, `POST …/valeurs/verification` (sans écrire), `PUT …/valeurs` (liste complète, brouillon, brique « format »). Un problème de valeur porte `index` (rang dans la liste vérifiée) et `field` (« paliers[1] › delai_jours ») pour désigner ligne et cellule.
- Frontend : `referentiels/ValuesTab.tsx` (toutes les valeurs chargées ; recherche, filtre, tri par clé, pagination par 50 ; marques nouvelle / modifiée / supprimée / erreur ; vérification par le serveur à chaque modification, 300 ms ; compteur d'erreurs sur l'onglet), `referentiels/ValuePanel.tsx` (formulaire construit sur le modèle : objets en sections, tableaux d'objets en sous-tableaux réordonnables, élément ouvert dans le panneau avec fil d'Ariane, listes simples ; clé figée sur une valeur existante), `referentiels/valueRules.ts`.
- L'onglet Valeurs reste monté une fois ouvert : changer d'onglet ne perd pas les modifications non enregistrées.

## Notes techniques (onglet Imports)

- Paquets : `urban-json==0.2.1`, `urban-json-editor==0.3.0` (versions de Miles). Le composant `<jslt-editor>` est servi sous `/api/jslt-editor` (`urban_json_editor.mount`), son catalogue sous `/api/jslt-functions` (`api/jslt_editor.py`).
- Moteur : `referentiels/jslt_engine.py` et `jslt_profile.py`, **repris du socle de Miles** (`miles_socle/transform/engine.py`, `profile.py`, commit `aa88631`) — toute correction est à reporter des deux côtés. Syntaxe : chemins `$.Colonne`, conversions `$.x#integer` ou `convert($.x, "integer")`.
- `referentiels/importing.py` : lecture CSV (séparateur, encodage, en-tête) et JSON, chemins détectés (forme de Miles : path, kind, description), essai (`run_template`) : erreurs rattachées à « ligne N » / « élément [i] », comparaison par la clé aux valeurs actuelles.
- Routes `…/referentiels/{nom}/imports` (`api/referentiel_imports.py`) : liste, création, renommage, suppression, `exemple` (multipart, 5 Mo), `lecture`, `apercu` (réglages non enregistrés possibles), `essai`, `template`.
- Frontend : `referentiels/ImportsTab.tsx` ; `JsltEditor.tsx`, `TemplateEditor.tsx`, `editorMetrics.ts`, `jsltKeys.ts` **repris de Miles** (avec leurs tests) ; `apiUpload` dans `apiClient.ts` pour l'envoi multipart.

## Notes techniques (importer des valeurs, a909dc66)

- `referentiels/importing.py` : `transform` (template appliqué ligne à ligne et valeurs validées, hors écriture et hors boucle d'événements via `asyncio.to_thread`), `report` (nouvelles, modifiées, inchangées, **supprimées** en remplacement, erreurs, différences), `imported_values` (fusion par clé ou remplacement ; `None` = refusé, **tout ou rien**). L'essai prend aussi le mode (en remplacement, il annonce les suppressions).
- Route `POST …/referentiels/{nom}/imports/{slug}/import` (multipart : `file` facultatif — sans fichier, le fichier d'exemple —, `mode` = `merge` | `replace`, 10 Mo au plus). Écrit `valeurs.json` dans le **brouillon** de la personne (relu au moment d'écrire). Refus : `422 import_refused` avec le compte rendu dans `error_args.report` ; fichier sans ligne : `400 import_file_empty` ; racine : `409 branch_root_readonly`. Réponse : compte rendu, `mode`, `file`, `revision` (nulle = aucun changement, rien d'écrit), `last_import`.
- Journal : `referentiels/import_journal.py`, une ligne du journal d'audit par import (type `referentiel_import`, objet `<branche>/<référentiel>/<import>`, détail : `outcome` imported | unchanged | refused, `mode`, `file`, nombres du compte rendu, `revision`). La **date du dernier import** (et son auteur) se lit dans ce journal : rien n'est écrit dans les fichiers, donc un même fichier importé deux fois ne crée pas de second changement.
- Frontend : `referentiels/ImportRunPanel.tsx` (pied du sous-onglet Template : mode, fichier d'exemple ou autre fichier, « Importer », confirmation avant remplacement, compte rendu ; attend que le template affiché soit enregistré), `referentiels/ImportReport.tsx` (compte rendu partagé par l'essai et l'import).
- **Pas de web API d'import** (décision de Gael) : la route `…/import` est celle de l'écran, réservée à une personne connectée (session) ; une clé d'API est refusée.

## Notes techniques (thème sombre Monaco, 46cf70a6)

- Thème `ossie-dark` (base `vs-dark`) déclaré **une seule fois** dans `frontend/src/monaco/setup.ts` (`MONACO_THEME`, couleurs de la maquette : fond `#1e1e1e`, numéros `#6e7681`, clés `#9cdcfe`, chaînes `#ce9178`, nombres `#b5cea8`, booléens et `null` `#569cd6`, commentaires `#6a9955`). Jetons de la coloration YAML de Monaco : clé = `type`, booléens / `null` = `keyword`.
- Appliqué à **tous** les éditeurs Monaco : YAML du modèle et visionneuse de code du raw (arrivée de `dev`, auparavant en thème clair `vs`). Un test vérifie qu'aucun éditeur ne passe ses propres couleurs.
- Cadre de l'éditeur YAML : rayon `var(--radius)` du design.

## Notes techniques (lier une propriété d'asset à un référentiel, 8269c642)

- Stockage : `<clé>.extend.yaml`, `fields.<propriété>.referentiel` = `{schema, property, group?}` ; clé mémoire `x-referentiel` ajoutée aux clés de travail humain (`sources/asset_files.HUMAN_KEYS`), donc jamais dans le `<clé>.schema.yaml`, et conservée au rafraîchissement depuis raw comme tout `x-`.
- Règles : `sources/referentiel_links.py` — `read_targets` (référentiels de la vue : chemin du schéma, clé, propriétés simples), `link_group` (un groupe en une fois ; clé composée complète obligatoire, `referentiel_link_incomplete` ; une seule liaison par propriété, `referentiel_link_taken` ; « Modifier » = `replace`, garde l'identifiant de groupe), `unlink` (tout le groupe), `link_views` (statut `ok` | `partial` | `broken`, propriétés de clé manquantes, membres du groupe ; jamais de suppression automatique).
- Routes : `PUT` / `DELETE …/sources/{slug}/curation/referentiel-link` (`api/curation_links.py`, `curation:write`, brouillon, journal `curation_referentiel_link` / `_unlink` comme l'enrichissement, refus sur la racine) ; `GET …/curation-assets/referentiels` (catalogue + `readonly` si racine). La fiche d'un asset porte `referentiel` par propriété, le résumé `referentiel_issues`.
- Agent : la lecture MCP d'un asset (`read_asset`) rend `referentiel` sur les champs liés.
- Frontend : `curation/ReferentielLink.tsx` (colonne « Référentiel » : étiquette `<référentiel> · <propriété>`, `· clé 1/2` en avertissement, « cassé » ; menu Modifier / Délier avec confirmation pour un groupe ; popup « Lier à un référentiel » : chemin du schéma, « CLÉ n/N », encadré de clé composée, même nom proposé d'abord, propriétés déjà liées écartées, « Lier » inactif avec la raison).
