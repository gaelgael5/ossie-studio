# JSONPath

The paths of patch_document operations on a YAML/JSON document: RFC 9535 singular queries, one node each.

`patch_document` designates what it changes with **JSONPath** (RFC 9535), restricted to
**singular queries**: names and indexes only, the only form that designates one node.

- `$` then segments: `.name`, `['name with spaces']`, `[index]` (negative from the end).
  `$.proprietes.volume_jour`, `$.levels[0].name`, `$['proprietes']['canal']`.
- No wildcard (`*`), no descendant (`..`), no filter (`[?...]`): refused
  (`write_jsonpath_invalid`).
- Each path designates **exactly one node**; zero nodes is refused (`write_path_not_single`).

Operations:

- `set`: replace the value of a node that exists.
- `add`: add a member absent from an object, or insert in an array at an index (the index after the
  last one appends).
- `remove`: remove a node that exists.

Identity fields never change (`write_identity_frozen`); some kinds freeze other fields
(`write_field_frozen`). For a reference-list value, paths apply **inside the value**.

```yaml example-patch
kind: carto_objet
document:
  cle: PRC-PRISE-COMMANDE
  type: processus-metier
  nom: Prise de commande
  documentation: Du panier à la commande.
operations:
  - {op: set, path: $.nom, value: Prise de commande en ligne}
  - {op: add, path: $.proprietes, value: {}}
  - {op: remove, path: $.documentation}
```
