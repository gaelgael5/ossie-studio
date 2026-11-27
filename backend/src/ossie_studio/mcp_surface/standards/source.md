# Data source (source)

A data source declared in the space: its manifest. **Read-only**: a source is declared,
changed and deleted by actions, because they also fetch the code, check the connection and
start the collection — `declare_source`, `update_source`, `delete_source`. `write_documents`
and `patch_document` refuse it (`write_kind_read_only`, naming these three actions). The
sorting rules of the manifest (`triage`) are written through their own kind, `regles_tri`.

Identity: `source` (the slug of the source, e.g. `crm`). Ossie computes where it is:
`sources/<source>/manifest.yaml`.

Content — the manifest as stored:

- `slug`, `label`, `type` (`git`, a database engine such as `snowflake`, or `openapi`);
- `connection`: the **name** of a connection of the space (never a secret: credentials stay
  in the encrypted store and are never returned);
- `parameters`: what the type needs (repository branch and parsers, schemas of a database…);
- `relation_profiles` (optional): the relation profiles a SQL source applies;
- `triage` (optional): the sorting rules (kind `regles_tri`).

A manifest that Ossie cannot read is still listed, with `ok: false` and the reason in `error`
(`manifest_missing`, `slug_mismatch`, an invalid field…); `read_document` then gives the file
as it is (its text if it is not even YAML).

Reading:

- `list_documents(kind: source)`: each source with `label`, `type`, `connection`,
  `parameters`, `ok` (and `error`), and `unpublished_changes` (files of the user's draft not
  yet published under the source, with `branch: draft`). Filter: `filters: {type: snowflake}`;
- `read_document(kind: source, identity: {source})`: the manifest, its path and its version.
