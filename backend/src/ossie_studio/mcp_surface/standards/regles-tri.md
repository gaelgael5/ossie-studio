# Sorting rules of a source (regles_tri)

The sorting rules of a source set noise aside in the curation (generated objects, technical
tables...). They live in the `triage` section of the source's manifest; Ossie rewrites that
section only, never the rest of the manifest.

Identity: `source` (slug of the source, `list_sources`), frozen.

Content — the **whole list** of rules (an empty list: none). Each rule:

- `target`: what the pattern matches — `object-name`, `object-type`, `field-name`, `file-path`,
  `sql-schema`;
- `pattern`: the pattern;
- `kind`: `glob` or `regex` (a regex must compile);
- `label` (optional): why the rule exists, shown on screen.

`write_documents` replaces the list (version required: the rules always exist, empty or not);
`patch_document` changes one rule (`$[0].pattern`). The rules of a source are never deleted:
write an empty list.

```yaml example
kind: regles_tri
identity: {source: ventes}
version: "<version from read_document>"
content:
  - {target: object-name, pattern: "*Migration*", kind: glob, label: Migrations EF}
  - {target: sql-schema, pattern: "^tmp_", kind: regex}
```

```yaml example-patch
kind: regles_tri
document:
  - {target: object-name, pattern: "*Migration*", kind: glob}
operations:
  - {op: set, path: "$[0].pattern", value: "*Snapshot*"}
```
