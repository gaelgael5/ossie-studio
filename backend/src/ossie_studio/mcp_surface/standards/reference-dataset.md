# Dataset reference (reference_dataset)

An **object link** between two datasets, without key columns: a property of one object that
points to another object (a code class holding another one, for instance). Ossie 0.1.1 cannot
express it as a relationship: it lives in the extension (`<domain>.extend.yaml`).

Identity: `domain` and `name` (unique in the domain). Frozen.

Content: `name` (optional, must match the identity), `from` (dataset), `field` (the property of
`from` that points), `to` (dataset pointed at), `kind` (`field`: one object, `list`, `map`),
`cardinality` (optional, for display: `n:1`, `1:n`...).

```yaml example
- kind: domaine
  identity: {name: catalogue}
  content: {label: Catalogue}
- kind: dataset
  identity: {domain: catalogue, name: produit}
  content:
    ossie:
      name: produit
      source: Catalogue.Produit
      fields:
        - {name: categorie, expression: {dialects: [{dialect: ANSI_SQL, expression: Categorie}]}}
    extension: {consultable: false, derived: false}
- kind: dataset
  identity: {domain: catalogue, name: categorie}
  content:
    ossie:
      name: categorie
      source: Catalogue.Categorie
    extension: {consultable: false, derived: false}
- kind: reference_dataset
  identity: {domain: catalogue, name: produit-categorie}
  content: {from: produit, field: categorie, to: categorie, kind: field, cardinality: "n:1"}
```
