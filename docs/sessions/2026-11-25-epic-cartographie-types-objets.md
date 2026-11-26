# Session du 25 novembre 2026 — Epic « Cartographie — types et objets » (développement)

Epic docflow **65d94dd0** (workspace `ossie-studio`, bloc `backlog`) : un espace
« Cartographie » où l'on crée des types d'objets reposant sur ArchiMate 3.2, puis
des objets et des relations à la main, en YAML dans l'espace Git. Suivi par l'epic
« Cartographie — modèles et vues » (4976292f), qui dépend du composant
`urban-archimate` (dépôt à part).
Branche : **`feature/cartographie-types-objets`**, partie de `dev` (`de68632`).

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui décide quand fusionner sur `dev`.

## Décisions de Gael (25 novembre)

- **Clé d'un objet** unique **tous types confondus** (« 1. oui tout type confondu ») ; le slug d'un type figé.
- **Règles de relation** : **Ossie est la source unique** (« 2. oui c'est ossie ») ; `urban-archimate` les reçoit d'Ossie.
- **Menu** : « Cartographie » devient une entrée du menu principal, avec des onglets (Types, Objets, puis Modèles) (« 3. oui »).
- Plan de l'enabler f8167ae1 validé (« oui go ») : le format du catalogue est posé ici avec les seuls types utiles aux tests ; l'enabler 0e088eaa le complète.

## Décisions de Gael (26 novembre)

- **Règles de relation** : on reprend la table d'**Archi** (version 3.2, licence MIT, notice conservée) (« 1. oui »), Gael n'ayant pas la table de la spécification sous la main.
- **Pas de ticket chez `urban-archimate`** (« 1. oui ») : son composant ne tient déjà aucune table, il demande à Ossie par le délégué `allowedRelations(cléSource, cléCible)`.
- **Menu** : « Cartographie » juste après « Modèles » (l'ancienne entrée « Domaines »), avant « Gouvernance ».
- **Branche racine** (critère 8 de e7521e80) : comme les Référentiels et les Modèles, ce que la personne fait va dans **son brouillon**, sur toute branche, racine comprise, puis se fusionne et se publie ; « + Ajouter » n'est masqué que sans le droit « Cartographie : modifier ».
- **Onglet « Objets »** : il n'apparaît qu'avec son ticket (6f71bcba), pas d'onglet vide.
- **Pictogramme** du type ArchiMate dans les vignettes et la liste de la fenêtre d'ajout (couleur de l'application).
- **Noms des attributs alignés sur le contrat d'`urban-archimate`** (« 2. oui go ») : `acces` (`Read`, `Write`, `ReadWrite` ; absent = non précisé, plus de valeur `Access`), `modificateur` (influence, au lieu de `force`), `dirigee` (association, au lieu de `orientee`). Le critère 5 de 0e088eaa (« quatre valeurs ») devient trois valeurs et l'absence.

## Tickets

| Ticket | Statut |
|---|---|
| f8167ae1 — Format des types et des objets dans l'espace Git (enabler) | En review |
| 0e088eaa — Types ArchiMate 3.2 en version de base, règles de relation (enabler) | En review |
| e7521e80 — Espace « Cartographie » : types en vignettes, filtre, ajouter un type | En review |
| 5c39d4df — Types : personnaliser un type | En review |
| 6f71bcba — Objets : créer, modifier, supprimer à la main | En review |
| b58a0a13 — Relations : relier deux objets (fiche d'une relation) | En review |
| 41bf680e — Primitives MCP de lecture des types et des objets | En review |
| 38c9d26c — Clôture (avec Gael, jamais seul) | — |

## Notes techniques (format, f8167ae1)

- Module `backend/src/ossie_studio/cartographie/` : `catalog.py` (+ `archimate-3.2.yaml`, amorce : quelques éléments métier et applicatifs, Jonction, Accès, Association, Influence, Affectation, Composition, Flux), `model.py` (format d'un type et d'un objet : `CartoType`, `CartoObject`, `check_type`, `check_object`, `properties_schema`, chemins), `space.py` (`read_cartographie` avec signalements, `save_type`, `save_object`, `delete_object`), `history.py` (`file_dates` depuis `WorkdirManager.path_log`), `service.py` (`CartographieTarget`, écriture dans le brouillon), `errors.py` (`CartographieError`, `Problem` des référentiels).
- Réutilisé des référentiels : le contrôle d'une propriété (`referentiels.model.check_property`, rendu public) et l'écrivain YAML déterministe (`dump_model`).
- Propriétés d'un objet validées par JSON Schema 2020-12 (attributs du type ArchiMate + propriétés ajoutées, `additionalProperties: false`).
- Un objet utilisé par une relation ne peut pas être supprimé (`object_in_use`), règle de l'epic.
- Conventions : `docs/conventions-cartographie.md`.
- Tests : `backend/tests/test_cartographie_format.py` (critères 1 à 11, suppression refusée, dates Git, écriture réelle dans le brouillon).

## Notes techniques (catalogue, règles, points de vue — 0e088eaa)

- Catalogue `archimate-3.2.yaml` complet : 61 éléments (dont la jonction) et 11 relations, avec couche, libellé et définition en français ; libellés des couches et des 25 points de vue. `catalog.py` : `definition`, `unset` (libellé d'un attribut absent), `icon()`, `elements(couche)`, `relations()`.
- Icônes : `cartographie/icons/<type>.svg`, 72 pictogrammes dessinés pour Ossie (pas ceux d'Archi ni d'archimate-js), un trait `currentColor`, 24 × 24.
- Règles : `cartographie/archi/` contient `relationships.xml`, `viewpoints.xml` et `LICENSE.txt` d'Archi **tels quels** (commit `9826e95b`), lus par `rules.py` avec `defusedxml`. Pas de fichier généré : mettre à jour = recopier les fichiers d'un commit plus récent. Contrôle fait le 26 novembre : la table d'archimate-js exportée par `urban-archimate` ne diffère de celle d'Archi que sur 2 couples (Réseau de distribution → Écart / Événement de mise en œuvre).
- `rules.py` : `allowed_relations`, `outgoing`, `incoming` (types ArchiMate ; une relation prise comme extrémité = concept `Relationship`), `viewpoints()` (règles d'Archi : point de vue vide = tout, regroupement, jonction et relations toujours permis).
- `space.py` : `allowed_between` et `relation_types_between` (objets de l'espace, un type suit son `x-archimate`) ; une relation non permise est signalée à la lecture et refusée à l'écriture (`relation_not_allowed`) — décision prise en latitude (« interdit : inventer une relation absente de la spécification »).
- La jonction reste **un** type `Junction` avec l'attribut `jonction` (comme la spécification et Archi) ; `urban-archimate` parle d'`AndJunction` / `OrJunction` (format d'échange) : la traduction relève du fichier de règles du modèle (epic « Cartographie — modèles et vues »).
- Tests : `backend/tests/test_cartographie_catalog.py` (critères 1 à 6, fidélité à la table d'Archi, fichiers d'Archi inattendus).

## Notes techniques (écran « Cartographie », e7521e80)

- Droits « Cartographie : consulter / modifier » (`cartographie:read`, `cartographie:write`), par espace (`authz/catalog.py`).
- Routes `api/cartographie.py`, sous `/api/spaces/{s}/branches/{b}/cartographie` : `GET /catalogue` (couches, types ArchiMate avec libellé, définition, pictogramme, attributs, relations auxquelles ils prennent part — `rules.involved_relations`, sans compter le regroupement et la jonction —, propriétés de base `model.BASE_PROPERTIES`), `GET /types` (vignettes : propriétés ajoutées, objets, état `created` / `modified` dans le brouillon), `POST /types/apercu` (fichier exact, rien n'est lu ni écrit), `POST /types` (brouillon ; refus `carto_type_exists`, `carto_type_invalid`, `invalid_request` avec `label_required`, `slug_invalid`, `archimate_unknown`).
- Liste indexée en mémoire par empreinte de `cartographie/` : l'index des Référentiels est devenu `api/list_index.py`, partagé.
- Journal : la cartographie publiée apparaît dans la page Historique (`carto_type`, `carto_object`), comme les Référentiels (`audit/git_history.py`).
- Écran : `pages/CartographiePage.tsx`, `cartographie/AddTypeDialog.tsx`, `cartographie/ArchimateIcon.tsx`. Un clic sur une vignette ouvre la fenêtre d'édition du type (ticket 5c39d4df).
- Tests : `backend/tests/test_cartographie_screen.py`, `frontend/src/pages/CartographiePage.test.tsx`, ordre du menu dans `App.test.tsx`.

## Notes techniques (personnaliser un type, 5c39d4df)

- Routes `GET`, `PUT`, `DELETE /cartographie/types/{slug}` : le document du type, son YAML, le nombre d'objets et l'usage de chaque propriété ajoutée ; l'enregistrement garde le slug et le type ArchiMate (`carto_type_archimate_frozen`) ; les clés hors format (`$defs`, `description`…) sont refusées plutôt que perdues (`invalid_request`, `schema_keys`).
- Règles dans `space.save_type` (pour tout écrivain, écran ou agent) : retirer une propriété utilisée par des objets est refusé (`carto_property_in_use`, nombre d'objets par propriété) ; **toute autre modification qui rendrait des objets invalides aussi** (`carto_type_objects_invalid`, avec les problèmes) — par exemple retirer une valeur utilisée d'une énumération, ou rendre requise une propriété que des objets n'ont pas. Décision prise en latitude, comme les Référentiels (les valeurs doivent rester valides) : on ajoute la propriété en facultatif, on remplit les objets, puis on la rend requise. Renommer une propriété utilisée revient à la retirer : refusé.
- `space.delete_type` : refusé tant que le type a des objets (`carto_type_in_use`).
- Écran `cartographie/CartoTypePage.tsx` (adresse `/cartographie/types/<slug>`, ouverte par un clic sur une vignette) : en-tête (libellé, couche en étiquette sobre, Renommer, type ArchiMate figé, fichier, nombre d'objets, retour aux types), bandeau du type ArchiMate, propriétés de base en lecture seule, éditeur visuel et YAML repris des Référentiels (`SchemaEditor`, qui accepte maintenant un contenu au-dessus de l'éditeur visuel et des actions à côté du YAML), garde de sortie sans enregistrer, Ctrl+S, suppression du type en deux temps.
- Les refus sont aussi vérifiés avant l'envoi : type ArchiMate changé dans le YAML, libellé vide, propriété utilisée retirée, clé hors format.
- Critère 1, « formulaire des objets » : le schéma des propriétés d'un objet comprend déjà les propriétés ajoutées (`model.properties_schema`) ; le formulaire lui-même arrive avec le ticket « Objets » (6f71bcba).
- Tests : `backend/tests/test_cartographie_screen.py` (partie 5c39d4df), `frontend/src/cartographie/CartoTypePage.test.tsx`.

## Notes techniques (objets, 6f71bcba)

- Routes `api/cartographie_objects.py`, sous `/cartographie/objets` : liste (toutes les fiches, relations marquées, indexée par empreinte de `cartographie/`), fiche (`schema` des propriétés, relations sortantes et entrantes avec l'objet à l'autre bout), création, modification (nom, documentation, propriétés ; clé, type, source et cible gardés), suppression (refusée tant qu'une relation l'utilise, relations nommées). Refus : `carto_object_key_taken`, `carto_object_invalid` (problèmes avec la propriété en cause), `carto_object_not_found`, `carto_object_in_use`, `carto_object_keys_duplicate`, `carto_object_type_frozen`.
- Dates et auteurs : `history.file_dates` sur les commits publiés qui touchent `cartographie/objets` (2 000 au plus) ; un objet du seul brouillon est « non publié ».
- Les problèmes de la cartographie portent toujours `field` dans l'API (`errors.problem_payload`).
- Catalogue : verbes de lecture des relations (`verbs`, « déclenche » / « déclenché par ») ; le schéma d'un attribut ArchiMate porte `x-labels` et `x-unset` (affichage des valeurs en français, ignorés par la validation). Le détail d'un type rend `object_schema` (fiche de création).
- Écran : onglet Objets (`cartographie/ObjectsTab.tsx`, adresse `/cartographie/objets`) — recherche nom ou clé, pastilles par type avec leur nombre et une pastille Relations, sélecteur de type, tri, 24 vignettes puis « Afficher 24 de plus » ; fiche (`cartographie/CartoObjectPage.tsx`, `/cartographie/objets/<clé>` et `/cartographie/objets/nouveau`) — identité (clé contrôlée à la saisie : forme et unicité), documentation rédigée dans BlockNote comme en Curation (appliquée à la fiche, enregistrée avec elle), propriétés par le formulaire des Référentiels (`SchemaFields`, exporté de `ValuePanel.tsx`) avec les facultatives vides repliées et « + Renseigner une propriété », relations en lecture seule (la ligne ouvre la fiche de la relation, le nom celle de l'objet lié), fichier et dates, suppression en deux temps, garde de sortie et Ctrl+S.
- Hors de ce ticket : « Relier… » et la fiche propre d'une relation (bande source → cible), ticket b58a0a13 ; une relation s'ouvre pour l'instant dans la fiche générique (source et cible en lecture seule).
- Tests : `backend/tests/test_cartographie_objects.py`, `frontend/src/cartographie/CartoObjects.test.tsx`.

## Notes techniques (relations, b58a0a13)

- Route `GET /cartographie/objets/{clé}/relations-permises?cible=…` : les types ArchiMate permis de la source vers la cible (objet ou relation), et les types de relation de l'espace qui reposent sur eux, avec le schéma des propriétés d'une relation de ce type. Logique dans `Cartographie.allowed` / `Cartographie.relation_types` (les fonctions `space.allowed_between` / `relation_types_between` s'y appuient).
- Fiche d'une relation : le détail porte `ends` (source et cible, nom, type, type ArchiMate) et `rule` (types ArchiMate permis entre leurs types). La création passe par `POST /cartographie/objets` avec `source` et `cible` ; une relation non permise est refusée (`relation_not_allowed`).
- Le schéma d'un attribut ArchiMate porte `x-archimate: true` : sur la fiche et dans la fenêtre, il n'est jamais replié (« le sens est demandé »).
- Écran : fenêtre « Nouvelle relation » (`cartographie/RelateDialog.tsx`) depuis la fiche d'un objet, la fiche d'une relation ou une vignette (« Relier… » au survol) — cible cherchée par nom ou clé (objets et relations), types permis seulement (les types ArchiMate permis sans type de l'espace sont nommés), clé proposée à partir des deux clés (`REL-<source sans préfixe>-<cible sans préfixe>`, suffixe si prise), nom proposé (« Accède à Commande »), attributs et propriétés, fichier créé ; après création, la fiche de la relation s'ouvre.
- Fiche d'une relation (même page que celle d'un objet) : bande source → cible (noms cliquables, type et valeur de l'attribut, « écriture → lecture » quand elle change), relations sur cette relation et « + Relier… », règle ArchiMate, suppression refusée si une autre relation l'utilise (nommée). Le retour ramène à la fiche de l'objet d'où l'on vient (état de navigation).
- Tests : `backend/tests/test_cartographie_relations.py`, `frontend/src/cartographie/CartoRelations.test.tsx`.

## Notes techniques (primitives MCP, 41bf680e)

- Trois primitives, pas plus (Gael : « n'explosons pas la liste des API »), domaine `cartographie`, capacité `cartographie:read`, lecture seule (`mcp_surface/cartographie.py`) :
  - `list_carto_types` : types de l'espace (slug, nom, type ArchiMate, couche, nature `object` ou `relation`, nombre d'objets) ; `catalogue: true` ajoute les 72 types ArchiMate 3.2 ;
  - `list_carto_objects` : filtres facultatifs et combinables `type` (slug de l'espace ou type ArchiMate), `nature`, `name` (nom ou clé, sans casse ni accents), pagination `limit` / `offset` avec `total` et `has_more` (convention de `search_referentiel_values`) ; résumés avec source et cible pour une relation ;
  - `get_carto_object` : documentation, propriétés (valeurs ArchiMate) et `property_labels` (libellés français), source et cible, relations entrantes et sortantes ; clé inconnue → `carto_object_not_found` avec la clé.
- Même lecture que l'écran (`cartographie.space.read_cartographie`) ; vue brouillon pour un agent connecté (ce qui vient du brouillon est marqué `unpublished`), publiée pour une clé d'API (le brouillon y est refusé).
- Noms en anglais comme le reste du catalogue MCP (`nature`, `name`), valeurs `object` / `relation`.
- Tests : `backend/tests/test_mcp_cartographie.py`.
