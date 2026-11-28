# Tag (tag)

A governed tag of the vocabulary, with its owner, description, links and properties.

Identity: `id`: segments of lowercase letters, digits, `_` and `-`, joined by `/` for the
hierarchy (`pii/email`). Frozen.

Content — the tag file:

- `id` (optional, must match the identity).
- `info`: `owner`, `description`.
- `links`: `{name: url}`.
- `properties`: free properties.

```yaml example
document_type: tag
identity: {id: pii/email}
content:
  info:
    owner: dpo
    description: Adresse électronique d'une personne.
  links:
    politique: https://wiki.example.org/pii
```
