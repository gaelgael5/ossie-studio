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

## La file de validation (enabler 6b171b74)

Gael : « on ne peut pas lancer des processus de validation en parallèle, et on ne
peut pas lancer plusieurs processus de validation de différents utilisateurs en même
temps. Il faut plutôt trouver un moyen de les sérialiser ». Code :
`doc_index/queue.py`, `doc_index/passes.py`, migration `0055_validation_queue.sql`.

- **Une table** `validation_requests` : une ligne par demande de passe sur une vue.
  Elle survit à un redémarrage. Deux index la tiennent : une seule demande **en
  attente** par vue (déduplication), une seule passe **en cours** pour toute
  l'application, même avec plusieurs processus serveur.
- **Le travailleur** (`passes.Validation.forever`, lancé au démarrage si
  `validation_worker` est vrai, coupé dans les tests) prend la plus ancienne demande,
  fait la passe, la retire, et recommence. Une passe muette depuis plus de 5 minutes
  (serveur arrêté pendant la passe) est remise en attente.
- **Une passe** : comparer la vue au disque ; valider les fichiers `a_valider`, le
  document seul d'abord (réutilisé pour la même empreinte et la même version des
  règles, `RULES_VERSION`), puis les règles entre fichiers sur la vue (aujourd'hui les
  règles de la publication, `drafts.validation`, gouvernance et domaines compris) ;
  poser `valide` ou `en_erreur` avec les problèmes ; publier sa progression.
- **Échec** : la file est libérée, la vue marquée non validée
  (`doc_index_view.validated_at` NULL, `validation_error`), une ligne
  `validation_pass` part au journal d'audit (auteur « system »).

| Déclencheur | Vue |
|---|---|
| fin d'un import (référentiel, cartographie), d'une collecte, d'un `write_documents` de plusieurs documents | le brouillon de la personne |
| page de publication (`GET …/draft/validation`) | le brouillon de la personne |
| toutes les N minutes (`validation_interval_minutes`, 10 par défaut, page « Paramètres ») | les vues qui ont des fichiers `a_valider` |
| publication | la branche publiée |

Un enregistrement isolé ne lance pas de passe : il marque le fichier `a_valider`.

**État** : `GET …/draft/validation/file` rend la passe en cours (vue, fichiers faits
sur total), le nombre de demandes en attente, la position du brouillon de la personne
et de la branche publiée, et leur état dans l'index (fichiers, à valider, en erreur,
dernière passe réussie).

## Règles de validation par type de document, et règle d'or (ticket d7139b17)

Gael : « La validation dépend en premier du type de document. On valide le document,
les références et les règles entre fichiers. » Code : `doc_index/rules.py`, déclarations
dans le registre (`mcp_surface/document_types.py`, champ `validation`).

- **Déclaration** : chaque type de document liste ses règles (`RuleDeclaration` : nom,
  portée `document` / `references` / `cross_file`, niveau `error` bloquant ou
  `warning` informatif). Ajouter une règle revient à la déclarer ici, sans toucher à
  la publication.
- **Niveau** : le niveau déclaré est le plus haut que la règle peut donner pour ce
  type. Un fichier raw (`sources/<source>/raw/…`) ne bloque jamais, qu'il ait un type
  ou non. Un fichier hors registre (déclaration d'import, extension d'un type…) garde
  le niveau de la règle.
- **Document seul** : fichier lisible (`parseable`, celui d'avant, appelé fichier par
  fichier), description d'un dataset (`dataset_description`). Réutilisé pour la même
  empreinte et la même version des règles (`RULES_VERSION`).
- **Références** (`references`) : chaque identité citée existe dans la vue (clés de
  l'index). Une relation qui cite un objet supprimé est en erreur, avec un lien vers
  la relation.
- **Entre fichiers** : les règles d'avant, mêmes constats (`source_manifest`,
  `broken_ref`, `orphan_asset`, `governance`, `domain`), plus la cartographie (`carto` :
  schéma du type, relation permise par les règles effectives, clés…) et les
  référentiels (`referentiel` : modèle, valeurs).

| Type de document | Règles |
|---|---|
| tous les types écrits | `parseable` ; `references` là où ils citent |
| `carto_type`, `carto_objet` | `carto` |
| `dataset` | `dataset_description`, `domain` |
| `domaine`, `relation_dataset`, `reference_dataset` | `domain` |
| `tag`, `classification`, `certification`, `regle_tag`, `regle_classification` | `governance` |
| `source`, `regles_tri` | `source_manifest` |
| `referentiel_modele`, `referentiel_valeur` | `referentiel` |
| `schema_asset` | `broken_ref` (bloquant), `orphan_asset` (informatif) |
| `schema_raw`, `extension_raw`, `point_entree` | informatif seulement |

**Règle d'or** (Gael) : « tu ne peux pousser que des choses valides et conformes ».
- Le journal de validation (page de publication, `get_validation_log`, conditions de
  publication) se lit dans l'index, après une passe à jour sur la vue de la personne
  (`Validation.ensure`, par la file).
- Une erreur bloque, qu'elle soit dans le brouillon ou non, qu'elle ait existé avant
  ou non (`validation_errors`). Rien ne se publie tant que la vue a des fichiers à
  valider ou n'a pas été comparée (`validation_pending`). Un avertissement ne bloque
  jamais.
- Supprimés : l'erreur « préexistante » qui ne bloquait pas, l'acceptation d'un
  avertissement (route, primitive `accept_validation_warning`, ligne du commit ;
  migration `0056`).

## Page de publication et état de validation (ticket 48618517)

Gael : « Quand on arrive sur la page de publication […] tu regardes en asynchrone les
fichiers qui nécessitent d'être revalidés et tu le fais. Puis tu checks que tous les
fichiers présents sur le disque sont bien dans l'index et validés. Tout ça en
asynchrone. Le bouton publier ne s'active que quand tout est validé. »

- **Lecture sans attente** : `doc_index/state.py` (`view_state`) lit l'état de la vue
  dans l'index : chaque fichier (document, état dans le brouillon, statut `valid` /
  `error` / `warning` / `to_validate` / `deleted`, « nouveau sur le disque », problèmes),
  les comptes par filtre, la comparaison disque / index, la dernière passe complète
  (date, déclencheur, échec) et la place de la vue dans la file.
- **Routes** : `POST …/draft/validation/passes` demande une passe (ouverture de la
  page) ; `GET …/draft/validation/state?filter=&q=&limit=` lit l'état. Filtres : `all`,
  `problems`, `errors`, `warnings`, `to_validate`, `mine`. La page relit l'état toutes
  les 1,5 s tant que la passe de la vue est dans la file.
- **« Déjà en erreur dans la version publiée »** : le même problème (règle, code,
  arguments), au même chemin, dans la vue publiée validée. Il bloque quand même.
- **Comparaison** : `doc_index_view.disk_files` (fichiers sur le disque) et `detected`
  (fichiers que la dernière comparaison a trouvés nouveaux ou changés : modifiés hors
  de l'application ; vide au premier remplissage) ; `validated_trigger` : déclencheur
  de la dernière passe complète (migration `0057`).
- **Conditions de publication** (`publish_readiness`) : lues dans le même état, sans
  attendre. `validation_errors` (une erreur, n'importe où dans la vue),
  `validation_pending` (vue jamais comparée, fichiers à valider, ou passe dans la file),
  `validation_failed` (la dernière passe a échoué). La réponse porte aussi
  `validation` : erreurs, dont hors brouillon, fichiers à valider, place dans la file.
- **Publier** : le serveur fait une passe à jour (`Validation.ensure`) après la fusion
  automatique, puis relit les conditions ; il ne se fie pas à l'écran.
- **Agents** : `get_publish_readiness` rend la même réponse que la page, au même moment.
  `get_validation_log` demande une passe sans l'attendre et rend le même état que la
  page (arguments `filter`, `search`, `limit`) ; on le rappelle tant que `queue.state`
  est posé.
- `GET …/draft/validation` (journal après une passe à jour, qui attend) reste pour les
  usages qui veulent un verdict final ; l'écran ne l'appelle plus.

## La recherche passe par l'index (ticket 06171fd3)

Gael : l'index est « un outil de validation **et un outil de recherche** ».

- **Lecture** : `doc_index/search.py` — `snapshot` (les documents de quelques types dans
  une vue, avec leur fichier et sa version), `citing` (les fichiers qui citent une
  identité : recherche inverse sur les références sortantes), `holding` (ceux qui la
  portent), `find` (identité partielle).
- **Fraîcheur** : `DraftWorkdir.indexed_view` rend la vue lue (publiée, ou brouillon de
  la personne s'il existe) et la compare de nouveau à Git seulement si la tête de la
  branche a bougé. Les écritures du brouillon mettent l'index à jour à l'enregistrement,
  avant toute validation : la recherche est à jour même quand la validation ne l'est pas.
- **Cartographie dans l'index** : un type et un objet sont lus par le service de la
  cartographie (`parse_type`, `parse_object`), aux chemins que lit `read_cartographie` ;
  leurs champs gardent `readable` et ce qu'il en lit (`INDEXER_VERSION` 2).
  `cartographie/indexed.py` les range comme `Cartographie` (fichier illisible ignoré,
  première occurrence d'un identifiant, clé seule résolue si un seul type la porte).
- **list_documents** : un type de document qui déclare `index_lister` (registre) est
  listé par l'index, sans parcourir le dépôt. Aujourd'hui : `carto_type`, `carto_objet`.
  Les autres gardent leur `lister`.
- **Qui utilise** : supprimer un objet de cartographie visé par une relation est refusé
  avec les relations tirées de l'index (`users_from_index`) ; la suppression revérifie
  sur les fichiers.
- **Identité seule** : `read_document` complète une identité partielle (une clé de
  cartographie sans son type) par l'index ; plusieurs candidats : `identity_ambiguous`.
  Le contenu vient toujours du fichier.
- **Écrans** : les listes des types et des objets de cartographie lisent l'index. Pour un
  objet, l'index garde aussi ce que la liste en montre (extrait de la documentation,
  premières propriétés : `cartographie/summary.py`, `INDEXER_VERSION` 3) ; la liste des
  objets garde son cache par empreinte pour les dates de publication (historique Git).
- **Index corrigé** : la liste des documents d'un contenu est rangée telle quelle en
  `jsonb` (elle était encodée deux fois, illisible pour les requêtes) ; un contenu
  inchangé mais indexé par une version plus ancienne de l'extraction est réindexé sur
  place à la comparaison suivante (sans être compté comme nouveau ni à revalider).

## Recherche par l'index (1/6) : schémas raw, assets, extensions, points d'entrée (ticket 4618ee38)

Gael : « tout mettre dans l'index des documents », pour qu'il n'y ait qu'un seul index à
terme (l'Explorateur raw y passera dans un ticket à part, et la table
`raw_explorer_index` disparaîtra).

- **Faits d'un fichier** (`sources/raw_facts.py`, `file_facts`, colonne
  `doc_index_content.facts`, migration `0058`) : pour un fichier de
  `sources/<source>/`, ce que l'Explorateur et la curation en lisent, calculé par leurs
  propres fonctions. Rôles : `source_manifest`, `raw_schema` (résumé `_summarize` et
  ce que lisent la rétention et les règles de tri), `raw_view` (ancres), `raw_extend`
  (compteurs, synthèse, points d'entrée seulement), `tech_manifest`, `docs`,
  `contracts`, `asset_schema`. Jamais de contenu entier. `INDEXER_VERSION` 4.
- **Une seule règle, deux sources de faits** : `collect_source_entries` passe par
  `build_source_entries` (`RawLayer`), `list_curation` par `curation_objects`, et
  `module_detail` accepte des extensions déjà lues. `IndexedSources` /
  `IndexedSource` reconstruisent une source depuis l'index pour ces mêmes fonctions.
- **Listes** : `schema_raw`, `schema_asset`, `extension_raw` et `point_entree` sont
  listés par l'index (`index_facts: "sources/"` dans le registre). Les listes de
  `mcp_surface/document_type_lists.py` passent par un fournisseur de sources
  (`_FileSources` ou `_IndexSources`) : le même code de liste pour les deux.
- `read_document` d'un point d'entrée lit toujours les fichiers.

## Recherche par l'index (2/6) : modèles et valeurs des référentiels (ticket c854ad1e)

Gael : « oui » à garder les **valeurs entières** dans l'index (la liste des valeurs rend
chaque valeur en entier ; sans elles, il faudrait relire `valeurs.json`).

- **Faits** (`referentiels/indexed.py`, `file_facts`) : le modèle lu par `load_model`
  (`referentiel_model`), les valeurs lues par `load_values` (`referentiel_values`), et
  pour chaque déclaration d'import `imports/<slug>.yaml` son refus éventuel
  (`referentiel_import` : une déclaration illisible rend le référentiel illisible).
  La clé d'une valeur dépend du modèle, un autre fichier : elle se calcule à la
  lecture. `INDEXER_VERSION` 5.
- **Référentiels reconstruits** : `IndexedReferentiels.read(name)` lève exactement
  comme `read_referentiel` (mêmes codes, même ordre : modèle, valeurs, imports).
- `referentiels/published.py` sépare la lecture du calcul (`readable`,
  `summaries_of`, `search_in`) : les fichiers et l'index passent par les mêmes
  fonctions.
- **Listes** : `referentiel_modele` et `referentiel_valeur` déclarent `index_lister` et
  `index_facts` (`referentiels/`). Coût : une copie des valeurs en base par version de
  `valeurs.json` (les versions qu'aucune vue ne lit sont purgées).

## Recherche par l'index (3/6) : domaines, datasets, relations, références (ticket fc5bb3b4)

- **Faits** (`domains/indexed.py`) : pour chaque fichier d'un domaine (`<nom>.extend.yaml`,
  `<nom>.ossie.yaml`, `<nom>.virtuel.ossie.yaml`), le document lu, ou le motif de
  l'échec (`domain_file`). Les listes montrent presque tout le contenu d'un domaine :
  même choix que pour les valeurs d'un référentiel (contenus entiers, Gael : « oui »).
- **Une seule règle** : `read_domain` passe par `domain_from(name, load)` et
  `list_domains` par `domains_of` ; `IndexedDomains` les nourrit depuis l'index.
- Listes `domaine`, `dataset`, `relation_dataset`, `reference_dataset` par l'index
  (`index_facts` : `domaines/`). `INDEXER_VERSION` 6.

## Recherche par l'index (4/6) : sources, contrats, règles de tri (ticket 3492625e)

- **Faits** (`sources/raw_facts.py`) : le manifeste lu strictement (`manifest` : champs,
  ou motif du refus), l'index des contrats déposés (`contracts_index`) et l'empreinte
  de chaque fichier de `contrats/` (`contract_checksum`) ; le manifeste technique
  OpenAPI de `raw/` était déjà gardé (`tech_manifest`).
- `IndexedSource.entry()` (comme `read_sources_tree`), `contracts_view()` (comme
  `read_contracts_view`) et `tri_rules_raw()` (comme `read_tri_rules_raw`).
- Listes `source`, `contrat`, `regles_tri` par l'index, à travers le même fournisseur
  que le 1/6. `INDEXER_VERSION` 7.

## Recherche par l'index (5/6) : tags, classifications, certifications, règles, profils (ticket bb279dd6)

- **Faits** (`governance/files.py`, `file_facts`) : le **texte** des fichiers de
  gouvernance que les listes lisent (`governance_text`, None s'il n'est pas en UTF-8) :
  les `.yaml` de `tags/`, `certifications/`, `classifications/`, `tag-rules/`,
  `classification-rules/`, `relation-profiles/`, et l'état des règles fournies
  (`tag-rules-builtin.yaml`, `classification-rules-builtin.yaml`). Des fichiers de
  configuration courts. Les retraits de classification (`classification-removals.yaml`,
  qui grossissent avec l'usage) ne sont pas gardés : aucune liste ne les lit.
- **Une seule façon de lire** : chaque lecteur (`tags_from`, `certs_from`, `schemes_from`,
  `tag_rules_from`, `classification_rules_from`, `profile_files_from`,
  `builtin_disabled_from`) lit par un accès `GovernanceFiles` : `DiskFiles` (la copie)
  ou `IndexFiles` (l'index : mêmes fichiers, même ordre, mêmes erreurs). Les
  `read_*(root)` restent, en passant par `DiskFiles`.
- Les règles et le profil fournis par Ossie sont ajoutés par le même code, au même rang
  (`_rule_items`, `_relation_profile_items`) ; l'état d'une règle native se calcule
  depuis la liste des désactivées (`_RuleStore.native_with`).
- Listes `tag`, `classification`, `certification`, `regle_tag`, `regle_classification`,
  `profil_relation` par l'index (`index_facts` : `governance/`). `INDEXER_VERSION` 8.
