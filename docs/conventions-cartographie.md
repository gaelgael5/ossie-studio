# Conventions — cartographie (types, objets, relations, modèles)

Epic « Cartographie — types et objets » (docflow 65d94dd0), enablers f8167ae1 (format)
et 0e088eaa (catalogue ArchiMate 3.2, règles de relation, points de vue).
On garde la **sémantique** d'ArchiMate 3.2, pas son format de fichier : tout est en
YAML dans l'espace Git, sous `cartographie/`. Code : `backend/src/ossie_studio/cartographie/`.

## Le catalogue ArchiMate (livré avec l'application)

`cartographie/archimate-3.2.yaml`, à côté du code (pas dans l'espace Git) : les
**61 éléments** d'ArchiMate 3.2 (stratégie, métier, application, technologie,
physique, motivation, mise en œuvre et migration, regroupement, lieu, jonction) et
ses **11 relations**. Pour chaque type : `kind` (`element` ou `relation`), `layer`
(couche d'un élément), `label` (libellé français, nom par défaut d'un nouveau type
de l'espace), `definition` (une phrase en français, pour la fenêtre « Ajouter un
type ») et `attributes`, les attributs propres au type, avec pour un attribut à
valeurs fermées les **valeurs ArchiMate** (stockées) et leur **libellé français**
(affiché), et `unset`, le libellé affiché quand l'attribut est absent :

```yaml
Access:
  kind: relation
  label: Accès
  definition: Un comportement lit ou écrit une information.
  attributes:
    acces:
      label: Sens de l'accès
      values: {Read: lecture, Write: écriture, ReadWrite: lecture-écriture}
      unset: non précisé
```

Quatre types ont des attributs propres ; leurs noms sont **ceux du contrat
d'`urban-archimate`** (décision de Gael du 26 novembre), qui reprend le format
d'échange de l'Open Group :

| Type | Attribut | Valeurs | Absent |
|---|---|---|---|
| Accès (`Access`) | `acces` | `Read`, `Write`, `ReadWrite` | non précisé |
| Influence (`Influence`) | `modificateur` | texte libre (`+`, `++`, `-`…) | — |
| Association (`Association`) | `dirigee` | booléen | non dirigée |
| Jonction (`Junction`) | `jonction` | `And`, `Or` | et |

L'**icône** d'un type est `cartographie/icons/<type>.svg` : le pictogramme ArchiMate
dessiné d'un trait (`currentColor`, sans couleur de couche), 24 × 24. Le catalogue
porte aussi le libellé français des couches et des **points de vue**.

Ossie est la **source unique** de ce catalogue et des règles de relation
(décision de Gael) : le composant de dessin `urban-archimate` n'en tient pas de
copie, il pose la question à Ossie par ses délégués (`allowedRelations`…).

## Règles de relation et points de vue

On ne retape pas l'annexe des relations de la spécification : on lit la table
d'**Archi** (l'outil ArchiMate de référence, co-maintenu avec The Open Group),
recopiée telle quelle dans `cartographie/archi/` avec sa licence MIT (commit et
procédure de mise à jour dans `archi/README.md`). Code : `cartographie/rules.py`.

- `allowed_relations(source, cible)` : les types de relation permis entre deux types
  ArchiMate, dans l'ordre du catalogue. Une relation prise comme extrémité vaut le
  concept `Relationship` d'Archi : d'un Objet métier vers une relation de Flux, seule
  l'association est permise. `outgoing` / `incoming` donnent toutes les cibles ou
  toutes les sources possibles d'un type.
- Entre deux **objets de l'espace** (`space.allowed_between`), un type de l'espace suit
  le type ArchiMate sur lequel il repose (`x-archimate`) ; `space.relation_types_between`
  donne les types de relation **de l'espace** que l'on peut créer entre eux (ce que
  proposent « + Relier… » et le délégué des relations permises du diagramme).
- **Points de vue** (`viewpoints()`) : les 25 points de vue d'Archi ; identifiant =
  celui d'Archi avec des tirets (`application-cooperation`), comme dans les modèles
  d'`urban-archimate`. Comme dans Archi : un point de vue sans élément (`layered`) les
  autorise tous ; le regroupement, la jonction et toutes les relations sont permis
  partout.

## Un type de l'espace

`cartographie/types/<slug>.schema.yaml` — un type ArchiMate et un nom donné par la
personne. Plusieurs types peuvent reposer sur le même type ArchiMate.

```yaml
title: Processus de vente
x-archimate: BusinessProcess
type: object
properties:
  canal:
    type: string
    enum:
    - site
    - magasin
  volume_jour:
    type: integer
```

- `title` : le libellé ; le **slug** (nom du fichier) suit la règle de l'application
  et est **figé** (renommer change `title`, pas le fichier) ;
- `x-archimate` : le type ArchiMate, **figé** ;
- `properties` / `required` : seulement les **propriétés ajoutées** (mêmes types
  que le modèle d'un référentiel : texte, date, liste de valeurs, nombre, booléen,
  objet, tableau ; noms en minuscules, chiffres et `_`) ; la base — clé, nom,
  documentation, attributs propres au type ArchiMate — vient du catalogue et n'est
  pas réécrite ; une propriété ajoutée ne peut pas porter le nom d'un attribut
  ArchiMate du type.

## Un objet, une relation

Un fichier par objet : `cartographie/objets/<slug du type>/<clé en minuscules>.yaml`.
La personne ne choisit pas le fichier : son chemin se déduit du type et de la clé,
tous deux figés, donc il ne change jamais.

```yaml
cle: PRC-PRISE-COMMANDE
type: processus-de-vente
nom: Prise de commande
documentation: |
  Du panier validé jusqu'à la commande **enregistrée et confirmée**.

  - contrôle du stock disponible ;
  - envoi de la confirmation par courriel.
proprietes:
  canal: site
  volume_jour: 160
```

Une **relation** est un objet comme les autres, avec `source` et `cible` (clés
d'objets ou de relations) :

```yaml
cle: REL-PRISE-COMMANDE-ECRIT-COMMANDE
type: acces
source: PRC-PRISE-COMMANDE
cible: OBJ-COMMANDE
nom: Écrit la commande
proprietes:
  acces: Write
```

- **clé fonctionnelle** : majuscules, chiffres et tirets, **unique dans l'espace tous
  types confondus**, **figée** ; c'est l'identifiant de l'objet partout ;
- `documentation` : du markdown (rédigé avec BlockNote) ;
- `proprietes` : attributs ArchiMate (valeurs ArchiMate, jamais le libellé
  français) et propriétés ajoutées, validés par le schéma du type ; rien d'autre
  n'est accepté ; une propriété facultative vide n'est pas écrite ;
- ordre des clés fixe (`cle`, `type`, `source`, `cible`, `nom`, `documentation`,
  `proprietes`) : deux écritures identiques donnent les mêmes octets ;
- **dates et auteurs** : tirés de l'historique Git de la branche, jamais stockés ;
  un fichier du seul brouillon est « non publié » ;
- **supprimer** un objet supprime son fichier ; refusé tant qu'une relation l'utilise.

## Signalements à la lecture

`cartographie.space.read_cartographie` lit toute la cartographie d'une branche et
signale : fichier illisible ; objet rangé hors de `cartographie/objets/<type>/`
(`object_misplaced`) ou sous le dossier d'un autre type (`object_wrong_folder`) ;
clé différente du nom du fichier (`object_key_mismatch`) ; **clé en double**
(`object_key_duplicate` — aucune écriture n'est acceptée tant que ce n'est pas
corrigé) ; extrémité de relation absente (`relation_endpoint_unknown`) ; relation
que les règles d'ArchiMate ne permettent pas entre les types de ses extrémités
(`relation_not_allowed`, avec « source -> cible : relation ») ; propriété
hors du schéma (`property_invalid`, `property_unknown`, `property_required`, avec
la propriété en cause) ; type inconnu ou mal formé.

## Écriture

Dans le brouillon de la personne (`cartographie.service.CartographieTarget`), comme les
sources, les référentiels et les modèles, sur toute branche, racine comprise ; rien
n'atteint la branche avant la publication. L'écran « Cartographie » et ses routes
(`api/cartographie.py`) passent par là. Refus nommés (`CartographieError`) :
`type_exists`, `type_not_found`, `type_archimate_frozen`, `type_invalid`,
`object_key_taken`, `object_not_found`, `object_type_frozen`, `object_invalid`,
`object_keys_duplicate`, `object_in_use`, `property_in_use`, `type_objects_invalid`,
`type_in_use`. Une relation non permise est refusée (`object_invalid`, problème
`relation_not_allowed`).

Modifier un type ne doit jamais rendre ses objets invalides : retirer une propriété
qu'ils utilisent est refusé (`property_in_use`, avec le nombre d'objets), comme toute
autre modification qui casserait leurs valeurs (`type_objects_invalid`). Un type ne
se supprime pas tant qu'il a des objets (`type_in_use`).

## Un modèle (diagramme) : trois fichiers

Epic « Cartographie — modèles et vues » (docflow 4976292f), enabler e338fe14. Code :
`cartographie/diagram.py`. Les structures sont **celles du composant `urban-archimate`**
(son `docs/contrat.md`) : Ossie relit le JSON Schema livré avec le paquet Python
(`urban_archimate.STATIC_DIR / "hydrated-model.schema.json"`) pour contrôler la forme
des trois fichiers ; le modèle stocké est le modèle hydraté sans les `data`.

- **Le modèle** — `cartographie/modeles/<slug>.modele.yaml` : `id` (clé), `nom`,
  `description`, `regles` (chemin), `graphique` (chemin), `documents` (chemins des
  objets chargés), `elements` et `relationships` (clés retenues), `vues` (`id`, `nom`,
  `point_de_vue` facultatif, `noeuds`, `connexions`). Un nœud a son propre `id` et
  porte un `element` (clé), une `note` ou un `cadre`, avec ses `enfants` ; une
  connexion porte `relationship` (clé), `de` et `vers` (identifiants de nœuds).
  **Ni coordonnée ni contenu d'objet** (décisions de Gael).
- **Le fichier de règles** — `cartographie/regles/<slug>.rules.yaml` : `lang`,
  `defaut` (facultatif), `elements` et `relationships`, des blocs rangés par `type`
  (slug d'un type de l'espace ; à défaut, un type ArchiMate) avec `name`,
  `documentation`, `properties` (`propertyDefinitionRef`, `value`) et les attributs
  ArchiMate (`accessType`, `modifier`, `isDirected`…). Une valeur est un chemin dans
  l'objet (`$.nom`, `$.proprietes.acces`) ou un littéral. **Règle d'un objet** : celle
  de son type, sinon celle de son type ArchiMate (`x-archimate`), sinon `defaut`
  (`diagram.rule_block`). Les définitions de propriété et l'identifiant ArchiMate ne
  sont pas stockés : le composant les déduit (schéma du type, clé).
- **Le fichier graphique** — `cartographie/modeles/<slug>.graphique.yaml`, un par
  modèle (son chemin est imposé) : `vues.<vue>.noeuds.<nœud>` (`x`, `y` ou `relatif`,
  `dx`, `dy` ; `l`, `h`) et `vues.<vue>.connexions.<connexion>` (`ancre_source`,
  `ancre_cible` parmi gauche/droite/haut/bas, `points`, `libelle`).

**Écriture déterministe** : clés dans l'ordre du contrat, valeurs vides omises, une
géométrie par ligne (`N1: {x: 40, y: 60, l: 320, h: 180}`), un nombre entier écrit en
flottant redevient entier. Déplacer un nœud ne change donc qu'une ligne, du seul
fichier graphique. Le graphique est réécrit dans l'ordre du modèle, sans les entrées
qui ne correspondent à rien.

**Contrôles à la lecture** (`diagram.read_diagram`), chacun avec l'identifiant en cause
(`detail`) et sa place dans le fichier (`field`) — les cinq du contrat :
`model_key_without_document` (clé retenue absente des documents),
`model_relation_end_not_retained`, `model_node_not_retained`,
`model_connection_not_retained`, `model_connection_node_unknown`,
`model_document_unused`, et `graphic_entry_unknown` (avertissement : ignorée,
jamais un refus). En plus : forme (`model_invalid`, `rules_invalid`,
`graphic_invalid`), fichier absent ou illisible (`*_missing`, `*_unreadable`),
document absent ou en double, clé répétée, élément qui est une relation (ou
l'inverse), vue, nœud ou connexion en double, point de vue inconnu, chemin des règles
ou du graphique hors de sa place ; dans les règles, type inconnu, dans la mauvaise
section ou réglé deux fois, chemin de valeur mal écrit.

**Écriture** (`save_diagram`, `save_rules`) : refus nommés `model_exists`,
`model_not_found`, `model_id_taken`, `model_slug_invalid`, `model_invalid`,
`rules_invalid` ; rien n'est écrit en cas de refus. **Hydratation** (`hydrate`) : le
modèle que reçoit le composant (règles, graphique, schémas des types présents et
objets avec leur `data`) ; un modèle incohérent est refusé (`model_invalid`, 409).

**Le composant** est servi par l'application sous `/api/archimate`
(`urban_archimate.mount`, paquet `urban-archimate==0.1.1`) : `urban-archimate.js` et
le schéma.
