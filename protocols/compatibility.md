# Protocol Compatibility Policy

`factori check-protocol-compat` performs a conservative structural comparison of two directories
containing `*.schema.json` files. It is a developer-contract check, not a proof of application-level
semantic compatibility and not scientific evidence.

## Statuses

- `Compatible`: no breaking or unknown changes were detected.
- `CompatibleWithWarnings`: no breaking changes were detected, but at least one change could not be
  classified safely.
- `BreakingChangesDetected`: one or more breaking changes were detected.
- `ComparisonFailed`: directories or schema documents could not be read and compared.

## Breaking Changes

The checker classifies these changes as breaking:

- removing or renaming a schema file without retaining the old file as an alias;
- removing any property, including an optional property;
- adding a required property or making an existing property required;
- narrowing or incompatibly changing accepted JSON types;
- removing enum values;
- adding or tightening minimum/maximum, length, item-count, or property-count constraints;
- forbidding previously allowed additional properties;
- adding a pattern, `multipleOf`, enum, constant, or type restriction.

Optional-property removal is intentionally breaking. Although the producer was not required to send
the property, generated consumers may still depend on it when present.

## Non-Breaking Changes

The checker classifies these changes as non-breaking:

- adding an optional property or a new schema file;
- making an existing required property optional;
- widening accepted types, including integer to number;
- adding enum values;
- removing or relaxing recognized constraints;
- allowing previously forbidden additional properties;
- adding an internal definition.

## Documentation-Only Changes

Titles, descriptions, comments, examples, schema identifiers, and the generated protocol-version
or source-model metadata are classified as documentation changes. Unknown or evidence-related
`x-*` metadata is not assumed to be documentation. Renaming a top-level protocol is represented by
a removed schema file plus a new schema file and therefore remains breaking unless the old filename
is retained as an alias.

## Unknown Changes

The checker does not claim compatibility for changes involving `anyOf`, `oneOf`, `allOf`, `not`,
unresolved references, changed defaults, complex `additionalProperties`, removed definitions, or
unrecognized schema keywords. These are reported as unknown and should be reviewed manually.

## Usage

```bash
uv run factori check-protocol-compat \
  --old-dir path/to/old/jsonschema \
  --new-dir path/to/new/jsonschema \
  --fail-on-breaking
```

Use `--json` for stable machine-readable output. The command is read-only: it creates no files,
run artifacts, artifact-manifest entries, or ledger commits.
