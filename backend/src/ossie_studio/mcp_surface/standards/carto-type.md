# Map type (carto_type)

A type of the map: an ArchiMate type and the properties added to its objects.

Identity: `slug` (lowercase letters, digits, dashes): the file name, frozen.

Content — the type's schema file:

- `title`: the label shown on screen.
- `x-archimate`: the ArchiMate type (`ApplicationComponent`, `BusinessProcess`, `Access`...),
  frozen once objects exist. A relationship type uses an ArchiMate relationship (`Access`,
  `Assignment`, `Serving`...). Every ArchiMate 3.2 type (id, French label, layer, nature) is
  given with this standard by `describe_standard('carto-type')`, in `archimate_catalogue`.
- `type: object`.
- `properties` / `required`: only the **added** properties (JSON Schema 2020-12: string with
  `enum`, `format: date`, number, integer, boolean, object, array; names in lowercase, digits and
  `_`). The base (key, name, documentation, the ArchiMate attributes of the type) comes from the
  catalogue and is not rewritten; an added property cannot take an ArchiMate attribute's name.
- `x-relations` (optional): relationships disabled or added for this type.

```yaml example
kind: carto_type
identity: {slug: application}
content:
  title: Application
  x-archimate: ApplicationComponent
  type: object
  properties:
    criticite:
      type: string
      enum: [haute, moyenne, basse]
    utilisateurs:
      type: integer
      minimum: 0
```
