# Session du 26 novembre 2026 — Epic « Cartographie — modèles et vues » (développement)

Epic docflow **4976292f** (workspace `ossie-studio`, bloc `backlog`) : ouvrir, dessiner
et enregistrer des modèles ArchiMate à partir des objets de la cartographie, avec le
composant `urban-archimate` (dépôt à part, livré en 0.1.1 sur PyPI). Suite de l'epic
« Cartographie — types et objets » (65d94dd0).
Branche : **`feature/cartographie-types-objets`** (déjà fusionnée une première fois
dans `dev`, PR #10).

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui décide quand fusionner sur `dev`.

## Décisions de Gael (26 novembre)

- **Intégrer la partie diagramme d'`urban-archimate`** et prendre les tickets de l'epic
  dans l'ordre : format, règles par défaut, onglet « Modèles », ouvrir, enregistrer,
  primitives MCP ; montrer le résultat de chacun.
- **Jonction** (option « b ») : Ossie garde **un seul type `Junction`** avec son
  attribut `jonction` (`And`, `Or`, absent = et) ; c'est le composant qui s'adapte.
  Ticket créé dans le backlog d'`urban-archimate` (759f0fd8, epic b5767e97) ; tant
  qu'il n'est pas livré, une jonction ne s'affiche pas correctement dans un modèle.

## Tickets

| Ticket | Sujet | Statut |
|---|---|---|
| e338fe14 | Format du modèle, du fichier de règles et du fichier graphique | en revue |
| 00d7cd6d | Fichier de règles par défaut | en revue |
| 5a05c7bb | Onglet « Modèles » | en revue |
| f5421cc7 | Ouvrir un modèle | en revue |
| c3dd045c | Enregistrer un modèle | à faire |
| be89037a | Primitives MCP modèles et vues | à faire |
| cb9d99b3 | Vérification d'intention (avec Gael) | à faire |

## Notes techniques (format, e338fe14)

- `cartographie/diagram.py` : `Diagram`, `View`, `Node`, `Connection` ; `Rules` ;
  `Graphic`, `ViewGraphic`. Lecture `parse_*`, écriture `dump_*` (déterministe, une
  géométrie par ligne en style en ligne YAML), contrôles `check_diagram`,
  `check_rules`, `check_graphic`, lecture complète `read_diagram`, écriture
  `save_diagram` / `save_rules`, `hydrate` pour le composant.
- **Formes** : le JSON Schema livré par le paquet `urban-archimate`
  (`static/hydrated-model.schema.json`) est relu ; le schéma du modèle stocké en est
  déduit (références sans `data`, sans `types`). Une évolution du contrat se propage
  donc sans recopie.
- **Codes** des contrôles en anglais comme le reste d'Ossie ; correspondance avec ceux
  du composant : `cle-retenue-sans-document` → `model_key_without_document`,
  `relation-extremite-non-retenue` → `model_relation_end_not_retained`,
  `noeud-cle-non-retenue` → `model_node_not_retained`, `connexion-cle-non-retenue` →
  `model_connection_not_retained`, `connexion-noeud-inconnu` →
  `model_connection_node_unknown`, `document-sans-objet-retenu` →
  `model_document_unused`, `graphique-*-inconnu(e)` → `graphic_entry_unknown`.
- « Se réécrivent octet pour octet » : l'exemple écrit à la main (commentaires, style
  en ligne) se lit sans erreur ; sa forme écrite par Ossie se relit et se réécrit à
  l'identique (les commentaires ne sont pas conservés, comme pour les objets).
- Le fichier graphique a un chemin imposé (`<slug du modèle>.graphique.yaml`) : un par
  modèle, jamais partagé.
- Un `defaut` écrit dans le fichier de règles est accepté (le schéma du contrat le
  permet) ; la règle par défaut d'Ossie arrive avec 00d7cd6d.
- Composant servi sous `/api/archimate` (`urban_archimate.mount`), comme
  `urban-json-editor` sous `/api/jslt-editor`.

## Notes techniques (règle par défaut, 00d7cd6d)

- `diagram.default_rule(space)` : un seul bloc pour tout l'espace (nom, documentation,
  attributs ArchiMate des relations, toutes les propriétés ajoutées des types). Un
  chemin qui ne mène à rien pour un objet n'y fait rien paraître (le composant ignore
  une valeur absente), d'où un bloc unique plutôt qu'un bloc par type.
- Le délégué de pose (f5421cc7) pourra rendre `rule_block(with_default(...))` : un bloc
  sans `type` est ignoré par le composant, qui retombe sur le `defaut` déjà reçu.
- Le nom d'échange des attributs est dans le catalogue (`exchange`), pas en dur dans le
  code. La jonction (élément, attribut `jonction`) n'en a pas encore : le composant
  n'applique les attributs qu'aux relations, et le nom attend le ticket 759f0fd8.
- « Créé vide » : le fichier de règles créé avec un modèle porte seulement `lang: fr`
  (la langue du modèle produit par le composant, `en` sinon).

## Notes techniques (onglet « Modèles », 5a05c7bb)

- Maquette « Cartographie — onglet Modèles en vignettes, fenêtre « Nouveau modèle » »
  (1922071d). Son onglet « Processus » relève d'un autre epic : pas fait ici.
- Aperçu des vignettes calculé côté serveur (`diagram.preview`) avec les mêmes règles
  de position que le composant (`src/model/geometry.ts` : relatif, taille par défaut
  120 × 55, grille de secours) ; dessiné en SVG, trait sarcelle sur la trame.
- « Branche racine en lecture seule » (critère 5) : remplacé par la décision de Gael
  du 26 novembre (brouillon sur toute branche) ; sans le droit « Cartographie :
  modifier », vignettes visibles et ouvrables, sans création ni suppression.
- `diagram.delete_object` vérifie les modèles avant `space.delete_object` (les
  modèles dépendent de l'espace, pas l'inverse : pas d'import circulaire).
- Catalogue servi à l'écran : les points de vue en plus (`viewpoints`).

## Notes techniques (ouvrir un modèle, f5421cc7)

- Vérifié avec le vrai composant (0.1.1) dans la page : positions du fichier graphique,
  thème raccordé, boîte à outils d'Ossie filtrée par le point de vue, pose par le
  délégué.
- **Pas d'outil « type de relation »** dans la boîte à outils : selon le contrat, tout
  outil passe par le délégué de pose, qui rend un objet existant. Une nouvelle relation
  se trace avec la flèche « relier » du composant, qui appelle `allowedRelations`.
- **Modèle à nettoyer** : le composant refuse un document sans objet retenu ; Ossie ne
  le transmet pas (`CLEANED_ON_OPEN`), l'enregistrement le retirera du modèle.
- Défauts vus côté composant (à proposer à Gael pour `urban-archimate`) : libellé d'un
  élément qui en contient d'autres dessiné au centre, masqué par ses enfants ; contour
  noir de focus sur la surface après un clic.
