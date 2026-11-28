# Asset schema (schema_asset)

The schema of an asset: the part of a raw object retained for the semantic layer. **Read-only**:
it is produced by the curation (`retain_asset`, `enrich_asset`, `refresh_assets`), which also
writes the human work beside it (business name, description, concept, tags in the
`.extend.yaml`). `write_documents` and `patch_document` refuse it (`write_kind_read_only`,
naming those actions).

Identity: `source` (slug of the source) and `name` (the schema file name, as
`list_documents(kind: schema_asset)` gives it). Ossie computes where it is:
`sources/<source>/assets/<name>`.

Content — a JSON Schema 2020-12 document: the retained fields of the raw schema.

Reading:

- `list_documents(kind: schema_asset, identity: {source})`: each asset, whether it is `orphan`
  (its raw object disappeared) and the sorting rule that excludes it (`excluded_by`);
- `read_document(kind: schema_asset, identity: {source, name})`: the file as written;
- `read_schema(kind: schema_asset, identity: {source, name})`: the effective schema, merged with
  its human work, and its business enrichment in `extras`.
