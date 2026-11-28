# Errors and warnings

The shape of the refusals and warnings of the primitives, and how to act on them.

On any refusal of `write_documents` or `patch_document`, nothing is written and **every** error
is returned at once:

```json
{
  "error": "write_refused",
  "written": false,
  "errors": [
    {
      "path": "$.documents[2].content.proprietes.volume_jour",
      "code": "carto_object_invalid",
      "message": "volume_jour doit être un entier supérieur ou égal à 0 ; reçu -5.",
      "expected": {"type": "integer", "minimum": 0},
      "received": -5,
      "aide": {"primitive": "describe_standard", "standard": "carto-objet"}
    }
  ],
  "warnings": []
}
```

- `path`: the JSONPath of the faulty data **in your request** (`$.documents[i]...`, or
  `$.operations[i].path` for a malformed operation). For `patch_document`, an error found when the
  modified document is checked points into the document (`$.proprietes.canal`); for BPMN, the
  message gives the line.
- `code`: a stable code (the screen's codes, prefixed by domain: `carto_...`, `write_...`).
- `message`: in French, readable as is, with what was received and what was expected.
- `expected` / `received`: when they help.
- `aide`: for a format error, where to read the rule — `describe_standard` with a standard, or
  `list_document_types` for an unknown document type. Every primitive adds it to its format errors.

On success: `written: true`, the draft `revision`, each document's document_type, identity, path, `action`
(`created`, `replaced`, `deleted`, `unchanged`) and new `version`, and the `warnings`:

```json
{"document": "$.documents[0]", "message": "Facture (OBJ-FACTURE) n'est pas relié à Prise de commande dans la cartographie."}
```

A warning never blocks: the document is written.

## Reading errors

`read_document`, `list_documents` and `read_schema` return one error at a time, with the same
fields at the top level (`error` for the code, `path` in your request, `message`, `expected`,
`received`, `aide`):

- `document_not_found`: no document of that document type and identity in the version read (with
  `expected_path` when known). Not a format error: no `aide`. Read with `branch: draft` a
  document you have just written.
- `identity_ambiguous`: a partial identity given to `read_document` (e.g. a map key without its
  type) is carried by several documents; `candidates` lists their full identities.
- `list_filter_unknown`: an identity field or a filter the document type does not declare (`expected`
  lists the possible ones); `list_filter_invalid`: a filter value of the wrong form.
- `schema_document_type_untyped`: the content of that document type depends on no type; read its standard.
