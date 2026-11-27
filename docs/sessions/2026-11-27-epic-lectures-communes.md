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
| dcc52af7 | Modules par `schema_raw`, retrait de `list_modules` | prêt pour dev |
| 2c069134 | Genre `extension_raw`, retrait de `get_functions` | prêt pour dev |
| 15c43ed1 | Genre `point_entree`, retrait de `list_entry_points` et `get_module` | prêt pour dev |
| 970c90ae | Genre `profil_relation` | prêt pour dev |
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
