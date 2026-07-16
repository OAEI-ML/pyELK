# WP11 — Complete Rust/PyO3 Backend

## Goal

Implement the compiled-ontology decoder, property closure, class saturation, consistency,
taxonomy, realisation, and queries in Rust, exposed through one thin private PyO3 session.
Match the pure engine exactly while releasing Python and scaling across configured workers.

## Read first

| Source | Sections |
|---|---|
| `specs/native-packaging.md` | §§1–4, 7–11 |
| `specs/contracts.md` | §§7–10 |
| `specs/saturation.md` | all |
| `specs/taxonomy-queries.md` | all |
| `specs/verification.md` | §§6–9, 12 |
| WP5–WP7 pure implementations/tests/manifests | semantic reference |

## Depends on

WP7. WP8/WP9 may proceed in parallel; the specifications and raw contracts are frozen.

## Owned paths

```text
Cargo.toml
Cargo.lock
rust-toolchain.toml
rust/**
src/pyelk/_native.pyi
tests/backends/test_rust_core.py
tests/backends/test_rust_saturation_differential.py
benchmarks/bench_native_boundary.py
```

## Forbidden paths

Python OWL/parser/indexing/reasoning implementations, public facade/dispatcher, setup/build
configuration, completeness/oracle data, release workflows.

## Deliverables

1. Cargo workspace with Python-free `pyelk-core` and thin `pyelk-pyo3` cdylib.
2. Defensive ontology/query IR v1.0 decoders (including `None` query fallbacks) and result
   packed codec with protocol handshake.
3. Full property and class reasoning engine, consistency, taxonomy, realization, and query
   methods; no callbacks or Python objects in core.
4. Duplicate-free concurrent scheduler with one mutating worker per context and no lost
   wakeups.
5. Deterministic canonical raw outputs and optional bounded debug snapshot/counters.
6. PyO3 `NativeSession`, Python detachment, panic containment, lifecycle, and type stub.
7. Cargo unit/property tests, Miri-compatible safe core where possible, fuzz targets,
   concurrency stress tests, and Python differential tests against WP7 saturation.
8. Criterion/stage/boundary benchmarks and worker scaling report.

## Acceptance criteria

1. `cargo test --locked --all-features`, Clippy with warnings denied, rustfmt, and fuzz smoke
   pass; no unjustified `unsafe` exists.
2. Decoder rejects every WP0 corrupt-input family without panic/oversized allocation.
3. Bounded debug conclusions/property closure exactly equal pure Python for upstream and at
   least 10,000 generated tiny cases.
4. Public raw outcomes are deterministic for workers 1, 2, N, repeated runs, and shuffled
   inputs; concurrency stress shows no stranded work/deadlock.
5. Exported methods detach Python during core work, retain no Python reference, catch panics,
   and permanently invalidate a failed/closed session.
6. Native boundary time is below 5% on medium/large benchmark inputs; native core is at least
   5x pure geometric-mean throughput in the recorded environment.
7. The extension imports only as private `pyelk._native`; no public/native type leaks and no
   forbidden Python file changed.
