# Model rules (carto_regles)

A rules file: how the objects of the space translate into the ArchiMate exchange format.

Identity: `slug` (e.g. `standard`). A rules file cannot be deleted alone (models use it).

Content:

- `lang`: language of the exported texts (`fr`).
- `defaut` (optional): the block used for an object without a rule of its own. Without it, Ossie's
  default rule applies (name ← `$.nom`, documentation ← `$.documentation`, ArchiMate attributes
  and added properties under the same names).
- `elements` / `relationships`: blocks keyed by `type` (a type slug of the space, or an
  ArchiMate type), each with `name`, `documentation`, `properties` (`propertyDefinitionRef`,
  `value`) and ArchiMate attributes (`accessType`, `modifier`, `isDirected`...). A value is a path
  in the object (`$.nom`, `$.proprietes.acces`) or a literal.

```yaml example
kind: carto_regles
identity: {slug: metier}
content:
  lang: fr
  elements:
    - type: processus-metier
      name: $.nom
      documentation: $.documentation
```
