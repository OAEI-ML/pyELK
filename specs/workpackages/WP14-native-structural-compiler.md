# WP14 — Encoded structural compiler and native session handoff

**Goal:** replace scalar Python ontology compilation on the optimized Rust path with direct,
segment-aware compilation from pyowl-core's public `EncodedStructuralView`.

**Status:** production schema-1 implementation shipped on pyELK 0.1.x; schema-2 consumer migration
implemented for pyELK 0.2.0. **Depends on:** WP13 and the exact released/candidate pyowl-core
model-2/encoded-schema-2 contract recorded in the release compatibility ledger.

The exact checkpoint, verification commands, revisions, and blockers are recorded in the
[WP14 handoff report](../../reports/workpackages/WP14.md).

## Read first

- `../native-structural-ingestion.md` complete;
- `../SPEC.md` §§7–10;
- `../compatibility.md`, `../indexing.md`, `../native-packaging.md`, and
  `../verification.md` complete; and
- pyowl-core `native-ontology-redesign.md`, `indexes-views.md`, and WP17 handoff/schema ledger.

## Owned paths

- the new public-core encoded-view adapter under `src/pyelk/indexing/`;
- the Rust structural decoder/compiler and session-construction modules under both Rust crates;
- `src/pyelk/backends/rust.py`, `src/pyelk/_native.pyi`, and ingestion diagnostics;
- focused encoded-ingestion, hostile-descriptor, lifetime, differential, and consumer tests;
- encoded-ingestion benchmarks/evidence and the directly affected docs/spec traceability; and
- coordinated dependency/compiler-schema metadata changes required to advertise the capability.

Existing scalar compiler behavior, saturation semantics, public results, and frozen oracle data
are not rewritten. Any shared build/release file edit is called out explicitly and kept to the
negotiated dependency or feature declaration.

## Deliverables

1. Negotiate the exact encoded schema and validate every descriptor/buffer/segment before native
   compilation.
2. Compile feature occurrences, polarity, expressions, properties, axioms, annotations, and
   supported extensions into Rust-owned ELK IR without Python per-row objects or private-IR bytes.
3. Create the permanent Rust reasoning session directly from that IR and retain the encoded-view
   owner for every borrow lifetime.
4. Keep scalar Python and scalar-wire Rust fallbacks complete, with explicit ingestion-path
   diagnostics and no semantic change to backend selection.
5. Add canonical compiler digest/count comparison, exhaustive Python/native differential cases,
   direct/mmap/overlay/composite tests, corrupted-buffer/fuzz/limit tests, and close/fork/thread/
   panic lifecycle tests.
6. Add end-to-end shared-view-to-result benchmarks with Python object, parser, wire, copy, FFI,
   time, worker, and RSS counters; run the full release corpus and same-machine Java comparison.
7. Update compatibility tables, cache/provenance schemas, changelog/migration guidance, wheel
   matrices, SBOM/license audits, and Exact/OAEI conformance pins without adding a runtime cycle.

## Acceptance

- Exact scalar-versus-encoded compiler digests/counts and all public semantic results agree for
  frozen, generated, W3C, biomedical, overlay, and composite cases.
- An existing compatible snapshot reaches a native session with zero parser, resolver, core wire,
  scalar-materialization, per-axiom FFI, serialized-private-IR, or base-flattening counter delta.
- Direct and mmap buffers require no ontology-sized staging copy; any exceptional copied column is
  bounded, reported, justified, and included in the performance gate.
- Malformed or incompatible encoded inputs fail before session publication and never silently
  switch paths after partial consumption.
- The time/RSS/boundary gates in `../native-structural-ingestion.md` pass on the labelled runner;
  smoke results alone cannot complete this WP.
- Pure Python installs and compiler-free sdists remain complete on every supported interpreter,
  while native artifacts pass the existing ABI, sanitizer, dependency, license, and no-Java
  audits.
- Exact-OM and OAEI integration fixtures pass the exact shared view/composite identity and report
  the selected ingestion path without importing either consumer from pyELK.

## Handoff

Publish the supported core package/API/adapter/encoded-schema range, pyELK compiler schema,
canonical fixture digests, benchmark evidence, and the exact consumer revisions tested. pyELK
must remain independently usable with older compatible scalar-only core providers.

## Implementation checkpoint

The contiguous WP14 implementation series from `7edbb07` through `886f6a3` now provides the
hidden public-core adapter, defensive Rust decoder/compiler, direct session construction,
direct/mmap/overlay/composite traversal, transactional unsupported handling, compiler summary
parity, lifecycle hardening, ingestion timing/copy accounting, and bounded public handoff
diagnostics. The complete frozen constructor ledger is either compiled with scalar-equivalent
semantics or rejected through the same whole-axiom unsupported policy. Scalar Python and
scalar-wire behavior remain complete.

That historical checkpoint preceded capability promotion. The 0.1.x release dispositions and the
0.2.0 schema-2 migration addendum in the handoff report supersede its old capability-state wording
without rewriting the recorded benchmark evidence. The 0.2.0 release boundary is bound to the
exact final pyowl-core commit/tree in `release/core-compatibility.json`; generated supply-chain
evidence and installed-artifact gates fail closed if that contract diverges.
