# Raw schema (schema_raw)

A schema collected from a source: one per object of the source (a table, an entity, an API
schema). **Read-only**: it is produced by the collection (`collect_source`) and only the
collection keeps it up to date. `write_documents` and `patch_document` refuse it
(`write_kind_read_only`, naming `collect_source`).

Identity: `source` (slug of the source, `list_documents(kind: source)`) and `name` (the schema
file name, as `list_documents(kind: schema_raw)` gives it, e.g. `customer.schema.yaml`). Ossie
computes where it is: `sources/<source>/raw/<name>`.

Content — a JSON Schema 2020-12 document, as the parser of the source wrote it (`title`, `type`,
`properties`, `required`, `$ref` to other raw schemas, `x-kind`...).

Reading:

- `list_documents(kind: schema_raw, identity: {source})`: each schema, its number of fields and
  whether it is retained as an asset (`retained`: none, partial, full);
- `read_document(kind: schema_raw, identity: {source, name})`: the file as written;
- `read_schema(kind: schema_raw, identity: {source, name})`: the schema, references resolved.
