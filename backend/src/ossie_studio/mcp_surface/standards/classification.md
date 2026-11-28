# Classification (classification)

A classification scheme with its ordered levels.

Identity: `id` (a safe name, e.g. `sensibilite`). Frozen.

Content — the scheme file:

- `id` (optional, must match the identity), `name` (required), `description`, `badge_prefix`,
  `multi_value`, `is_primary`, `display_order`.
- `levels`: the ordered list of levels: `name`, `short_name` (required), `label`, `rank`,
  `description`, `criteria`, `color`, `icon`, and `id` for an existing level.

When a classification is replaced (or patched), a level **with** its `id` is updated, a level
**without** `id` is created, an existing level missing from the list is deleted (refused if it is
used), and the levels take the order of the list. To add a level with `patch_document`:
`{op: add, path: "$.levels[0]", value: {name: Interne, short_name: INT}}`.

```yaml example
document_type: classification
identity: {id: sensibilite}
content:
  name: Sensibilité
  description: Niveau de sensibilité d'une donnée.
  levels:
    - {name: Public, short_name: PUB, color: green}
    - {name: Interne, short_name: INT}
    - {name: Confidentiel, short_name: CONF, color: red}
```
