# Certification (certification)

A certification that can be granted to assets, tied to a tag.

Identity: `slug` (lowercase letters, digits, dashes, e.g. `or`). Its file is named by an internal
id that Ossie finds and creates itself. Frozen.

Content:

- `slug` (optional, must match the identity), `name` (required), `rank` (integer from 1),
  `tag` (required: the tag id it grants), `description`, `criteria`, `icon`, `restricted`.

```yaml example
kind: certification
identity: {slug: or}
content:
  name: Or
  rank: 1
  tag: certification/or
  description: Donnée de référence, vérifiée et documentée.
```
