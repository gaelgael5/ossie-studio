# Session du 27 novembre 2026 — Epic « Primitives MCP — lectures communes » (développement)

Epic docflow **8579c565** (workspace `ossie-studio`, bloc `backlog`) : finir d'aligner
les lectures des agents sur `list_documents` / `read_document`, comme l'epic
« écriture et lecture communes par genre » (40783497) l'a fait pour la cartographie,
les référentiels, la gouvernance, les règles, les domaines et les schémas.

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui
> décide quand fusionner sur `dev`.

## Décisions de Gael

- Après la revue des 21 primitives de lecture restantes : « On passe sur les api de
  lecture », puis « 1. toutes 2. oui 3. nouvel epic » — toutes les lectures qui lisent
  des fichiers de l'espace deviennent des genres ; un genre pour les profils de
  relations ; un nouvel epic.
- Choix validés (« 1. oui ») : les modules passent par le genre `schema_raw` (un module
  est déjà un schéma raw) ; `get_module` est remplacé par quatre lectures communes ;
  `profil_relation` est lisible **et** écrivable, comme à l'écran.
- Le service commun des sources (1920df67) attend la fin des tests de Gael : l'autre
  agent corrige `api/sources.py` pendant ces tests.

## Tickets

| Ticket | Sujet | Statut |
|---|---|---|
| 88fd2cc5 | Genre `source` en lecture seule, retrait de `list_sources` | en revue |
| e806a6b4 | Genre `contrat` en lecture seule, retrait de `list_contracts` | en revue |
| dcc52af7 | Modules par `schema_raw`, retrait de `list_modules` | en revue |
| 2c069134 | Genre `extension_raw`, retrait de `get_functions` | en revue |
| 15c43ed1 | Genre `point_entree`, retrait de `list_entry_points` et `get_module` | en revue |
| 970c90ae | Genre `profil_relation` | en revue |
| 2459ff40 | Vérification d'intention (avec Gael) | à faire |

Catalogue : 38 primitives sur `dev`, **32 visées**.

## Notes techniques

- `kind_stores.ReadOnlyStore` (ex-`ReadOnlySchemaStore`) sert tous les genres en lecture
  seule ; un fichier illisible est rendu en texte.
- `Item.changes_under` : un répertoire dont `list_documents` compte les fichiers non
  publiés (`unpublished_changes`), comme le faisait `list_sources`.
- Migration `0047` : un profil agent qui avait `list_sources` reçoit `list_documents` et
  `read_document`.
- Genre `contrat` : l'emplacement vient de l'index `contrats.yaml` de la source ;
  la version déposée est dans l'identité, `version` reste celle du document. Migration
  `0048` (`list_contracts`).
- Modules par `schema_raw` : la cartographie applicative d'une source est calculée par la
  lecture de raw de l'Explorateur (`collect_source_entries` → `application_view`), sans
  l'index en base (un lister du registre n'a pas de connexion). Filtres `kind` et
  `module` (avec `identity.source`). Migration `0049` (`list_modules`).
- Genre `extension_raw` : même identité que `schema_raw` ; le système calcule le
  `<clé>.extend.yaml`. Plus de troncature (la lecture rend tout). Migration `0050`.
- Genre `point_entree` : identité `{source, owner, method, endpoint}` — `owner` le
  schéma qui le porte, `method` `*` quand la classe elle-même est le point d'entrée,
  `endpoint` « VERBE /route » pour HTTP, sinon la nature (`task`, `message`) ; jamais
  de chemin, et le registre refuse les valeurs vides. `get_module` = quatre lectures
  communes (expliquées dans le standard `point-entree`). `mcp_surface/application.py`
  supprimé. Migration `0051`. Catalogue : **32 primitives**.
- Genre `profil_relation` : écrivable, validé par `parse_profile` (la méthode de l'écran) ;
  le refus pointe la règle fautive, retrouvée par le même service. Le profil fourni
  `ossie-datavault` existe sans fichier (version de son contenu) ; l'écrire le
  personnalise, supprimer la personnalisation le rétablit, le supprimer lui est refusé.
