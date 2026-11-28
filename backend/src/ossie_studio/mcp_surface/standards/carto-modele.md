# Map model (carto_modele)

A model of the map: the objects and relationships it keeps, and its views (nodes and connections).

Identity: `slug` (lowercase letters, digits, dashes). The model's id (`MOD-...`) is frozen.

Content — the model file (never coordinates, never object content):

- `id`: the model's key, e.g. `MOD-VENTE`.
- `nom`, `description`.
- `regles`: path of its rules file, `cartographie/regles/<slug>.rules.yaml` (see `carto-regles`).
- `graphique`: path of its drawing, `cartographie/modeles/<model slug>.graphique.yaml`.
- `documents`: paths of the object files it loads; every kept key must have its document.
- `elements` / `relationships`: the keys of the objects / relationships it keeps.
- `vues`: list of views: `id`, `nom`, `point_de_vue` (optional ArchiMate viewpoint), `noeuds`
  (each with its own `id` and an `element` key, a `note` or a `cadre`, and `enfants`) and
  `connexions` (`id`, `relationship` key, `de` and `vers` node ids). A node or connection only
  shows something the model keeps.

A new model gets an empty drawing; replacing a model keeps its drawing (`carto_graphique`).

```yaml example
- document_type: carto_objet
  identity: {type: acces, key: REL-PRISE-ECRIT-COMMANDE}
  content: {cle: REL-PRISE-ECRIT-COMMANDE, type: acces, source: PRC-PRISE-COMMANDE, cible: OBJ-COMMANDE, nom: Écrit}
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
          - {id: N3, note: "Source : atelier du 2 décembre"}
        connexions:
          - {id: C1, relationship: REL-PRISE-ECRIT-COMMANDE, de: N1, vers: N2}
```
