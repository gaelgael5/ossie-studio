# Map object (carto_objet)

An object of the map, or a relationship (an object of a relationship type, with source and target).

Identity: `type` (slug of an existing type of the map) and `key` (uppercase letters, digits,
dashes: `APP-CAISSE`). The key is unique **within its type** (two types may have the same key),
and frozen; the type is frozen. An object is designated elsewhere as `type/KEY`
(`application/APP-CAISSE`). The exact schema of `proprietes` for a type:
`read_schema(document_type: carto_objet, type: ...)`.

Content — the object's file, keys in this order:

- `cle`: the key (same as the identity).
- `type`: the type's slug (same as the identity).
- `source`, `cible`: for a relationship only, its two ends (objects or relationships) as
  `type/KEY`, frozen. A key alone is still read when it exists in one type only, and is rewritten
  as `type/KEY` on the next write. The relationship must be allowed by ArchiMate (and the type's
  rules) between them.
- `nom`: the name (required).
- `documentation`: markdown, optional.
- `proprietes`: the ArchiMate attributes of the type, **as ArchiMate values** (`acces: Write`,
  absent = not specified), and the added properties of the type. Nothing else is accepted.
  A property **linked to a reference list** (`x-referentiel` in `read_schema`, the type's
  `<slug>.extend.yaml`) must hold a key of that list (composite key: one property per part):
  a value absent from the list is refused (`property_referentiel_unknown`); list the values with
  `list_documents(document_type: referentiel_valeur, referentiel: ...)`. An unchanged value that
  has since left the list is not refused, only reported on the object.

An object used by a relationship or kept by a model cannot be deleted: delete the relationships in
the same batch, or remove it from the models first.

```yaml example
- document_type: carto_objet
  identity: {type: role, key: ROL-PREPARATEUR}
  content:
    cle: ROL-PREPARATEUR
    type: role
    nom: Préparateur de commandes
    documentation: |
      Prépare les colis en entrepôt.
- document_type: carto_objet
  identity: {type: affectation, key: REL-PREPARATEUR-PRISE-COMMANDE}
  content:
    cle: REL-PREPARATEUR-PRISE-COMMANDE
    type: affectation
    source: role/ROL-PREPARATEUR
    cible: processus-metier/PRC-PRISE-COMMANDE
    nom: Prépare
- document_type: carto_objet
  identity: {type: acces, key: REL-PRISE-COMMANDE-ECRIT-COMMANDE}
  content:
    cle: REL-PRISE-COMMANDE-ECRIT-COMMANDE
    type: acces
    source: processus-metier/PRC-PRISE-COMMANDE
    cible: document-metier/OBJ-COMMANDE
    nom: Écrit la commande
    proprietes:
      acces: Write
```
