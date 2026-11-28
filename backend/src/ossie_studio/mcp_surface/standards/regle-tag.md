# Tag rule (regle_tag)

A rule that puts governed tags on raw objects from what the code says of them (base class,
annotation, name, path, field, kind). It holds for the whole space.

Identity: `id` (lowercase letters, digits, `_`, `-`; up to 80 characters): the file name, frozen.
Ids starting with `ossie-` are **native rules**, provided by Ossie: they are listed by
`list_documents(kind: regle_tag)` with `native: true`, and only their `enabled` flag changes
(write the rule back with `enabled: false` to disable it, as on screen). A native rule is never
deleted.

Content — the rule file:

- `id` (optional, must match the identity), `name`, `description`;
- `enabled` (default true);
- `files`: glob patterns of the source files aimed at (`"*.cs"`), and/or `sources`: source types
  (`git`, `openapi`, `postgres`, `sqlserver`, `snowflake`); at least one of the two;
- `match`: `any` (default: one condition type is enough) or `all`;
- `when`: the conditions, by type — `inherits` (direct base classes), `annotation`, `name`,
  `path`, `field`, `kind` (`class`, `enum`, `model`...); each a list of glob patterns, case
  sensitive (a qualified name also compares by its short name);
- `tags`: the governed tags to put; a tag that does not exist yet is created (warning);
- `inherit_tags`: the tag passes to the classes that derive from the tagged object.

A malformed condition is refused (`write_content_invalid`, at `$.when`, with the service's code).

```yaml example
kind: regle_tag
identity: {id: aspnet-controllers}
content:
  name: Contrôleurs ASP.NET
  files: ["*.cs"]
  match: any
  when:
    inherits: [ControllerBase, Controller]
    annotation: [ApiController]
  tags: [technique/service]
  inherit_tags: true
```
