# Native structural ingestion and compilation

Status: normative successor optimization with a complete repository-owned hidden-path checkpoint
at pyELK `886f6a3`. Capability promotion and release acceptance remain open. It changes the native
compilation path, not the ELK `0.6.0` compatibility profile, public results, or complete
pure-Python fallback.

## 1. Objective

The optimized path MUST compile an existing `pyowl_core.OntologyView` into a Rust-owned ELK
session without scalar Python axiom expansion or the intermediate
`CompiledOntology.encode()`/native-decode round trip. The same retained core snapshot must remain
usable by pyHermiT, projection, Exact-OM, and OAEI without reparsing or flattening.

This is consumer compilation, not a second ontology parser or public model. ELK-specific
polarity, feature occurrence, expression interning, property closure, saturation state, and
taxonomy data remain private pyELK IR.

## 2. Capability and fallback contract

The optimized compiler requests the public, versioned
`pyowl_core.EncodedStructuralView` through `OntologyView.view(...)` after validating
`CoreCapabilities.encoded_view_schemas`. It MUST:

- retain the encoded view owner until native session close;
- validate schema name/version, model schema, scope, descriptor digest, buffer bounds,
  endianness, alignment, segment references, and structural fingerprint before use;
- consume read-only buffers through the Python buffer protocol in one or a bounded number of
  coarse calls;
- support direct, decoded, mmap-backed, overlay, and composite encoded views; and
- never import `pyowl_core._native`, inspect a private core arena, persist schema-local IDs, or
  call Python once per axiom or term.

The scalar compiler remains the complete compatibility path. A core/provider without the encoded
capability still works through the current Python `iter_axioms()` compiler and either backend.
`backend="python"` never requires native buffers or a compiler. `backend="rust"` means the Rust
reasoner, not mandatory encoded ingestion; diagnostics report whether session creation used
`encoded-native` or `scalar-wire` ingestion. `auto` may prefer encoded-native only after its
parity and performance gates pass.

Capability absence is not a semantic error. Malformed or falsely advertised encoded data is a
core adapter/protocol error and MUST NOT silently fall back after partial consumption.

## 3. Optimized pipeline

```text
existing OntologyView identity
          |
          +-- Python backend --> scalar deterministic compiler --> Python session
          |
          +-- compatibility --> scalar deterministic compiler --> encoded ELK IR --> Rust session
          |
          `-- optimized -----> EncodedStructuralView buffers/segments
                                  |
                         Rust feature scan + polarity compiler
                                  |
                         Rust-owned ELK IR/session directly
```

The optimized path must not construct the Python `OntologyBuilder`, per-axiom
`IndexTransaction`, Python expression graph, or serialized private IR. It may allocate exactly
the ELK-specific Rust representation required for reasoning. Segment-aware compilation of an
overlay or composite reuses base columns during traversal and must not flatten the core view into
a second structural ontology.

Rust compiler output is bound to:

```text
(core structural fingerprint,
 core model schema,
 encoded-view schema and descriptor digest,
 encoded-view scope/segment manifest,
 ELK compatibility profile,
 pyELK compiler schema,
 pyELK package/native implementation version)
```

No cache uses dense encoded IDs without all owner/schema/fingerprint components above.

## 4. Semantic parity

For every accepted ontology, the scalar and encoded compilers MUST produce equivalent private IR
and exact public behavior for:

- supported/ignored construct decisions and feature occurrence counts;
- polarity and reversible whole-axiom rejection;
- entity, expression, literal, individual, and property identities;
- duplicate/permutation handling and canonical ordering;
- consistency, classification, realization, queries, entailment, and completeness reports; and
- annotations, extensions, imports, provenance roles, overlays, and composites where they affect
  compilation or diagnostics.

A canonical compiler digest and per-section counts compare both paths without making private IR a
public API. Forced differential campaigns include every frozen ELK fixture, generated constructor
coverage, hash-seed/worker permutations, and the complete native suite. Any mismatch fails closed;
there is no per-axiom Python callback or semantic fallback inside a native session.

## 5. Lifetime, safety, and concurrency

The binding may borrow encoded buffers only while holding a strong owner and only under a
documented buffer-lifetime contract. Rust long-running work releases the GIL and holds no Python
callback. Session close, interpreter shutdown, cancellation, panic, fork, and provider close are
tested so no dangling view or buffer remains.

If the native compiler copies a column, the copy is measured and justified. The normal direct or
mmap-backed path has zero ontology-sized staging copy and no private-IR byte serialization. Rust
allocations are checked against configured limits before count-derived growth. Recursive
composite resolution may re-encode its bounded selected-root postings (four bytes per resolved
root ID) and anonymous-scope remaps (64 bytes per source/target digest pair) for the detached Rust
compiler call; `encoded_staging_copy_bytes` reports those exact temporary byte lengths. Column
buffers remain borrowed and base views are never flattened, so these metadata copies do not count
as copied structural columns or serialized private IR.

## 6. Performance evidence

Benchmarks distinguish:

1. already-loaded view to validated encoded view;
2. ELK compilation/session creation;
3. first consistency/classification/realization result;
4. warm repeated session queries; and
5. incremental RSS, copied bytes, materialized Python objects, buffer calls, and worker scaling.

Every timed path validates identical result digests and uses the same snapshot identity, options,
workers, and cache state. The current boundary microbenchmark over an already compiled hierarchy
is retained but cannot serve as end-to-end evidence.

Release evidence uses the full generated and hash-pinned biomedical suite with the repetitions,
machine labelling, Java comparison, and semantic gates in `verification.md`. Acceptance requires:

- zero parser/resolver/wire-encoder/wire-decoder calls for an existing compatible view;
- zero scalar axiom/term materialization and zero base flattening on encoded-native ingestion;
- boundary plus view-validation time below 5% of fresh native compile-and-classify time on each
  designated medium/large workload;
- encoded-native view-to-result time at least 2x faster than scalar-wire native by geometric mean,
  with no nontrivial workload more than 10% slower outside the measured noise floor;
- no more than 10% incremental-RSS regression without an approved measured tradeoff; and
- no regression against the existing native reasoning gates or pinned Java-relative targets.

The optimization target is to remove compilation as a dominant phase and beat Java ELK
end-to-end. Until controlled evidence passes, documentation calls encoded ingestion experimental
and does not infer performance from the existence of Rust code.

## 7. Versioning and packaging

Adopting an additive core encoded-view capability does not by itself change ELK semantics or the
public pyELK API. The implementation PR records the minimum core package/API/adapter and exact
encoded schema range, updates compiler/cache schema versions when bytes or digest meaning change,
and invalidates incompatible private caches rather than reinterpreting them.

Native and pure artifacts retain the same pyELK version and public features. Pure wheels and the
compiler-free sdist remain complete on Python 3.10+. No artifact gains Java, Horned-OWL, OWLAPI,
mOWL, Exact-OM, or OAEI as a runtime dependency.

## 8. Completion

This successor optimization is complete only when scalar Python, scalar-wire Rust, and
encoded-native Rust produce exact results; direct/mmap/overlay/composite lifetimes and hostile
descriptors pass; release-scale time/RSS/copy evidence passes; public documentation and
provenance identify the selected ingestion path; and Exact-OM/OAEI handoff tests observe the same
core snapshot with no repeated parse or ontology-sized materialization.

The implementation checkpoint satisfied the repository-owned adapter/compiler, semantic parity,
hostile-input, lifecycle, and diagnostic portions above. For 0.1.0, the release owner closed the
remaining release decision, accepted the external gate dispositions, and bound the advertised
capability to pyowl-core 0.1.0 commit
`d3e7893b0609fcd7df390375267a00356f09cb22`. Historical pre-promotion evidence and its original
blocker wording remain in the [WP14 handoff report](../reports/workpackages/WP14.md), followed by
the dated production-release disposition.
