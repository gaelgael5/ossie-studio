# Session du 26 novembre 2026 — Epic « Cartographie — processus BPMN » (développement)

Epic docflow **f97e7afe** (workspace `ossie-studio`, bloc `backlog`) : décrire en BPMN
le détail d'un processus métier, en rattachant ses participants, couloirs et données aux
objets de la cartographie. Composant d'édition : `urban-bpmn` (dépôt et backlog à part,
tous ses tickets livrés).

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui décide quand fusionner sur `dev`.

## Décisions de Gael (26 novembre)

- On travaille le backlog d'**Ossie** ; tickets 1 et 2 sans attendre le composant.
- **Publication d'`urban-bpmn`** : Gael publie (l'étiquette `v.0.1.1` portait un point en
  trop : la publication automatique comparait « .0.1.1 » à « 0.1.1 » et s'arrêtait).
- Vérification d'un fichier côté serveur : **de structure** (XML lisible, BPMN 2.0,
  dessin, références) ; le message détaillé de bpmn-moddle vient du composant à
  l'ouverture (bpmn-moddle n'existe qu'en JavaScript).

## Tickets

| Ticket | Sujet | Statut |
|---|---|---|
| 1d35290d | Format et rangement d'un processus BPMN | en revue |
| 7e061c3e | Processus : liste, création, import | en revue |
| 48335b93 | Ouvrir et enregistrer un processus | en revue |
| 883ca50f | Vérification d'intention (avec Gael) | à faire |

## Notes techniques (format, 1d35290d)

- `cartographie/process.py`. Lecture `defusedxml` ; écriture par retouches ciblées du
  texte (pas de réécriture par ElementTree, qui perdrait préfixes et commentaires ; pas de
  `lxml` dans le projet).
- Balise de l'extension : celle du contrat d'urban-bpmn (`urban:reference`, `src/moddle/
  urban.json`), et non `urb:ref` / `urb:detailDe` de l'exemple du ticket (latitude :
  « à condition qu'ils soient ceux du contrat »). Sur `bpmn:process`, la même balise dit
  le processus métier détaillé.
- Les noms à jour sont appliqués par le composant (`LoadOptions.references`) : Ossie ne
  réécrit pas le XML à l'ouverture.
- À l'import, l'`id` du document devient la clé (seule retouche) : ticket 1 (« la clé est
  aussi l'id du bpmn:definitions ») et ticket 2 (« gardé tel quel ») concilient ainsi.

## Notes techniques (écran Processus, 7e061c3e)

- D'abord un quatrième onglet, l'écran Processus est devenu une entrée du menu
  « Cartographie ▾ » après la fusion de `dev` (ticket 1bea81d8 : un écran par entrée).
- Détail des routes et de l'écran : `docs/conventions-cartographie.md`, « L'écran
  Processus ».
- Écarts au ticket (validés par Gael le 26 novembre : 1 et 3 ; 2 expliqué) :
  - « Lecture seule à la racine » remplacé par la règle des droits : sans
    `cartographie:write`, ni création ni import ni rattachement (la racine se modifie par
    le brouillon, comme les modèles).
  - La mention « Importé · non rattaché » de la maquette s'affiche « Non rattaché à un
    processus métier » : le fichier ne garde pas trace de son origine.
- L'aperçu est calculé côté serveur à partir de la section `bpmndi` (pas de rendu
  bpmn-js dans la liste) ; les libellés des tâches passent à la ligne, coupés par « … »
  en dernier recours.
- La page d'un processus montrait son aperçu ; l'éditeur urban-bpmn l'a remplacé avec
  48335b93.

## Notes techniques (éditeur, 48335b93)

- `urban-bpmn==0.1.1` monté sous `/api/bpmn` ; `urban-archimate` passé en 0.1.3 le même
  jour (panneaux masquables et redimensionnables, zoom).
- Détail : `docs/conventions-cartographie.md`, « L'éditeur d'un processus ».
- Le XML réellement rendu par le composant après deux poses est gardé en fixture
  (`tests/fixtures/bpmn/urban-bpmn-enregistre.bpmn`) et s'enregistre sans problème.
- Le filigrane bpmn.io (licence) est gardé ; l'éditeur est isolé (`isolation: isolate`)
  pour qu'il reste sous les fenêtres d'Ossie.
- Critère 8 traité comme au ticket 2 : la règle des droits, pas la racine.
- Ajouté au-delà du ticket : le délégué « attach » (rattacher un élément existant, utile
  après un import), annoncé par la fenêtre d'import (« les couloirs pourront ensuite être
  rattachés aux rôles depuis l'éditeur »).
