# Entry point of the code (point_entree)

An entry point found in the code by the collection: an HTTP route (verb and route), a
background task or a message consumer, carried by a method of a class, by a free function of
a module (minimal API, module-level function), or by the class itself. **Read-only**: produced
by the collection (`collect_source`). `write_documents` and `patch_document` refuse it
(`document_type_read_only`, naming `collect_source`).

Identity — copy it from `list_documents`; it never contains a file path and stays the same
from one collection to the next while the code does not change:

- `source`: the slug of the source;
- `owner`: the raw schema that carries it (the `name` of a class or of a module in
  `schema_raw`);
- `method`: the method or function name; `*` when the class itself is the entry point;
- `endpoint`: `VERB /route` for an HTTP entry point (e.g. `GET /api/commandes/{id}`); `task`
  or `message` otherwise.

Ossie computes where it is: an entry of the `.extend.yaml` of its owner (document type
`extension_raw`). Its `version` is the version of its content.

Content:

- `kind` (`http`, `task`, `message`), `verb`, `route` (`null` when the route cannot be
  computed);
- `method`, `owner` (the title of the class, or `null` for a free function of a module),
  `owner_name`, `module` and `module_name` (the module it belongs to: the nearest project file
  up the tree);
- `contract_status`: `linked` (matched to an operation of an OpenAPI contract of the source,
  listed in `contract_operations`), `absent` (missing from the contract) or `not_compared`.

Reading:

- `list_documents(document_type: point_entree, identity: {source})`: the entry points of a source.
  Filters: `module` (a module, by its name or title), `contract_status`, `kind`. Which routes
  of the code are missing from the contract? `filters: {contract_status: absent}`;
- `read_document(document_type: point_entree, identity: {source, owner, method, endpoint})`: one entry
  point.

## Reading a whole module

Everything about one module takes four reads, all with `identity.source`:

1. `read_document(document_type: schema_raw, identity: {source, name: <module>})`: the module and its
   facts (`x-module`: project file, nature, targets, dependencies);
2. `list_documents(document_type: schema_raw, filters: {module: <module>})`: its objects;
3. `read_document(document_type: extension_raw, identity: {source, name: <module>})`: its free
   functions (none: `document_not_found`);
4. `list_documents(document_type: point_entree, filters: {module: <module>})`: its entry points.

The module list with its counts and dependencies in both directions:
`list_documents(document_type: schema_raw, filters: {kind: module})`.
