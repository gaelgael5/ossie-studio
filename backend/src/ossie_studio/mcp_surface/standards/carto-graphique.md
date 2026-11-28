# Model drawing (carto_graphique)

The drawing of a model: position and size of its nodes, anchors of its connections, per view.

Identity: `slug` of the model (the drawing has no identity of its own and is created with its
model; it cannot be deleted alone).

Content — the drawing file:

- `vues.<view id>.noeuds.<node id>`: `x`, `y` (or `relatif` to another node with `dx`, `dy`),
  `l` (width), `h` (height).
- `vues.<view id>.connexions.<connection id>`: `ancre_source`, `ancre_cible` (`gauche`, `droite`,
  `haut`, `bas`), `points` (bend points), `libelle`.

Entries that match nothing in the model are dropped.

```yaml example
- document_type: carto_objet
  identity: {type: acces, key: REL-PRISE-ECRIT-COMMANDE}
  content: {cle: REL-PRISE-ECRIT-COMMANDE, type: acces, source: processus-metier/PRC-PRISE-COMMANDE, cible: document-metier/OBJ-COMMANDE, nom: Écrit}
- document_type: carto_modele
  identity: {slug: vente}
  content:
    id: MOD-VENTE
    nom: Vente en ligne
    regles: cartographie/regles/standard.rules.yaml
    graphique: cartographie/modeles/vente.graphique.yaml
    documents:
      - cartographie/objets/processus-metier/prc-prise-commande.yaml
      - cartographie/objets/document-metier/obj-commande.yaml
      - cartographie/objets/acces/rel-prise-ecrit-commande.yaml
    elements: [PRC-PRISE-COMMANDE, OBJ-COMMANDE]
    relationships: [REL-PRISE-ECRIT-COMMANDE]
    vues:
      - id: VUE-1
        nom: Processus et données
        noeuds:
          - {id: N1, element: PRC-PRISE-COMMANDE}
          - {id: N2, element: OBJ-COMMANDE}
        connexions:
          - {id: C1, relationship: REL-PRISE-ECRIT-COMMANDE, de: N1, vers: N2}
- document_type: carto_graphique
  identity: {slug: vente}
  content:
    vues:
      VUE-1:
        noeuds:
          N1: {x: 40, y: 60, l: 160, h: 70}
          N2: {x: 320, y: 60, l: 160, h: 70}
        connexions:
          C1: {ancre_source: droite, ancre_cible: gauche}
```
