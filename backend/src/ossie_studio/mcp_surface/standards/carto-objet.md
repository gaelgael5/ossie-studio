# Map object (carto_objet)

An object of the map, or a relationship (an object of a relationship type, with source and target).

Identity: `type` (slug of an existing type of the map) and `key` (uppercase letters, digits,
dashes: `APP-CAISSE`). The key is unique in the space whatever the type, and frozen; the type is
frozen. The exact schema of `proprietes` for a type: `read_schema(kind: carto_objet, type: ...)`.

Content — the object's file, keys in this order:

- `cle`: the key (same as the identity).
- `type`: the type's slug (same as the identity).
- `source`, `cible`: for a relationship only, the keys of its two ends (objects or relationships),
  frozen. The relationship must be allowed by ArchiMate (and the type's rules) between them.
- `nom`: the name (required).
- `documentation`: markdown, optional.
- `proprietes`: the ArchiMate attributes of the type, **as ArchiMate values** (`acces: Write`,
  absent = not specified), and the added properties of the type. Nothing else is accepted.

An object used by a relationship or kept by a model cannot be deleted: delete the relationships in
the same batch, or remove it from the models first.

```yaml example
- kind: carto_objet
  identity: {type: role, key: ROL-PREPARATEUR}
  content:
    cle: ROL-PREPARATEUR
    type: role
    nom: Préparateur de commandes
    documentation: |
      Prépare les colis en entrepôt.
- kind: carto_objet
  identity: {type: affectation, key: REL-PREPARATEUR-PRISE-COMMANDE}
  content:
    cle: REL-PREPARATEUR-PRISE-COMMANDE
    type: affectation
    source: ROL-PREPARATEUR
    cible: PRC-PRISE-COMMANDE
    nom: Prépare
- kind: carto_objet
  identity: {type: acces, key: REL-PRISE-COMMANDE-ECRIT-COMMANDE}
  content:
    cle: REL-PRISE-COMMANDE-ECRIT-COMMANDE
    type: acces
    source: PRC-PRISE-COMMANDE
    cible: OBJ-COMMANDE
    nom: Écrit la commande
    proprietes:
      acces: Write
```
