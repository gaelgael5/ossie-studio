# Domain (domaine)

A domain is an **Ossie semantic model**: a set of datasets with their relationships. Ossie keeps
it in three files of `domaines/` and chooses them itself (never give a path): `<name>.ossie.yaml`
(consultable datasets, Ossie 0.1.1 strict), `<name>.virtuel.ossie.yaml` (non-consultable
datasets, same form), `<name>.extend.yaml` (what Ossie 0.1.1 does not carry). The documents
Ossie writes are checked against the official Ossie 0.1.1 JSON Schema: no Ossie-studio data in
them.

Identity: `name` (lowercase letters, digits, dashes): the domain's name and the `name` of its Ossie
semantic model. Frozen.

Content: `label` (free label shown on screen, extension), `description` (Ossie). Its datasets,
relationships and references are document types of their own: `dataset`, `relation_dataset`,
`reference_dataset`.

A domain is created, with its datasets and relationships, in one `write_documents`: the order
of the list is free. It is deleted only when it has no dataset left (delete them in the same
batch).

```yaml example
- document_type: domaine
  identity: {name: ventes}
  content: {label: Ventes, description: Les ventes de la boutique.}
- document_type: dataset
  identity: {domain: ventes, name: client}
  content:
    ossie:
      name: client
      source: boutique.client
      primary_key: [id]
      fields:
        - {name: id, expression: {dialects: [{dialect: ANSI_SQL, expression: id}]}}
    extension: {consultable: true, derived: false}
```
