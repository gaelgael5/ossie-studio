# Writing rules

Rules shared by write_documents and patch_document: identity, versions, batches, size, rights.

## Say what you write, never where

Every document is designated by its **document type** (`document_type`) and its **identity** (`list_document_types` gives the
identity fields of each document type). Ossie computes where the document is stored, exactly as the screen
does: never give a `path` (it is refused for every document type today).

## Versions

- No `version`: the entry **creates** the document; it is refused if the document already exists
  (`write_exists`, with `current_version`).
- With `version`: the entry **replaces** the whole document, or **deletes** it (`delete: true`).
  Take the version from `read_document` (same document type and identity), read with `branch: draft`:
  writes go to the user's draft, so the draft holds the version to give. A stale version is
  refused (`write_version_stale`, with `current_version`): read the document again before
  writing.
- Sending a document identical to the stored one changes nothing (`action: unchanged`).

## A batch is checked on its final state

`write_documents` applies its list **all together or not at all**, in one change of the user's
draft (never published). The order of the list is free: a relationship may come before the object
it targets, a value before the model of its reference list, an object before its type, an object
may be deleted with the relationships that use it. On any refusal nothing is written and every
error is returned.

## An identity never changes

A key, a slug or an id is never renamed. To change it, **create the new document and delete the
old one in the same batch**, re-creating what pointed to the old one. `patch_document` refuses to
touch an identity field (`write_identity_frozen`).

Rename the business object `OBJ-COMMANDE` into `OBJ-COMMANDE-V2`:

```yaml example
- document_type: carto_objet
  identity: {type: document-metier, key: OBJ-COMMANDE-V2}
  content:
    cle: OBJ-COMMANDE-V2
    type: document-metier
    nom: Commande
- document_type: carto_objet
  identity: {type: document-metier, key: OBJ-COMMANDE}
  version: "<version from read_document>"
  delete: true
```

## Size

Each document is limited in size once written (`document_max_bytes`, given by
`list_document_types` and `describe_standard`): each entry of `write_documents`, the document
obtained by `patch_document`, a reference-list value alone (never the whole values file). Beyond
it: `write_document_too_large`, with the size and the limit.

## Rights and branch

Each document needs the screen's right for its document type on the space; one document type without right refuses
the whole batch (`document_type_forbidden`). Writing always goes to the user's draft: omit `branch`
or pass `draft`. Only an agent acting for a person can write.
