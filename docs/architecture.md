# Architecture: the compile-then-reason pipeline

pyELK is organised as a strict pipeline. Each `Reasoner` captures one immutable
ontology view, compiles it once into a private deterministic representation, and
creates exactly one backend session that answers every later request from cached
saturation stages. Sessions are immutable: there is no incremental update, and a
new revision of an ontology means a new reasoner.

```text
OntologyInput ──▶ pyowl-core coercion ──▶ capture & handshake
                                              │
                                              ▼
                                    indexing (ELK conversion)
                                              │  CompiledOntology IR
                                              ▼
                                      backend session (Python | Rust)
                                              │
                        property saturation ──▶ class saturation
                                              │
                                              ▼
                      consistency ▸ taxonomy ▸ realization ▸ queries
                                              │
                                              ▼
                        public values + completeness metadata
```

The stage boundaries below are normative in [`specs/parsing.md`](../specs/parsing.md),
[`specs/indexing.md`](../specs/indexing.md), [`specs/saturation.md`](../specs/saturation.md),
and [`specs/taxonomy-queries.md`](../specs/taxonomy-queries.md).

## 1. Inputs: the shared structural layer

pyELK owns no OWL model, parser, or import resolver. Those belong to the
independent `pyowl-core` package, and pyELK re-exports its classes by identity.
Construction performs one `pyowl_core.coerce_snapshot` call: paths, bytes, and
streams are parsed by core; documents are assembled without reparsing; existing
views (snapshot, overlay, composite) are retained by identity; a
`SnapshotProvider` is asked for its snapshot exactly once. Before compilation the
facade validates the core package/API/model/wire/adapter versions and the view's
capability set, failing with core's `AdapterCompatibilityError` rather than
guessing.

Import acquisition is an input-layer policy decided by `LoadOptions`. A view whose
provenance records an ignored or unresolved import closure is only accepted with
`allow_incomplete_imports=True`, and every result from such a session carries the
`PYELK_IGNORED_IMPORT` completeness reason.

## 2. Indexing: from OWL values to compiled IR

The `pyelk.indexing` package converts the logical axiom closure into a
`CompiledOntology` — the single backend-neutral compiled representation. It
reproduces pinned ELK 0.6.0 behavior:

- **Transactional conversion.** Each logical axiom converts inside an isolated
  transaction. If a nested construct is unsupported, the whole axiom rolls back,
  exactly the rejected feature occurrence is recorded, and (in the default
  `ignore` mode) compilation proceeds; no ghost entity or rule survives.
- **Structural interning with polarity tracking.** Expressions are interned by
  structural key with ELK's simplifications (empty intersection → `owl:Thing`,
  n-ary intersections left-associated, `ObjectHasValue` → existential over a
  nominal, and so on). Polarity is occurrence metadata, not identity, and every
  occurrence of every upstream `Feature` is counted for later completeness
  evaluation.
- **Deterministic freezing.** Entity IDs are ordered by `(kind, UTF-8 IRI)`,
  expression IDs by a deterministic topological order, and all tables are
  deduplicated and sorted. Freezing the same effective view is byte-identical
  under every hash seed, platform, and input permutation with equal core
  fingerprints.

The IR carries a `source_fingerprint` derived from core logical/signature
fingerprints and version metadata — never from reserialized axiom text — and
serialises to a validated, checksummed binary format (`PYELKIR`) that is the only
payload crossing into the native extension on the scalar path.

Queries reuse the same machinery: class expressions and entailment axioms compile
into self-contained mini-IRs against the session symbol table. A query the pinned
ELK converter cannot index compiles to `encoded=None`, and the backends answer it
with ELK's exact unindexed-query fallback values.

## 3. Backends and ingestion paths

One backend session exists per reasoner. The dispatcher resolves
`ReasonerConfig.backend`, `PYELK_BACKEND`, and `PYELK_PURE_PYTHON` into `python`
or `rust`; `auto` requires a version/IR handshake and self-check before selecting
the native extension and otherwise keeps Python with the fallback reason.

There are three ingestion paths, reported as `ingestion_path` in
`diagnostics()`:

- `scalar-python` — the Python compiler produces `CompiledOntology` dataclasses
  consumed directly by the pure backend;
- `scalar-wire` — the same compiled IR is encoded once and transferred to the
  native session as bytes;
- `encoded-native` — when both the core view and the native build advertise the
  encoded structural-column schema, the ontology is handed to the native compiler
  directly, bypassing scalar row materialization; the facade then decodes only
  bounded compiler metadata.

Backend calls are coarse-grained — whole ontology in, whole taxonomy/realization/
query result out — never per-axiom callbacks. Raw native results are decoded from
a checksummed wire format (`PYELKRAW`) and re-validated in Python; any violation
raises `BackendProtocolError` instead of leaking invalid data.

## 4. Saturation: properties first, then classes

Reasoning is ELK's consequence-based, context-oriented saturation, staged
monotonically:

```text
COMPILED → PROPERTIES → CONSISTENCY → CLASSIFIED → REALIZED
```

**Property saturation** runs first and completely: it closes the told
subproperty/chain axioms under reflexivity and transitivity, computes inherited
ranges, and precomputes the prefix/suffix composition maps that authorise link
composition during class saturation. No class rule may consult a partially built
property closure.

**Class saturation** allocates a context per demanded root (named class, nominal
individual, or query root), seeds it with its initialization conclusions, and
processes a duplicate-suppressed agenda: pop a novel conclusion, store it, apply
the static and occurrence-driven rules it triggers, and enqueue derived
conclusions — locally or into another context's inbox. Rules with several
premises fire regardless of arrival order. The pure Python engine is
single-threaded but implements the same queue-state transitions as the concurrent
Rust engine, making it a direct oracle; Rust output is independent of worker
count and schedule.

Stages advance lazily and are cached: a consistency request saturates only
`owl:Thing` and asserted individuals plus what they demand; classification adds
all named-class roots; realization adds named individuals. Complex query roots
are cached by canonical mini-IR key and never appear in public enumeration.

## 5. Taxonomy, realization, and queries

Saturated subsumptions are quotiented by mutual inclusion into equivalence nodes,
then transitively reduced into the public `Taxonomy` (nodes, direct sub→super
edges, top, bottom). Unsatisfiable named classes join the bottom node.
Realization groups individuals by derived equality and links each instance node
to its minimal named type nodes. The facade converts validated raw IDs back into
the exact `pyowl_core` entity objects captured from the input view.

## 6. Completeness metadata

A single backend-independent evaluator turns the ontology and query feature
counts into `CompletenessIssue` values per task, reproducing ELK's
incompleteness monitors — including the quiet no-incompleteness fallbacks that
inconsistent-ontology taxonomy/realization/query values use. Backends never
suppress or invent issues; every public answer is a `ReasoningResult` whose
`complete` flag is true exactly when no reason applies.

## Where to read more

- [`specs/contracts.md`](../specs/contracts.md) — frozen public and internal contracts.
- [`specs/compatibility.md`](../specs/compatibility.md) — the supported ELK 0.6 fragment.
- [`specs/native-packaging.md`](../specs/native-packaging.md) — Rust backend and wheel policy.
- [`specs/verification.md`](../specs/verification.md) — parity and release gates.
