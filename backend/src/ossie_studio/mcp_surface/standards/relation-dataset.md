# Dataset relationship (relation_dataset)

An **Ossie 0.1.1 relationship**: a foreign key from the columns of one dataset (the "many"
side) to the columns of another. Both datasets are consultable, or both virtual (they live in the
same Ossie document). Each column must be a declared field of its dataset.

Identity: `domain` and `name` (unique in the domain). Frozen.

Content — the Ossie relationship, official names: `name` (optional, must match the identity),
`from`, `to` (dataset names), `from_columns`, `to_columns` (same length). Ossie-studio does not
keep `ai_context` or `custom_extensions`.

A column that is not a field of its dataset is refused (`write_content_invalid`, at
`$.from_columns[i]` or `$.to_columns[i]`).

```yaml example
- kind: domaine
  identity: {name: ventes}
  content: {label: Ventes}
- kind: dataset
  identity: {domain: ventes, name: client}
  content:
    ossie:
      name: client
      source: boutique.client
      fields:
        - {name: id, expression: {dialects: [{dialect: ANSI_SQL, expression: id}]}}
    extension: {consultable: true, derived: false}
- kind: dataset
  identity: {domain: ventes, name: commande}
  content:
    ossie:
      name: commande
      source: boutique.commande
      fields:
        - {name: id, expression: {dialects: [{dialect: ANSI_SQL, expression: id}]}}
        - {name: client_id, expression: {dialects: [{dialect: ANSI_SQL, expression: client_id}]}}
    extension: {consultable: true, derived: false}
- kind: relation_dataset
  identity: {domain: ventes, name: commande-client}
  content: {from: commande, to: client, from_columns: [client_id], to_columns: [id]}
```
