# fActorI Read-Only Kernel Prototype

This crate is the first Luna-owned slice of the Rust kernel plan. It exposes a standalone
stdin/stdout boundary for deterministic protocol, canonical-JSON hash, ledger, and persisted-artifact
integrity checks. Persisted artifact checks require `--root <project-root>` and open both the
artifact and SQLite ledger read-only inside that root.

It intentionally does **not** mutate the fActorI SQLite ledger, execute subprocesses, construct
evidence capabilities, or decide claim authority. Those operations remain Python-owned until the
semantic review and cutover gates in `RUST_KERNEL_CONTRACT.md` are complete.

## Local checks

```bash
cargo fmt --manifest-path rust-kernel/Cargo.toml -- --check
cargo test --manifest-path rust-kernel/Cargo.toml
```

The request and response envelopes are exported from the Python protocol source into
`protocols/jsonschema/`. The canonical JSON corpus under `fixtures/` is shared by the Rust unit
tests and Python parity tests.
