# Conventions — cartographie (types, objets, relations)

Epic « Cartographie — types et objets » (docflow 65d94dd0), enabler f8167ae1.
On garde la **sémantique** d'ArchiMate 3.2, pas son format de fichier : tout est en
YAML dans l'espace Git, sous `cartographie/`. Code : `backend/src/ossie_studio/cartographie/`.

## Le catalogue ArchiMate (livré avec l'application)

`cartographie/archimate-3.2.yaml`, à côté du code (pas dans l'espace Git). Pour
chaque type ArchiMate : `kind` (`element` ou `relation`), `layer`, `label`
(libellé français, nom par défaut d'un nouveau type de l'espace) et `attributes`,
les attributs propres au type, avec pour un attribut à valeurs fermées les
**valeurs ArchiMate** (stockées) et leur **libellé français** (affiché) :

```yaml
Access:
  kind: relation
  label: Accès
  attributes:
    acces:
      label: Sens de l'accès
      values: {Access: non précisé, Read: lecture, Write: écriture, ReadWrite: lecture-écriture}
```

Ossie est la **source unique** de ce catalogue et des règles de relation
(décision de Gael) : le composant de dessin `urban-archimate` les reçoit d'Ossie.
L'enabler 0e088eaa complète le catalogue (tous les éléments et relations
d'ArchiMate 3.2, définitions, icônes, règles de relation publiées en JSON).

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
corrigé) ; extrémité de relation absente (`relation_endpoint_unknown`) ; propriété
hors du schéma (`property_invalid`, `property_unknown`, `property_required`, avec
la propriété en cause) ; type inconnu ou mal formé.

## Écriture

Dans le brouillon de la personne, par commit local (`cartographie.service.CartographieTarget`),
comme les sources et les référentiels. Refus nommés (`CartographieError`) :
`type_exists`, `type_not_found`, `type_archimate_frozen`, `type_invalid`,
`object_key_taken`, `object_not_found`, `object_type_frozen`, `object_invalid`,
`object_keys_duplicate`, `object_in_use`.
