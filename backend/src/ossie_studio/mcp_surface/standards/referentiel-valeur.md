# Reference-list value (referentiel_valeur)

One value of a reference list, designated by its key: never the whole values file.

Identity: `referentiel` (slug) and `key`: the value of the key property, or for a composite key an
object `{property: value}` with every key property. The exact schema of a value:
`read_schema(kind: referentiel_valeur, referentiel: ...)`.

Content — the value, conforming to the model (closed objects). The key properties may be omitted:
they come from the identity; given, they must match it (a key never changes: create the new value
and delete the old one in the same batch). The values file stays sorted by key; a value's version
only changes when the value changes.

```yaml example
- kind: referentiel_modele
  identity: {referentiel: delais}
  content:
    title: Délais de livraison
    type: object
    x-key: [pays, mode]
    required: [pays, mode, jours]
    properties:
      pays: {type: string}
      mode: {type: string, enum: [site, domicile]}
      jours: {type: integer, minimum: 0}
- kind: referentiel_valeur
  identity: {referentiel: delais, key: {pays: FR, mode: site}}
  content: {pays: FR, mode: site, jours: 2}
- kind: referentiel_valeur
  identity: {referentiel: delais, key: {pays: BE, mode: domicile}}
  content: {jours: 4}
```
