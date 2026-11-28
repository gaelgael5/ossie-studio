# Conventions — index des documents

Epic docflow « Index des documents et validation avant publication » (bc3ece1c),
enabler 15863091. Gael : l'index est « un outil de validation et un outil de
recherche ». Code : `backend/src/ossie_studio/doc_index/`, migration
`0054_document_index.sql`.

C'est un **cache dérivé de Git**, jamais une source de vérité (comme l'index de
l'Explorateur raw) : on peut le vider et le reconstruire sans rien perdre, sauf les
résultats de validation, qui se recalculent.

## Deux niveaux

| Table | Contenu |
|---|---|
| `doc_index_content` | **le contenu**, global par espace : une ligne par fichier et par empreinte (blob Git, la « version » des primitives). Ce que l'extraction en retient : documents, clés, références ; plus tard la validation du document seul |
| `doc_index_view` | **une vue** : la branche publiée (`user_id` NULL) ou le brouillon d'une personne sur une branche ; la tête publiée sur laquelle elle a été comparée au disque (`head`, `compared_at`) |
| `doc_index_file` | les fichiers d'une vue (chemin → contenu) et leur état : `a_valider`, `valide`, `en_erreur`, avec les problèmes des règles entre fichiers (qui dépendent de la vue) |

Le chemin fait partie de la clé du contenu : le type de document et l'identité se
lisent dans le chemin. Deux vues qui ont le même contenu pour un fichier partagent la
même ligne : il n'est indexé, et plus tard validé, qu'une fois pour tout le monde.

## Ce que l'index retient d'un fichier (`doc_index/extract.py`)

Une fonction pure de (chemin, contenu) : `index_file`. Pour chaque type de document du
registre, une reconnaissance par chemin (le pendant des emplacements de
`mcp_surface/document_types.py`) donne l'identité ; le contenu donne le nom, quelques
champs filtrables et les références sortantes.

**Clés et références** : chaînes `<espèce>:<identité>`.
- tout fichier porte `path:<chemin>` (un modèle cite ses objets par chemin) ;
- un document porte `<type de document>:<identité>`, champs d'identité dans l'ordre
  du registre séparés par `/` (`carto_objet:application/APP-01`) ;
- un objet de cartographie porte aussi `carto_objet:*/<CLÉ>`, que cite une extrémité
  de relation écrite en clé seule ;
- le modèle et les valeurs d'un référentiel portent `referentiel:<nom>`, que cite le
  fichier d'extension d'un type de cartographie lié à ce référentiel.

Références retenues aujourd'hui : extrémités d'une relation et type d'un objet, types
cibles ajoutés d'un type, référentiels d'un fichier d'extension de type, chemins d'un
modèle (objets, règles, graphique), objets rattachés à un processus BPMN, schéma d'une
extension raw, datasets cités par une relation ou une référence de domaine. Une
référence à un document du même fichier n'en est pas une.

Un fichier illisible ou que rien ne reconnaît est indexé sans document (seulement
`path:`). `INDEXER_VERSION` change quand l'extraction change : un contenu indexé par
une version plus ancienne est réindexé.

## Tenir l'index à jour (`doc_index/service.py`)

- **À l'enregistrement** : `DraftWorkdir._store`, le seul point où les services
  écrivent dans le brouillon (écran, agent, import, collecte), appelle
  `record_draft_write`. Les fichiers dont le contenu a changé prennent leur nouvelle
  empreinte dans la vue du brouillon et passent `a_valider`, comme ceux qui les citent.
  Une vue de brouillon qui n'existe pas encore (ou tirée d'une autre tête) repart de la
  vue publiée comparée sur la même tête ; sinon elle ne garde que les fichiers du
  brouillon et attend sa comparaison. Un échec de l'index est journalisé
  (`doc_index_write_failed`), jamais celui de l'écriture.
- **Comparer au disque** : `DraftWorkdir.compare_index` compare la vue publiée (révision
  `head`, `git ls-tree`) puis celle du brouillon (`head` + les objets du brouillon).
  Un fichier nouveau ou d'empreinte différente est indexé et passe `a_valider` ; un
  fichier disparu est retiré ; ceux qui les citent passent `a_valider`. Couvre les
  modifications faites hors de l'application (fusion, pull, édition à la main). Les
  contenus manquants sont lus en un seul `git cat-file --batch`.
- **Reconstruire** : `store.db_clear_space` vide l'index d'un espace ; une comparaison
  le reconstruit à l'identique.

Les passes de validation (file sérialisée) et les règles sont l'objet des autres
tickets de l'epic.
