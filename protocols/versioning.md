# Protocol Versioning Rules

The checked-in JSON Schemas under `protocols/jsonschema/` are developer contracts for Python,
future Rust tools, future servers, and coding agents. They are not run provenance or scientific
evidence.

The current protocol version is recorded in `protocols/version.json`.

## Required Bumps

- MAJOR: required for breaking schema changes.
- MINOR: required for backward-compatible additions, including new schemas or optional fields.
- PATCH: allowed for documentation-only schema metadata changes.
- No bump: allowed only when exported schemas are byte-for-byte semantically unchanged.
- Human review: required for unknown compatibility changes.

Breaking changes include removed schemas, removed properties, added required fields, narrowed
types, removed enum values, and stricter constraints. Non-breaking changes include new schemas, new
optional fields, added enum values, and less restrictive constraints. Documentation-only changes
include title, description, comments, examples, and generated metadata.

## Checks

Use:

```bash
uv run factori check-protocol-version \
  --old-dir path/to/old/jsonschema \
  --new-dir path/to/new/jsonschema \
  --old-version 0.1.0 \
  --new-version 0.5.0
```

Unknown changes fail by default. `--allow-unknown` is an explicit human-review override for the
version check only; it does not prove compatibility.
