# Verification, Oracle, and Release Gates

Exact compatibility is a testable deliverable, not an aspiration. This document defines the
Java oracle, canonical fixture schema, upstream corpus, backend differential tests, property
tests, benchmarks, provenance, and release gates.

## 1. Test layers

| Layer | Java? | Purpose |
|---|---:|---|
| unit | no | core identity/adapters, conversions, rules, taxonomy/query helpers |
| exhaustive tiny oracle | no | slow independent fixed point / graph algorithms on bounded random inputs |
| upstream frozen corpus | no | ELK 0.6.0 inputs and checked-in canonical expected JSON |
| Java differential | yes, opt-in | regenerate fixtures and test new/random cases against pinned ELK |
| backend differential | no | complete Python versus Rust equality on the same generated IR |
| metamorphic/fuzz | no/optional Java | order, duplicate, overlay, worker, hash seed, adapter/decoder robustness |
| packaging | no | installed wheels/sdist, fallback, ABI/dependency audits |
| performance | Java for comparison | stage time, throughput, scaling, RSS, regression gates |

Ordinary `pytest` and every installed-wheel smoke suite require no Java and make no network
access. Tests that invoke Java are marked `@pytest.mark.java_oracle`; benchmarks are marked
separately.

## 2. Java oracle

`tools/java-oracle/` is a small Maven application excluded from all distributions. It pins:

```text
ELK version:       0.6.0
ELK commit:        b8ac5ce83db0704a7359d96aa382891e2f547863
OWL API (adapter): 5.1.20
JDK fixture lane:  Eclipse Temurin 17
workers:           1 unless the test explicitly varies workers
incremental:       false
fresh entities:    allowed
unsupported:       IGNORE
```

The harness uses ELK's native Functional Syntax parser/core reasoner where practical. The
OWL API adapter is allowed only inside this dev tool for loading/query bridging; an oracle
test must record which path it used.

### 2.1 Protocol

The executable reads one JSON request per line and writes one JSON response per line. A
request contains:

```json
{
  "schema": 1,
  "id": "stable-test-id",
  "ontology_path": "absolute-or-workdir-relative.ofn",
  "operation": "class_taxonomy",
  "arguments": {},
  "configuration": {"workers": 1, "allow_fresh_entities": true}
}
```

Supported operations cover every public `Reasoner` method plus structural load observations,
`feature_counts`, and diagnostic `saturation_counts`. pyowl-core owns parser conformance;
pyELK compares the core snapshot-to-ELK conversion rather than implementing a second parser.
Responses contain either:

```json
{
  "schema": 1,
  "id": "stable-test-id",
  "ok": true,
  "value": {},
  "complete": true,
  "features": [],
  "diagnostics": {}
}
```

or a canonical error category with source span/query feature. Java stack traces go to a
separate diagnostics file and never contaminate JSON stdout.

The public ELK incompleteness API directly provides the boolean monitor but not a complete
structured reason list. The oracle bridge therefore also exposes the pinned occurrence
registry using a package-local test bridge or narrowly scoped reflection. The bridge is
version-locked and unit-tested against the enum manifest; reflection failure aborts fixture
generation rather than writing partial metadata.

The oracle maps value operations to the exact core paths in `compatibility.md` §7.1. In
particular, taxonomy, realisation, satisfiability, class-relation, and instance fixtures use
ELK's quiet methods; they do not go through the exception-throwing OWL API adapter. On an
inconsistent ontology the bridge records the quiet result's actual no-incompleteness monitor
and canonicalises the single all-individual instance node rather than inferring either from
the Functional Syntax golden printer.

### 2.2 Reproducibility

`python tools/oracle.py regenerate`:

1. verifies `baseline.toml` and the Maven dependency versions;
2. builds the harness in an isolated cache;
3. canonicalises every input and computes SHA-256;
4. starts one oracle process with the pinned configuration;
5. requests all applicable operations;
6. canonicalises responses and compares any existing fixture;
7. writes through a temporary directory only after the complete run succeeds;
8. emits `manifest.json` with tool/source/runtime hashes and the classified diff.

Fixture regeneration is never an implicit test side effect.

## 3. Canonical JSON schema

All maps use sorted keys; JSON is UTF-8, two-space-indented, and ends with one newline.
Floating point is absent from logical results.

### 3.1 Nodes and taxonomies

```json
{
  "nodes": [
    ["http://example/A", "http://example/B"],
    ["http://www.w3.org/2002/07/owl#Thing"]
  ],
  "direct_edges": [[0, 1]],
  "top": 1,
  "bottom": 2
}
```

Members in each node are sorted by UTF-8 IRI; nodes are sorted by their member tuple;
edges are rewritten to sorted node indices and sorted lexicographically. Instance taxonomy
adds `instance_nodes` and sorted `(instance_node_index, class_node_index)` direct types.

### 3.2 Query results

- booleans are JSON booleans;
- entity/node collections use sorted member arrays;
- completeness reasons use exact feature enum names and sorted combination arrays;
- parse errors contain category, one-based line/column, and offending token kind;
- internal IDs, Java class names, hash codes, logs, durations, and memory addresses are
  forbidden in semantic fixture values.

Two results are equal only if canonical JSON values, completeness, and error category/span
are exactly equal. There is no numeric tolerance, count window, Jaccard threshold, or ignored
edge class.

## 4. Upstream corpus

Copy the pinned resources from
`elk-reasoner/src/test/resources/test_input` into
`tests/data/elk-v0.6.0/upstream/` with provenance. The current corpus contains:

| Family | Ontology inputs | Golden use |
|---|---:|---|
| class classification | 66 | taxonomy nodes/edges and inconsistency |
| object-property classification | 11 | property nodes/edges and incompleteness |
| realisation | 5 | individual nodes and direct types |
| class query | 26 | satisfiability/equivalent/sub/super/instance queries |
| entailment | 16 groups | 16 positive and 14 negative expected-query files |
| **total** | **124 Functional Syntax ontologies** | **138 upstream golden outputs** |

The checked-in pyELK expected form is canonical JSON generated from Java, not a custom parser
of upstream output files alone. Upstream `.taxonomy`, `.propertytaxonomy`, `.instancetaxonomy`,
`.classquery`, `.entailed`, and `.notentailed` files are retained as provenance and an
independent cross-check.

Also port relevant Java test logic for:

- index construction and unsupported rollback;
- property-chain saturation;
- context/link/saturation invariants;
- concurrent saturation/order independence;
- taxonomy acyclicity, node consistency, and transitive reduction;
- fresh entities and complex class queries.

## 5. Additional conformance corpus

Maintain `tests/data/w3c/manifest.toml` for approved OWL 2 EL tests from the W3C suite. Each
entry records source URL, immutable input hash, W3C status, expected Direct-Semantics result,
ELK 0.6.0 result, and one of:

```text
elk-complete
elk-incomplete-as-designed
outside-pyowl-core-input-scope
```

W3C correctness never overrides the compatibility mode's Java result. A future enhanced EL
mode may expose broader conformance under a different explicit configuration.

## 6. Generated and metamorphic tests

### 6.1 Grammar/model generation

Hypothesis strategies generate bounded valid values over:

- classes, individuals, and object properties;
- top/bottom;
- intersections, existentials, has-value, has-self, nominals;
- supported polarity-safe complement/union cases;
- subclass/equivalence/disjointness;
- property hierarchy, chains, ranges, reflexivity, transitivity;
- assertions and same/different individuals;
- each unsupported/partial feature and feature combination.

Tiny random ontologies are evaluated by the Python engine, Rust engine, slow exhaustive
interpreter, and—on Java lanes—the oracle.

### 6.2 Metamorphic relations

For every applicable test, logical output must remain equal after:

- permutation of ontology axioms;
- permutation of semantically unordered operands, retaining multiplicity;
- addition/removal of duplicate logical axioms and axiom annotations;
- prefix renaming and canonical full-IRI printing;
- repeated calls and different query order;
- `PYTHONHASHSEED` changes;
- Python versus Rust;
- Rust workers 1, 2, and N;
- insertion of declarations for already occurring entities.
- replacing a path input with its already loaded snapshot or a no-op overlay;
- equivalent import traversal/resolver order with the same core closure fingerprints.

Relations expected to change results, such as property-chain reversal or removal of one
duplicate position in `DisjointClasses(A,A)`, have explicit negative controls.

## 7. Backend differential gates

All non-packaging tests are parameterised over the Python backend. Rust-enabled CI runs the
same suite over Rust and a third differential parametrisation that executes both and compares
canonical values.

Required equality includes:

- compile-independent public enumeration;
- consistency;
- class/property taxonomy;
- realisation;
- every direct/transitive query;
- all supported/unsupported entailment values;
- completeness reasons;
- error categories;
- debug saturation snapshot on bounded generated cases.

Native-only optimisations land behind these tests. A native mismatch cannot be waived as a
performance trade-off.

## 8. Input-adapter and decoder fuzzing

- Consume pyowl-core's released parser/fingerprint conformance corpus and fuzz findings as a
  dependency gate; do not copy its parser implementation or duplicate its full fuzz target.
- Fuzz pyELK coercion with malformed/incompatible providers, core wire versions, overlays,
  closed/erroring streams, and hostile resolver results; outcomes are core input errors or a
  valid captured snapshot, never a retry/reparse, crash, hang, or unbounded copy.
- Fuzz Python and Rust IR decoders with valid mutated/truncated bytes.
- Seed adapter/compiler cases with every upstream input and IR codec golden.
- Run Rust `cargo fuzz`/libFuzzer in scheduled CI and short deterministic smoke fuzzing on
  pull requests.
- Preserve every discovered crash or semantic mismatch as a minimal regression fixture.

## 9. Performance methodology

`benchmarks/manifest.toml` pins every corpus by name, source/licence, SHA-256, axiom/entity
counts, and redistribution policy. Default checked-in benchmarks use generated ontologies and
redistributable upstream resources. Large external biomedical corpora are cached by hash and
optional when their licence prevents redistribution.

Measure separately:

```text
pyowl-core load/parse/import closure (standalone only)
snapshot/provider capture (shared mode)
compile/index
property saturation
consistency/class saturation
taxonomy
realisation
first complex query
cached query
end-to-end
peak RSS
derived conclusion/context counts
```

Protocol:

- dedicated machine or labelled runner, fixed power mode, no concurrent jobs;
- record CPU, cores, RAM, OS, Python, Rust, Java, and git commits;
- at least two warm-up runs and five measured runs;
- report median, MAD, minimum, and geometric mean across corpora;
- compare the same canonical Functional Syntax and operations;
- separate Java process/JIT startup and parser time from warm reasoner time;
- workers 1 and N for Java/Rust; pure Python uses one;
- assert semantic fixture equality before accepting a timing sample.

Initial thresholds are in `native-packaging.md`. Check in machine-specific baselines rather
than comparing unrelated CI hardware. Pull-request regression uses a stable runner and fails
on >10% median regression confirmed by a rerun.

## 10. Packaging tests

Test artifacts, not the source checkout:

- universal wheel in a compiler/JRE-free environment;
- native wheel with auto/Rust/forced-Python selection;
- sdist with Cargo absent and present;
- each tier-one platform and every supported CPython minor for ABI3;
- offline import and representative reasoning fixture;
- metadata/type-hint/Python-file equality across wheel variants;
- no Java archive/class/launcher or unapproved shared dependency;
- exact `pyowl-core>=0.1,<0.2` metadata, core-version diagnostics, and pure/native core ×
  pure/native pyELK combinations where wheels exist;
- `pip --only-binary` wheel preference from a local simple index containing both variants.

## 11. Provenance and licensing

ELK 0.6.0 is Apache-2.0. Copied or translated upstream fixtures/source require:

- the project `LICENSE` in source and distributions;
- retained applicable copyright/attribution headers;
- a prominent modified/translated notice in derived files;
- `tests/data/elk-v0.6.0/UPSTREAM.toml` containing repository, tag, full commit, original
  paths, hashes, copy date, licence, and modifications;
- a generated `NOTICE.pyelk` attribution file if copied material is shipped in an sdist.

The pinned ELK tree has no root NOTICE payload to reproduce. Do not call the project
“clean-room” while implementers read/translate Java source. This is an independent
Apache-licensed reimplementation with explicit provenance. This section is engineering
guidance, not legal advice.

## 12. Definition of done

A release candidate is acceptable only when:

1. all upstream frozen corpus comparisons are exact on pure Python;
2. all comparisons are exact on every native wheel;
3. Java regeneration produces no unclassified semantic/completeness diff;
4. generated/exhaustive/property tests pass;
5. import/type/lint/format checks pass;
6. input-adapter/decoder fuzz smoke tests pass, the supported pyowl-core conformance gate
   passes, and scheduled fuzz has no open crash;
7. wheel/sdist matrix and no-Java tests pass;
8. native performance meets the current thresholds with results attached;
9. every compatibility feature and upstream reference is mapped in `traceability.md`;
10. there are no undocumented compatibility exceptions; and
11. view/provider inputs prove zero reparsing and zero public-model copies; standalone
    path/stream and shared Exact-OM-style snapshot/overlay/composite inputs return identical
    results.
