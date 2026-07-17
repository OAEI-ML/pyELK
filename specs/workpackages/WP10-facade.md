# WP10 — Public Facade, Results, Configuration, and Backend Dispatch

## Goal

Integrate pyowl-core view ingestion, compiler, completeness, pure saturation, taxonomy,
realisation, and queries behind the final typed `Reasoner` API. Implement raw-result validation, canonical
public values, lazy backend sessions, fresh/import/strict policies, and Python/Rust dispatch.

## Read first

| Source | Sections / symbols |
|---|---|
| `specs/contracts.md` | §§1–6, 8–10 |
| `specs/compatibility.md` | §§7–11 |
| `specs/native-packaging.md` | §8 dispatcher |
| `specs/taxonomy-queries.md` | public semantics |
| pinned `Reasoner.java`, non-incremental `AbstractReasonerState`/stage manager | lazy API behaviour |

## Depends on

WP2, WP3, WP4, WP8, and WP9 (WP7 transitively).

## Owned paths

```text
src/pyelk/__init__.py
src/pyelk/api.py
src/pyelk/config.py
src/pyelk/result.py
src/pyelk/backends/__init__.py
src/pyelk/backends/python.py
src/pyelk/backends/rust.py
tests/unit/test_api.py
tests/unit/test_results.py
tests/unit/test_config.py
tests/unit/backends/**
tests/integration/test_pure_reasoner.py
```

`src/pyelk/inputs.py` remains WP2-owned; this WP re-exports/uses it without restructuring.

## Forbidden paths

pyowl-core/input/indexing/reasoning implementations, completeness logic, contracts/codec, Rust,
build/release files, oracle/frozen fixture contents, benchmarks.

## Deliverables

1. Exact public exports, `ReasonerConfig`, result/node/taxonomy/instance values, exact core
   OWL/view re-exports, WP0 completeness contract identities, and lifecycle.
2. Pure `BackendSession` adapter assembling WP7–WP9 components and caching stages.
3. Rust adapter with version handshake, packed raw-result decode/validation, close/panic error
   mapping, and clear unavailable behaviour; it must work with a fake `_native` before WP11.
4. Dispatcher precedence, environment validation, self-check, explicit failure, automatic
   fallback, and `backend_report`.
5. Every public method/signature from `contracts.md`, deterministic canonical conversion, and
   completeness attachment from the sole evaluator.
6. Core import-provenance handling, one-call view/provider capture, complete
   `CompiledQuery.fresh_entities` validation, strict
   unsupported/query modes, unindexable-query fallback dispatch, closed-session errors, and
   context-manager support.
7. Facade-owned reentrant per-session serialization, atomic close versus in-flight calls,
   and immutable result lifetime after close.
8. Full pure-Python end-to-end integration tests over frozen fixtures.

## Acceptance criteria

1. Pure backend exactly reproduces all applicable frozen ELK semantic/completeness JSON for
   class/property taxonomy, realisation, class queries, and entailment.
2. Every public method returns the documented type/order; internal IDs/native values never
   leak; malformed raw results raise `BackendProtocolError`.
3. `auto` uses fake-valid Rust, falls back with reason on absent/broken/mismatched Rust, while
   explicit Rust fails clearly and explicit Python never imports `_native`.
4. Fresh allowed/disallowed, strict/explicitly incomplete core imports, unsupported ignore/error, unsupported
   query false/error, quiet inconsistency-before-fresh precedence, every inconsistent
   value/no-monitor case, repeated calls, and close lifecycle all pass.
5. `complete == not reasons` and `require_complete()` are enforced for every task.
6. Public import/use and the complete pure integration suite run with Java/Cargo/compiler
   absent and network denied.
7. Import-linter and all checks pass; only owned paths changed.
8. Multi-thread calls on one reasoner serialize without race/deadlock; close waits for an
   in-flight fake-native call and every later operation fails consistently.
9. Passing an Exact-OM-style provider or existing view invokes no parser, retains object
   identity/lifetime, and returns the same results as standalone `load_snapshot`.
