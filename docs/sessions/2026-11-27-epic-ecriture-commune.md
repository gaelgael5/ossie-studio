# Session du 27 novembre 2026 — Epic « Primitives MCP — écriture commune par genre » (développement)

Epic docflow **40783497** (workspace `ossie-studio`, bloc `backlog`) : une seule façon
d'écrire et de lire dans un espace pour un agent, quel que soit le domaine. Chaque
document porte son **genre** et son **identité** ; un registre en déduit
l'emplacement et désigne le validateur, c'est-à-dire **le service de l'écran**.
Branche : **`feature/mcp-ecriture-commune`**.

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui
> décide quand fusionner sur `dev`. Les API MCP n'avaient pas encore été testées par
> Gael : « on peut donc la modifier à souhait sans soucis ».

## Décisions de Gael

- Un processus BPMN s'identifie par **sa** clé ; une certification par son **slug** (son
  fichier porte un identifiant interne, trouvé ou créé par le service).
- Conformité au schéma XSD BPMN 2.0 vérifiée par lxml dans le service commun (écran
  compris) ; un processus écrit par l'agent est **rattaché** à un processus métier
  (l'écran garde l'import non rattaché).
- **Taille maximale d'un document écrit** : une seule limite pour tous les genres, BPMN
  compris, **200 Ko** par défaut (« On monte la limite globale à 200 Ko »), réglable
  sur la page « Paramètres ».
- `list_documents` pour les valeurs de référentiel : garder le filtre par égalité de
  `search_referentiel_values` (`filters.equals`) **et** ajouter une recherche texte.
- Import (ticket 1612cce4) : **option 1** — un seul chemin de validation, les mêmes
  codes et messages que MCP partout ; l'écran du compte rendu d'import change.
- Relation entre un dataset consultable et un non consultable (ticket b4aeae83) :
  **option 1**, on garde l'interdiction du service des domaines. Un dataset non
  consultable (classe de code, contrat, dataset manuel sans source) n'est pas
  interrogeable en SQL ; le lien passe par une `reference_dataset`.

## Tickets

| Ticket | Sujet | Statut |
|---|---|---|
| 7b4b8f3a | Registre des genres d'écriture | en revue |
| 10cc4ed6 | `write_documents` et `patch_document` | en revue |
| c7838a43 | `list_write_kinds`, `describe_standard`, renvoi `aide` | en revue |
| 69be2ef9 | `read_document`, `read_schema` par genre | en revue |
| 2113e7bc | `list_documents`, retrait des 11 lectures par domaine | en revue |
| ae8fc4b6 | Réglage de la taille maximale, page « Paramètres » | en revue |
| 1612cce4 | Le moteur d'import écrit par le registre | en revue |
| 62fbfe11 | Retrait des 13 écritures de la gouvernance | en revue |
| 56f3784a | Genres en lecture seule `schema_raw` / `schema_asset` | en revue |
| 71347227 | Genres des règles (tags, classification, tri) | en revue |
| b4aeae83 | Genres du modèle sémantique (domaine, dataset, relation, référence) | en revue |
| 92841aaa | Vérification d'intention (avec Gael) | à faire |

## Ce qu'un agent a maintenant

Catalogue : **38 primitives** (65 avant l'epic). Pour les documents de l'espace, six
primitives communes (domaine `documents`) :

- `list_write_kinds` : les genres, leur identité, leur standard, leur syntaxe de
  modification, leurs champs figés, leurs filtres de liste, la taille maximale ;
- `describe_standard` : le mode d'emploi de chaque genre et des syntaxes (JSONPath,
  XPath), les règles communes, le format des erreurs ; le catalogue ArchiMate avec
  `carto-type` ;
- `list_documents` / `read_document` : lister et lire par genre et identité ;
- `write_documents` (lot en tout ou rien, ordre libre) / `patch_document` (JSONPath ou
  XPath) ;
- `read_schema` se désigne par genre et type (`carto_objet` + `type`,
  `referentiel_valeur` + `referentiel`).

Genres : `carto_type`, `carto_objet`, `carto_modele`, `carto_graphique`,
`carto_regles`, `processus`, `referentiel_modele`, `referentiel_valeur`, `tag`,
`classification`, `certification`, `regle_tag`, `regle_classification`, `regles_tri`,
`domaine`, `dataset`, `relation_dataset`, `reference_dataset` ; en lecture seule,
`schema_raw` et `schema_asset` (le refus d'écrire nomme l'action qui les produit).

## Notes techniques

- `mcp_surface/kinds.py` : le registre (`WriteKind`) ; `kind_stores.py` : les
  validateurs (service de l'écran) ; `kind_lists.py` : la liste de chaque genre ;
  `kind_imports.py` : l'import par le registre ; `write_errors.py` : la traduction des
  refus (code, JSONPath, message, `aide`) ; `standards/*.md` : les modes d'emploi
  (exemples vérifiés par les tests).
- **Un seul chemin de validation** : les validateurs des genres alimentés par l'import
  (`carto_objet`, `referentiel_valeur`) ont une méthode `check` (lot, sans écrire) ;
  `write_documents` et le moteur d'import l'appellent tous les deux.
- Profils agents : migrations `0042`, `0045`, `0046` (une lecture retirée →
  `list_documents` et `read_document`) et `0043`, `0046` (une écriture retirée est
  nettoyée, sans recevoir l'écriture commune à la place). Page « Paramètres » : table
  `app_settings` (`0044`), capacités `settings:read` / `settings:write`.
- Modèle sémantique : `kind_domains.py` ; le contenu d'un dataset sépare la part
  Ossie 0.1.1 (`ossie`, validée contre le schéma officiel) et la part extension ;
  le service des domaines (`write_domain`) répartit entre les trois fichiers.
- L'API HTTP des référentiels accorde son droit aux clés d'API d'après
  `list_documents` / `read_document` / `read_schema` (comme `read_schema` au ticket
  06ef8679).
