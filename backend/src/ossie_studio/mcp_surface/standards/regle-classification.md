# Classification rule (regle_classification)

A rule that gives a classification level to the fields of raw objects, from their name, their
type, the tags of their class, the class or the file. The level put by a rule yields to a level
declared by hand.

Identity: `id` (lowercase letters, digits, `_`, `-`), frozen. Ids starting with `ossie-` are
**native rules** provided by Ossie: listed with `native: true`, only `enabled` changes; never
deleted.

Content — the rule file:

- `id` (optional, must match the identity), `name`, `description`, `enabled` (default true);
- `files`: glob patterns of the source files (optional: all files);
- `match`: `all` (default) or `any`;
- `when`: the conditions — `field` (field name), `type` (field type), `tag` (a tag of the
  class), `class` (class name); lists of patterns, compared **without case**;
- `scheme`: the id of an existing classification; `values`: names of its levels (one only for a
  single-value classification).

The classification and its levels must exist when the rule is written: a level that does not
exist is refused (`write_content_invalid`, at `$.values`). In one `write_documents`, the
classification and the rule can come together.

```yaml example
- document_type: classification
  identity: {id: rgpd}
  content:
    name: RGPD
    levels:
      - {name: Donnée personnelle, short_name: DP}
      - {name: Publique, short_name: PUB}
- document_type: regle_classification
  identity: {id: rgpd-emails}
  content:
    name: Emails
    match: all
    when:
      field: ["*email*", "*mail"]
      type: [string]
    scheme: rgpd
    values: [Donnée personnelle]
```
