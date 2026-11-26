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
| 5c39d4df — Types : personnaliser un type | Prêt pour dev |
| 6f71bcba — Objets : créer, modifier, supprimer à la main | Prêt pour dev |
| b58a0a13 — Relations : relier deux objets (fiche d'une relation) | Prêt pour dev |
| 41bf680e — Primitives MCP de lecture des types et des objets | Prêt pour dev |
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
- Écran : `pages/CartographiePage.tsx`, `cartographie/AddTypeDialog.tsx`, `cartographie/ArchimateIcon.tsx`. Un clic sur une vignette ne fait encore rien (fenêtre d'édition : ticket 5c39d4df).
- Tests : `backend/tests/test_cartographie_screen.py`, `frontend/src/pages/CartographiePage.test.tsx`, ordre du menu dans `App.test.tsx`.
