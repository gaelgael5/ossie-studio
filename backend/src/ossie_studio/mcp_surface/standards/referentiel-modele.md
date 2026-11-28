# Reference-list model (referentiel_modele)

The model of a reference list: a JSON Schema of its values, with the key that makes each value unique.

Identity: `referentiel` (slug: lowercase letters, digits, dashes). A reference list cannot be
deleted by an agent.

Content — a JSON Schema 2020-12 of one value:

- `title`, `description`.
- `type: object`, `properties`, `required`.
- `x-key`: the property (or properties, for a composite key) whose values make a value unique.
- Objects are **closed**: a value cannot carry an undeclared property. Types: string (with `enum`,
  `format: date` / `date-time`), number, integer, boolean, object, array.
- Replacing the model re-checks every existing value: a value that no longer fits refuses the
  write.

```yaml example
document_type: referentiel_modele
identity: {referentiel: pays}
content:
  title: Pays
  type: object
  x-key: [code]
  required: [code, nom]
  properties:
    code: {type: string, description: Code ISO 3166-1 alpha-2}
    nom: {type: string}
    zone: {type: string, enum: [UE, hors-UE]}
```
