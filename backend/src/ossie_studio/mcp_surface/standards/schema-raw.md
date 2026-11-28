# Raw schema (schema_raw)

A schema collected from a source: one per object of the source (a table, an entity, an API
schema). **Read-only**: it is produced by the collection (`collect_source`) and only the
collection keeps it up to date. `write_documents` and `patch_document` refuse it
(`document_type_read_only`, naming `collect_source`).

Identity: `source` (slug of the source, `list_documents(document_type: source)`) and `name` (the schema
file name, as `list_documents(document_type: schema_raw)` gives it, e.g. `customer.schema.yaml`). Ossie
computes where it is: `sources/<source>/raw/<name>`.

Content — a JSON Schema 2020-12 document, as the parser of the source wrote it (`title`, `type`,
`properties`, `required`, `$ref` to other raw schemas, `x-kind`...).

Reading:

- `list_documents(document_type: schema_raw, identity: {source})`: each schema, its nature (`kind`, its
  `x-kind`), its number of fields and whether it is retained as an asset (`retained`: none,
  partial, full). Filters:
  - `filters: {kind: module}` (or `class`, `table`, `view`…): only the objects of that nature;
  - `filters: {module: <name or title>}` (with `identity.source`): the objects that belong to a
    module, by the rule of the Explorer (the nearest project file up the tree of their file of
    origin);
- `read_document(document_type: schema_raw, identity: {source, name})`: the file as written;
- `read_schema(document_type: schema_raw, identity: {source, name})`: the schema, references resolved.

## Modules (application map)

A module is a project file of the code (`.csproj`, `.sqlproj`, Gradle or Maven module,
`package.json`, `pyproject.toml`). The collection stores it in raw like any object
(`module-<key>.schema.yaml`, `x-kind: module`, its facts under `x-module`).
`list_documents(document_type: schema_raw, identity: {source}, filters: {kind: module})` lists the modules
of a source; each one carries, besides the common fields:

- `nature` (library, web, worker, console, test, sql, application), `targets`, `language`,
  `project` (the project file);
- `counts`: `classes`, `entry_points`, `public_methods` (its objects and its own functions);
- `depends_on`: the modules it depends on (`{name, title}`), or `{project, external: true}` for
  a project outside the source; `used_by`: the modules that depend on it.

Its objects: `filters: {module: <name or title>}`. Its schema: `read_document`.
