# Relation profile (profil_relation)

A naming convention that gives the **virtual relations** of SQL sources whose foreign keys are
not declared (a data warehouse, a Data Vault): `ACCOUNT_HK` points to `ACCOUNT_HUB.ACCOUNT_HK`.
A SQL source applies the profiles it chooses (`relation_profiles` of its manifest, changed by
`update_source`); the relations are computed when the Explorer index is built, never written
in raw. Changing a profile needs no new collection: the relations follow at the next read.

Identity: `id` (lowercase letters, digits, `_`, `-`), frozen. Ossie computes where it is:
`governance/relation-profiles/<id>.yaml`.

The profile **provided** by Ossie, `ossie-datavault` ("Data Vault 2.0"), exists without any
file: `list_documents(kind: profil_relation)` lists it (`builtin: true`). Writing a profile
under its id customizes it for the space (`customized: true`); deleting that customization
restores the provided profile. The provided profile itself is never deleted, and the
`ossie-` prefix is reserved to it.

Content:

- `id` (optional, must match the identity), `name`, `description`;
- `rules`, at least one, tried in order (the first that finds its target wins). Each rule:
  - `column`: a regular expression on the **whole** column name, with named groups
    (`(?<name>…)` or `(?P<name>…)`);
  - `table`: the template of the target table, built from the groups (`"DIM_{name}"`); the
    table is looked for in the same schema, then in the whole source;
  - `target_column`: the template of the target column (`"ID"`, `"{name}_HK"`);
  - `kinds` (optional): the natures of the objects that carry the column (`table`, `view`).

A template may only cite a named group of its expression. An invalid expression or template is
refused (`write_content_invalid`, at `$.rules[i].column` or `$.rules[i].table`, with the
service's code: `relation_rule_pattern_invalid`, `relation_rule_template_invalid`).

```yaml example
kind: profil_relation
identity: {id: etoile}
content:
  id: etoile
  name: Modèle en étoile
  description: Une colonne <X>_ID pointe la colonne ID de la dimension DIM_<X>.
  rules:
    - column: "(?<nom>[A-Z0-9_]+)_ID"
      table: "DIM_{nom}"
      target_column: ID
      kinds: [table, view]
```

```yaml example-patch
kind: profil_relation
document:
  id: etoile
  name: Modèle en étoile
  rules:
    - {column: "(?<nom>[A-Z0-9_]+)_ID", table: "DIM_{nom}", target_column: ID}
operations:
  - {op: set, path: "$.rules[0].table", value: "D_{nom}"}
```

Reading:

- `list_documents(kind: profil_relation)`: each profile with `name`, `rules` (their number),
  `builtin`, `customized`, and `errors` for a file Ossie cannot read;
- `read_document(kind: profil_relation, identity: {id})`: the profile (the space's version, or
  the provided one).
