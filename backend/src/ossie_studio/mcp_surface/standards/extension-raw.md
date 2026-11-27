# Extension of a raw object (extension_raw)

What the parser knows of a raw object beyond its schema: the methods of a class, the free
functions of a module (Python, TypeScript, C# minimal API), and their entry points. It is
written next to the schema, regenerated at each collection; the schema itself stays standard
JSON Schema, without any method. **Read-only**: produced by the collection
(`collect_source`). `write_documents` and `patch_document` refuse it (`write_kind_read_only`,
naming `collect_source`).

Identity: the same as the raw schema it extends — `source` and `name` (the schema file name,
e.g. `commandescontroller.schema.yaml`, or `module-….schema.yaml` for a module). Ossie computes
where it is: `sources/<source>/raw/<key>.extend.yaml`.

Content — an object with, all optional:

- `methods` (a class) or `functions` (a module): each one has `name`, `visibility` (`public`,
  `internal`, `protected`; private methods are not collected), and may have `static`, `async`,
  `parameters` (`name`, `type` as written in the code, `schema` its JSON Schema equivalent,
  `optional`), `returns` (`type`, `schema`), `description`, `annotations`, `source` (`file`,
  `line`) and `entryPoint`;
- `entryPoint` (the object itself, e.g. a message consumer class).

An entry point has `kind` (`http`, `task`, `message`), `verb` and `route` for HTTP (`route:
null` when the route cannot be computed), `topic` for a message, and `contract` once the
collection compared it with the OpenAPI contracts of the source: `status` `linked` (with the
matched `operations`) or `absent`. No `contract`: not compared.

```yaml content
methods:
- name: Lire
  visibility: public
  parameters:
  - name: id
    type: Guid
  returns:
    type: Commande
  entryPoint:
    kind: http
    verb: GET
    route: /api/commandes/{id}
    contract:
      status: absent
- name: Calculer
  visibility: internal
  static: true
```

Reading:

- `list_documents(kind: extension_raw, identity: {source})`: the raw objects that have an
  extension, with `methods`, `functions` (their numbers) and `entry_point` (whether the object
  itself is one). Filter `filters: {module: <name or title>}` (with `identity.source`): the
  objects of a module;
- `read_document(kind: extension_raw, identity: {source, name})`: the whole extension. An object
  without method nor function has none: `document_not_found`.
