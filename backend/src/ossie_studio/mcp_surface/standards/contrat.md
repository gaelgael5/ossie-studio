# OpenAPI contract (contrat)

An OpenAPI contract deposited on an OpenAPI source, one document per version. A deposited
contract cannot be fetched again, so Ossie keeps it in the space, as it was deposited.
**Read-only**: it is deposited and removed by actions — `deposit_contract`,
`remove_contract` (they also analyse the contract and update the index of the source).
`write_documents` and `patch_document` refuse it (`document_type_read_only`, naming these two
actions).

Identity: `source` (the slug of an OpenAPI source, `list_documents(document_type: source)`) and
`version` (the name the contract was deposited under, e.g. `v1`). Ossie computes where it
is: under `sources/<source>/contrats/`, the file the index `contrats.yaml` keeps for that
version.

Content — the OpenAPI 3.x document as deposited (Swagger 2.0 is refused at deposit), read
as an object (a JSON contract is valid YAML and is read the same way).

Reading:

- `list_documents(document_type: contrat, identity: {source})`: each deposited version with the facts
  of the deposit — `file`, `original_file`, `openapi` (version of the specification),
  `declared_version` (`info.version`), `schema_count`, `operation_count`, `uploaded_at`,
  `author` — and `collected` (whether the last collection took this file). The deposited
  version is in `identity.version`; `version` is, as for every document type, the version of the
  document. A source that is not of type `openapi` has no contract: empty list;
- `read_document(document_type: contrat, identity: {source, version})`: the contract, its path and its
  version. The size limit of written documents does not apply to reading.

What the collection made of a contract is read through the document type `schema_raw` (one raw
schema per schema of the contract, `v1-<name>.schema.yaml`).
