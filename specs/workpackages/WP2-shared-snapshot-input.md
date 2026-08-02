# WP2 — Shared Snapshot Ingestion and Exact-OM Handshake

## Goal

Implement pyELK's thin input adapter over pyowl-core. Provide standalone path/bytes/stream
loading and zero-reparse document/view/`SnapshotProvider` ingestion, with
bounded-copy, import-policy, ownership, and provenance behavior. Do not implement or fork an
OWL syntax parser or writer.

## Read first

| Source | Sections / symbols |
|---|---|
| `specs/parsing.md` | all |
| `specs/contracts.md` | §§1–4, 6–7 |
| `specs/verification.md` | input, hostile-input, packaging, performance gates |
| pyowl-core 0.2 specification | `parse_document`, `load_snapshot`, `coerce_snapshot`, providers, overlays |
| pinned ELK parser fixtures | adapter/compiler parity inputs only |

## Depends on

WP1 and WP3 (for the attributed frozen ELK input corpus).

## Owned paths

```text
src/pyelk/inputs.py
tests/unit/inputs/**
tests/integration/test_shared_snapshot_input.py
tests/properties/test_input_fingerprints.py
benchmarks/bench_snapshot_ingestion.py
```

Obsolete `src/pyelk/parsing/**` and `src/pyelk/ontology.py` implementations are deleted by
this WP after imports are moved to the core adapter. A re-export-only compatibility module
may remain for one deprecation cycle only if a previously published pyELK release requires
it; it MUST contain no parser/model implementation.

## Forbidden paths

`pyowl_core` internals, OWL parser/serializer implementation, compiled indexing/reasoning,
backends, Rust, completeness/oracle generation, upstream fixture contents, and final facade.

## Deliverables

1. `load_snapshot` convenience delegating to `pyowl_core.load_snapshot` for
   acquisition/document input only, returning the concrete snapshot; view/provider input
   is rejected exactly as core rejects it (`contracts.md` §2).
2. One-call `coerce_snapshot` adapter accepting the full `OntologyInput` union.
3. Exact-OM integration solely through `SnapshotProvider.owl_snapshot()`; no Exact-OM
   import, private-record traversal, or path fallback after a provider is supplied.
4. Strict ownership/lifetime and zero-reparse tests using counting streams, parsers,
   providers, resolvers, and weak references.
5. Import closure/incomplete-import mapping into pyELK policy metadata without performing a
   second import traversal.
6. Fingerprint/revision/overlay capture and cache-key records binding all required core and
   pyELK schema versions.
7. Million-axiom, overlay, and source/target/bridge composite benchmarks demonstrating no
   duplicate structural collection,
   no text/RDF serialization intermediate, and bounded temporary memory.

## Acceptance criteria

1. Paths, bytes, streams, documents, snapshots, overlays, composites, and providers compile to equal
   pyELK input observations; non-source inputs invoke the parser zero times.
2. A compatible view is retained by identity; a provider is called once; the
   reasoner/input capture keeps it alive but never owns/closes it.
3. Functional Syntax, OWL/XML, RDF/XML, Turtle, and any additional core-supported syntax
   work standalone through core without pyELK format-specific code.
4. Cyclic imports and repeated imports are represented/traversed once; strict errors and
   accepted incomplete closures follow `LoadOptions.imports` exactly.
5. An overlay changing O(k) axioms adds O(k) shared-layer memory before ELK compilation;
   pyELK neither compacts nor mutates the base; a composite concatenates no component axioms.
6. Core wire/cache version mismatch and corruption rebuild safely; no incompatible cache is
   interpreted.
7. All tests pass on Python 3.10/3.12 without Java, network, compiler, or native extension;
   only owned paths change.
