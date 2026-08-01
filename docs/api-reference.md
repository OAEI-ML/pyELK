# pyELK API reference

This page describes the complete supported public surface of the `pyelk` package.
The normative contracts live in [`specs/contracts.md`](../specs/contracts.md); the
supported reasoning fragment and completeness behavior are pinned in
[`specs/compatibility.md`](../specs/compatibility.md) and
[`specs/taxonomy-queries.md`](../specs/taxonomy-queries.md).

`pyelk` exports the reasoning facade (`Reasoner`, `ReasonerConfig`, `load_snapshot`,
`backend_report`), the public result values (`ReasoningResult`, `CompletenessIssue`,
`PolicyFeature`, `EntityNode`, `Taxonomy`, `InstanceTaxonomy`), and exact
`pyowl_core` re-exports (`LoadOptions`, `ImportResolver`, `OntologyDocument`,
`OntologySnapshot`, `OntologyOverlay`, `OntologyComposite`, `OntologyView`,
`SnapshotProvider`, `Fingerprint`, `API_VERSION`, and the OWL structural types).
Every re-exported OWL type is the same class object as in `pyowl_core`; pyELK never
wraps or copies the shared model.

## Creating a session

```python
Reasoner(
    ontology,                  # pyowl_core.OntologyInput
    config=None,               # ReasonerConfig | None
    *,
    document_iri=None,         # IRI | str | None
    load_options=None,         # pyowl_core.LoadOptions | None
    resolver=None,             # pyowl_core.ImportResolver | None
)
```

`ontology` accepts a path (`str`/`PathLike`), bytes-like value, caller-owned text or
binary stream, `OntologyDocument`, any immutable `OntologyView` (snapshot, overlay,
or composite), or a `SnapshotProvider`. Input crosses exactly one
`pyowl_core.coerce_snapshot` call:

- a compatible view is retained by exact identity, with no reparse and no copy;
- a provider's `owl_snapshot()` is called exactly once;
- only a path, bytes value, or stream invokes core parsing. `document_iri` is
  required for caller-owned streams, which pyELK never closes.

A plain `str` is always a filesystem path, never inline ontology text or a URL.
Format selection, import policy, and parse limits are configured entirely through
`pyowl_core.LoadOptions` and an optional `ImportResolver`.

Use the reasoner as a context manager, or call `close()` explicitly. `close()` is
idempotent and terminal: it releases backend state, every later operation raises
`ReasonerClosedError`, and already returned public values remain valid.
`reasoner.ontology` is the exact captured core object and stays caller-shareable.

One reasoner is safe to call from multiple Python threads; a reentrant session lock
serializes stage, query, diagnostic, and `close()` calls. Run parallel independent
workloads with separate reasoner sessions.

`pyelk.load_snapshot` is the exact `pyowl_core.load_snapshot` function, re-exported
for standalone acquisition. It accepts document input only; an existing view or
provider is passed to `Reasoner` directly.

## Configuration

`ReasonerConfig` is an immutable dataclass, snapshotted at construction:

| Field | Default | Meaning |
|---|---|---|
| `backend` | `"auto"` | `"auto"` prefers a compatible native extension and otherwise selects Python, retaining the fallback reason. `"python"` never imports the native module. `"rust"` raises `BackendUnavailableError` instead of falling back. |
| `workers` | `0` | `0` means logical CPU count for Rust and one worker for Python. Python accepts larger values but reports `effective_workers=1`; worker count never changes results. Negative values raise `ValueError`. |
| `allow_fresh_entities` | `True` | When `False`, a query over entities absent from the ontology raises `FreshEntityError` before backend execution. Fresh entities otherwise behave as bare declarations. `owl:Thing`, `owl:Nothing`, and the top/bottom object properties are never fresh. |
| `unsupported` | `"ignore"` | `"ignore"` matches ELK: unsupported axioms are skipped and surfaced through completeness reasons. `"error"` raises `UnsupportedFeatureError` at the first unsupported axiom during construction, and `UnsupportedQueryError` for unsupported queries. |
| `allow_incomplete_imports` | `False` | Required to accept a view whose core provenance records an ignored or unresolved import closure; every result then carries the `PYELK_IGNORED_IMPORT` policy reason. |

Two environment variables set process-wide defaults, resolved when each session is
created:

- `PYELK_BACKEND=auto|python|rust` supplies the default backend request;
- `PYELK_PURE_PYTHON=1` forces Python and prevents even probing the native module.
  Combining it with an explicit `rust` request fails with `ValueError`.

## Results and completeness

Every reasoning operation returns `ReasoningResult[T]` with three fields:

- `value` — the canonical, immutable answer;
- `complete` — `True` exactly when `reasons` is empty;
- `reasons` — a sorted, deduplicated tuple of `CompletenessIssue` values, each
  naming the affected `task`, the upstream ELK `features` (or the
  `PYELK_IGNORED_IMPORT` policy value), the OWL `constructors`, and `polarities`.

`result.require_complete()` returns `value` or raises `IncompleteReasoningError`
carrying the same reasons. "Complete" means complete for the pinned ELK 0.6
procedure, not for arbitrary OWL 2; the exact feature and combination monitors are
listed in [`specs/compatibility.md`](../specs/compatibility.md).

Node-valued results use `EntityNode`, a nonempty tuple of mutually equivalent
entities sorted canonically; `node.canonical_member` is its first member.

## Global operations

| Method | Returns | Notes |
|---|---|---|
| `is_consistent()` | `ReasoningResult[bool]` | OWL 2 Direct Semantics consistency. |
| `is_inconsistent()` | `ReasoningResult[bool]` | Inverse decision, same metadata. |
| `classify()` | `ReasoningResult[Taxonomy[Class]]` | Named-class taxonomy; cached per session. |
| `classify_object_properties()` | `ReasoningResult[Taxonomy[ObjectProperty]]` | Named object-property taxonomy; complex chains are never members. |
| `realize()` | `ReasoningResult[InstanceTaxonomy]` | Instance nodes plus minimal direct types. |

`Taxonomy` holds `nodes`, transitively reduced `direct_edges` (sub → super), and
the `top` and `bottom` nodes. Satisfiable named classes are grouped by mutual
subsumption; unsatisfiable named classes join the bottom node. Helper methods:
`taxonomy.node(entity)` finds an entity's node, and `taxonomy.subs(entity)` /
`taxonomy.supers(entity)` return strict direct (`direct=True`) or transitive
neighbours.

`InstanceTaxonomy` holds the shared `class_taxonomy`, the `instances` nodes
(individuals grouped by derived equality), and `direct_types` pairs linking each
instance node to its minimal named type nodes.

## Class-expression queries

Queries accept any `pyowl_core` class expression. A supported complex expression is
saturated as a temporary query root without mutating the ontology.

| Method | Value | Direct semantics |
|---|---|---|
| `is_satisfiable(expr)` | `bool` | — |
| `equivalent_classes(expr)` | tuple of at most one `EntityNode[Class]` | — |
| `subclasses(expr, direct=False)` | node tuple | `direct=True`: maximal strict named subs; otherwise strict transitive closure. |
| `superclasses(expr, direct=False)` | node tuple | `direct=True`: minimal strict named supers; otherwise strict transitive closure. |
| `instances(expr, direct=False)` | node tuple | `direct=True` uses the realization minimal-type rule; otherwise includes instances of subclasses. |

`equivalent_classes` never returns two nodes: mutually equivalent named classes
collapse into one taxonomy node. The empty tuple reproduces ELK's unindexed-query
fallback for a complex expression with no named equivalent.

For an expression the pinned ELK converter cannot index, the operations return
ELK's exact unindexed-query values with query completeness reasons: satisfiability
is `True`, equivalent classes is empty, direct subclasses/superclasses are the
bottom/top node while the non-direct forms are empty, and instances are empty.

## Named-entity queries and enumeration

| Method | Value |
|---|---|
| `types(individual, direct=False)` | Named type nodes of one `NamedIndividual`; `direct=True` gives the minimal types, `direct=False` their upward closure including top. |
| `sub_object_properties(prop, direct=False)` | Strict direct or transitive subproperty nodes. |
| `super_object_properties(prop, direct=False)` | Strict direct or transitive superproperty nodes. |
| `equivalent_object_properties(prop)` | The property's `EntityNode[ObjectProperty]`, returned directly (a named property always has a node). |
| `all_classes()` | Committed named classes sorted by UTF-8 IRI, including `owl:Thing`/`owl:Nothing`. |
| `all_named_individuals()` | Committed named individuals, same order. |
| `all_object_properties()` | Committed named object properties, including top/bottom. |

Enumeration is available immediately after construction and does not force
saturation. Entities occurring only in ignored axioms are not committed and never
appear.

## Entailment

`is_entailed(axiom)` supports exactly eight axiom families: `SubClassOf`,
`EquivalentClasses`, `DisjointClasses`, `ClassAssertion`, `SameIndividual`,
`DifferentIndividuals`, `ObjectPropertyAssertion`, and `ObjectPropertyDomain`.

Any other axiom family returns `value=False` marked incomplete with the matching
`QUERY_*` feature (strict mode raises `UnsupportedQueryError`), following ELK's
unsupported-query adapter. A supported family containing an unindexable nested
construct also returns `False` with query reasons. Zero-member
`EquivalentClasses` and `SameIndividual` queries raise `ValueError` before backend
execution.

## Inconsistent ontologies

Value-returning operations bind to ELK's quiet inconsistent-ontology paths:

- `classify()` returns a single node containing every named class and no edges;
  `classify_object_properties()` collapses analogously;
- `realize()` has at most one instance node (all committed individuals) whose sole
  direct type is the collapsed class node;
- every expression is unsatisfiable, and query/type views return the collapsed
  values regardless of fresh-entity policy;
- every successfully indexed supported entailment query is `True` by classical
  explosion; unindexable queries remain `False`.

These quiet values carry no upstream feature reasons (the ignored-import policy
reason, when applicable, is retained). `is_consistent()` and `is_entailed()` keep
their ordinary completeness metadata.

## Backend selection and diagnostics

`pyelk.backend_report()` inspects the environment without creating a session. It
returns a `BackendReport` with the effective request, the selection, per-backend
`BackendAvailability` (including the native extension's version, IR version, ABI,
and any probe failure reason), a `selection_error`, and the captured pyowl-core
package/API/model/wire/adapter versions. Under `PYELK_PURE_PYTHON=1` the native
module is not probed and Rust availability is reported as `None` with a pure-mode
reason.

`reasoner.backend` returns the immutable per-session `BackendInfo`: backend `name`
(`"python"` or `"rust"`), `implementation_version`, IR `ir_major`/`ir_minor`,
`requested_workers` and `effective_workers`, `native_available`, and the retained
`fallback_reason` when `auto` selected Python.

`reasoner.diagnostics()` returns a sorted immutable mapping of scalars only — never
paths, pointers, or IR payloads. It always includes `ingestion_path`
(`scalar-python`, `scalar-wire`, or `encoded-native`), the canonical
`compiler_digest`, `compiler_cache_schema_version`, `ir_schema_version`,
`implementation_version`, `consumer_compile_seconds`, `materialized_scalar_rows`,
and the `encoded_*` buffer/copy/segment ledger (contractually zero/false on scalar
paths). Backends add stage, cache, and scheduler counters; encoded-native sessions
add view-publication and native phase durations.

## Exceptions

Input parsing, import resolution, malformed snapshots, and core resource limits
raise the corresponding `pyowl_core` exception classes; pyELK does not catch and
rebuild them. pyELK's own hierarchy, in `pyelk.exceptions`, starts at the
compiler/reasoner boundary:

| Exception | Raised when |
|---|---|
| `PyElkError` | Base class for all recoverable pyELK errors. |
| `UnsupportedFeatureError` | Strict compilation rejected an unsupported ontology feature; carries `feature` and `axiom`. |
| `UnsupportedQueryError` | Strict query compilation rejected an unsupported query; carries `feature` and `query`. |
| `FreshEntityError` | A query used fresh entities with `allow_fresh_entities=False`; carries the sorted `entities`. |
| `IncompleteReasoningError` | `require_complete()` was called on an incomplete result; carries the `reasons`. |
| `BackendUnavailableError` | An explicitly requested backend cannot be used; carries `requested` and `reason`. |
| `BackendProtocolError` | A compiled-IR or raw-result payload violated the frozen backend protocol. |
| `ReasonerClosedError` | An operation was attempted on a closed session. |
| `InternalReasonerError` | A backend failed internally; carries `stage`, `backend`, and `detail`. Rust panics are converted to this and never unwind into CPython. |

Memory exhaustion, `KeyboardInterrupt`, and process-level failures are never
wrapped.

## Determinism and backend equivalence

All public outputs are identical for the Python and Rust backends, for workers 1
and N, under any `PYTHONHASHSEED`, and across input permutations that produce
equal core fingerprints. Completeness metadata is part of that parity: a backend
returning the right edges with the wrong reasons is a bug, not a tolerance. The
Python backend is the semantic reference; the native extension must match it
exactly or fail its handshake.
