# Dataset (dataset)

A dataset of a domain. Its content has **two parts, kept apart**:

- `ossie`: the dataset **exactly as the Ossie 0.1.1 document writes it** (official JSON Schema,
  `read_schema(kind: dataset)`): `name` (optional, must match the identity), `source`,
  `description`, `primary_key`, `unique_keys`, `fields`. A field: `name`, `expression` with
  **one** dialect `ANSI_SQL` whose expression is the column name (or the property, for a
  non-consultable dataset), `label`, `description`. Ossie-studio does not keep `ai_context`,
  `custom_extensions` or `dimension`: they are refused rather than lost.
- `extension`: what Ossie 0.1.1 does not carry (in `<domain>.extend.yaml`): `consultable` (a
  table or view: true; a code class, a contract: false), `derived` (derived from an asset),
  `asset` (`{source, path}` of the asset it comes from), `fields` (per field name: `datatype`,
  `property`, `referentiel`).

Identity: `domain` and `name` (unique in the domain). Frozen.

Ossie stores the dataset in the consultable or the virtual document according to
`extension.consultable`; changing it moves the dataset (patch `$.extension.consultable`). A
dataset cited by a relationship or a reference cannot be deleted alone: delete them in the same
batch.

```yaml example
- kind: domaine
  identity: {name: ventes}
  content: {label: Ventes}
- kind: dataset
  identity: {domain: ventes, name: commande}
  content:
    ossie:
      name: commande
      source: boutique.commande
      description: Une commande d'un client.
      primary_key: [id]
      fields:
        - {name: id, expression: {dialects: [{dialect: ANSI_SQL, expression: id}]}}
        - name: total
          label: Total TTC
          expression: {dialects: [{dialect: ANSI_SQL, expression: total_ttc}]}
    extension:
      consultable: true
      derived: false
      fields:
        total: {datatype: Decimal}
```
