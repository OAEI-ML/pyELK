# pyELK — Master Specification

pyELK is a Python reimplementation of the core reasoning behaviour of
[ELK Reasoner 0.6.0](https://github.com/liveontologies/elk-reasoner/releases/tag/v0.6.0).
It has no Java runtime dependency. Every supported operation has a complete pure-Python
implementation and, when available, a high-performance Rust implementation selected
behind the same backend contract.

The installable distribution is `pyelk-reasoner`; its Python import namespace is `pyelk`.
The distinct distribution name avoids the unrelated project already published as `pyelk`
on PyPI without changing the public Python API.

This document is the project constitution. Detailed behaviour is normative in the linked
subsystem specifications. The words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are used
normatively.

The document hierarchy adapts the master-specification, subsystem, and bounded-work-package
pattern used by [PyLogMap](https://github.com/city-artificial-intelligence/PyLogMap). pyELK's
contracts, ownership boundaries, and parity gates are specific to this reasoner.

## 1. Frozen reference

The compatibility target is ELK `v0.6.0`, commit
[`b8ac5ce83db0704a7359d96aa382891e2f547863`](https://github.com/liveontologies/elk-reasoner/commit/b8ac5ce83db0704a7359d96aa382891e2f547863),
Git tree `9becd9e41eac6434a1e247c2a9b19644cdd9d27a`. GitHub identifies 0.6.0 as
the latest stable release as checked on 2026-07-16. The machine-readable pin and oracle
configuration are in [`baseline.toml`](baseline.toml).

The upstream source is a behavioural and algorithmic reference, not a runtime component.
References in these specifications always mean the pinned commit, even when a short path is
shown. An implementation MUST NOT silently follow `main` or a newer ELK release.

The logical basis is OWL 2 Direct Semantics and the
[OWL 2 EL profile](https://www.w3.org/TR/owl2-profiles/#OWL_2_EL), but compatibility is
with ELK's actual 0.6.0 fragment and incompleteness reporting. ELK does not completely
implement every construct admitted by the W3C EL grammar. The exact boundary is specified
in [`compatibility.md`](compatibility.md).

## 2. Goals

1. **Exact semantic parity.** For every operation and input inside the compatibility
   boundary, pyELK and the pinned Java oracle produce equal canonical results, including
   directness, top/bottom nodes, inconsistency behaviour, fresh-entity behaviour, and
   potential-incompleteness reasons.
2. **No Java dependency.** Installation, import, parsing, and reasoning work without a JRE,
   Maven, JVM bridge, subprocess, downloaded JAR, or network access. Java is permitted only
   in the opt-in fixture regeneration and differential-test tooling.
3. **Always-working fallback.** `pip install pyelk-reasoner` and installation from an sdist
   succeed without a C or Rust compiler. The pure-Python backend implements every in-scope
   operation; it is not a feature-reduced emergency path.
4. **Maximum practical performance.** Supported platform wheels contain a Rust/PyO3
   backend that owns whole reasoning sessions, releases Python while computing, and may
   use all configured cores. FFI is coarse-grained; Python callbacks are forbidden in hot
   loops.
5. **One public API.** Backend choice cannot change values, types, exceptions, completeness
   metadata, or deterministic ordering.
6. **Parallel implementation.** Stable contracts, owned paths, dependency waves, and exact
   acceptance gates allow independent agents to implement work packages without editing
   the same files.

## 3. In scope

- Immutable OWL object values and factories needed by ELK's native object model.
- Streaming OWL 2 Functional-Style Syntax parsing and canonical printing.
- Annotation acceptance with no logical effect, as in ELK core.
- Polarity-aware indexing and normalisation.
- Object-property hierarchy and property-chain saturation.
- Consequence-based class saturation and consistency checking.
- Class and object-property classification.
- ABox realisation, same/different-individual behaviour, and type/instance queries.
- Complex class-expression satisfiability, equivalence, subclass, superclass, type, and
  instance queries supported by ELK 0.6.0.
- Entailment for exactly the eight axiom families listed in `compatibility.md`.
- ELK-compatible unsupported-feature occurrence tracking and task-specific completeness.
- Java-oracle generation, frozen parity fixtures, backend differential tests, performance
  benchmarks, and binary-wheel release automation.

## 4. Explicit non-goals

- Incremental addition/removal and incremental taxonomy repair.
- Proof objects, tracing, explanations, justifications, or proof plug-in extension points.
- OWL API, Protégé, ORE, Java-compatible class names, or a Java-style visitor surface.
- A command-line application or server.
- RDF/XML, Turtle, OWL/XML, Manchester Syntax, network imports, catalog resolution, or
  ontology fetching in version 1. Functional Syntax and programmatic construction are the
  core ingestion surfaces. Later format adapters MUST compile to the same public model.
- Complete OWL 2 DL reasoning or silently improving ELK's known incomplete cases.
- Byte-for-byte reproduction of Java serialisation, log messages, hash iteration order,
  timing, thread scheduling, exception wording, or internal proof/conclusion identities.

## 5. Meaning of “exact same behaviour”

Semantic containers in Java are frequently unordered. Exact parity therefore means equality
after the canonicalisation in [`verification.md`](verification.md), not matching Java's
iteration order. No tolerance or similarity threshold is allowed for reasoning results.

Parity covers:

- parsed logical objects and ignored annotations;
- accepted, partially indexed, and wholly ignored constructs;
- inferred value plus completeness flag and feature reasons;
- ontology consistency and expression satisfiability;
- equivalence nodes and direct taxonomy edges;
- direct and transitive class/property/instance query answers;
- supported entailment answers;
- behaviour for empty ontologies, inconsistent ontologies, duplicate/permuted axioms,
  cycles, top/bottom entities, and fresh entities.

Ordering is a pyELK API guarantee: user-visible sequences are sorted by canonical structural
key. This is deliberately stronger than ELK's set iteration and is not considered a semantic
difference.

## 6. Repository layout

```text
pyELK/
├── pyproject.toml
├── setup.py                         # only optional RustExtension declaration
├── Cargo.toml
├── rust/                            # native backend; one PyO3 module
├── src/pyelk/
│   ├── __init__.py                  # small public export surface
│   ├── api.py                       # Ontology and Reasoner facade
│   ├── ontology.py                  # immutable document + parse/print entry points
│   ├── config.py                    # frozen ReasonerConfig
│   ├── result.py                    # public nodes, taxonomies, ReasoningResult
│   ├── exceptions.py
│   ├── owl/                         # immutable OWL object model and factories
│   ├── parsing/                     # Functional Syntax lexer/parser/printer
│   ├── indexing/                    # polarity conversion + canonical compiled IR
│   ├── reasoning/
│   │   ├── contracts.py             # backend/session protocol; frozen first
│   │   ├── completeness.py
│   │   ├── properties.py
│   │   ├── conclusions.py
│   │   ├── contexts.py
│   │   ├── rules.py
│   │   ├── saturation.py
│   │   ├── taxonomy.py
│   │   ├── realization.py
│   │   └── queries.py
│   └── backends/
│       ├── __init__.py              # dispatcher and diagnostics
│       ├── python.py                # complete reference BackendSession
│       └── rust.py                  # thin adapter over pyelk._native
├── tools/java-oracle/               # dev-only Maven executable; excluded from wheels
├── tests/
│   ├── unit/
│   ├── properties/
│   ├── parity/
│   ├── backends/
│   ├── packaging/
│   └── data/elk-v0.6.0/
├── benchmarks/
└── specs/
```

`setup.py` is allowed only because setuptools' declarative TOML cannot express every
conditional pure/native wheel build reliably. It MUST contain no package behaviour.

## 7. Import and ownership rules

```text
owl
├── parsing -> owl
├── ontology -> owl, parsing
├── indexing -> owl, ontology
└── result -> owl, reasoning.contracts

reasoning.contracts -> indexing
reasoning.{completeness,properties,conclusions,contexts,rules,saturation} ->
    reasoning.contracts, indexing
reasoning.{taxonomy,realization,queries} -> reasoning core, result, owl
backends.python -> reasoning core
backends.rust -> reasoning.contracts, _native
backends dispatcher -> both backend adapters
api -> parsing, indexing, backends, result, config
```

- `owl`, `parsing`, `indexing`, and public result values MUST NOT import a backend.
- No Python reasoning module may import `pyelk._native`.
- Rust and Python consume the same frozen `CompiledOntology` and implement the same
  `BackendSession` protocol.
- Public API code MUST NOT branch on backend except through the dispatcher.
- Test and oracle code MUST NOT be imported by `src/pyelk`.
- The import graph is enforced by `import-linter` from WP0 onward.

Each work package owns explicit paths. Agents MUST NOT make “helpful” edits to another
package's owned paths or shared integration files. Cross-package changes are proposed as a
contract issue and applied by the owning or integration work package.

## 8. Pipeline

```text
Functional Syntax / Python objects
  -> immutable Ontology
  -> feature scan + polarity-aware axiom conversion
  -> deterministic CompiledOntology
  -> object-property closure
  -> context saturation + consistency
  -> class/property taxonomy and realization
  -> query/entailment views
  -> canonical ReasoningResult[T]
```

Stages are lazy at the facade: a consistency query need not build every taxonomy. Within a
backend session, each stage is monotone and memoised. Because v1 has no incremental updates,
an `Ontology` is snapshotted when a `Reasoner` is created and the session is immutable.

## 9. Backend policy

Rust with PyO3 is the native implementation. The reason is architectural rather than
syntactic: Rust provides safe compact graph storage and work-stealing parallelism while
PyO3 and the stable CPython ABI permit a small, coarse-grained binding and a manageable wheel
matrix. The native engine MUST own full property and class saturation sessions; accelerating
individual set operations while retaining a Python scheduler is insufficient.

Selection first chooses a request from an explicit non-`auto` `ReasonerConfig.backend`, then
`PYELK_BACKEND=rust|python|auto`, then `auto`. `PYELK_PURE_PYTHON=1` is a hard
test/deployment guard applied to that request: it selects Python for `auto`/`python` and
rejects an effective `rust` request as a configuration conflict without importing native
code.

Explicit effective `rust` raises `BackendUnavailableError` if the extension cannot load;
`auto` falls back without losing functionality. `backend_report()`
reports the effective environment request, selected implementation, both backend
availabilities, version/ABI information, and any fallback or selection error. A live
`Reasoner.backend` additionally reports requested/effective workers.

## 10. Global invariants

1. Both backends return structurally equal public values for every generated valid input.
2. Results are independent of axiom order, operand order where OWL semantics is unordered,
   hash seed, backend, and worker count.
3. Every derived relation is monotone within an immutable session; duplicate conclusions
   are inserted at most once.
4. Every entity/expression/property ID is session-local, unsigned 32-bit, and never exposed
   as a public identity.
5. Public values retain IRIs, not internal numeric IDs.
6. An unsupported nested constructor causes the whole containing axiom to be ignored after
   reversible conversion, matching ELK 0.6.0, and records the feature occurrence.
7. A result marked complete MUST be sound and complete for ELK's target procedure. A known
   incomplete feature MUST never be silently labelled complete.
8. The Python wheel and sdist contain no Java binaries and make no network access.
9. The pure-Python test suite runs in an environment with `PATH` containing neither Java,
   Cargo, nor a C compiler.

## 11. Coding and quality rules

- Python 3.10+, `src/` layout, public type hints, `py.typed` marker.
- `ruff format`, `ruff check`, strict `mypy`, `pytest`, `hypothesis`, and `import-linter`.
- Immutable slotted dataclasses for public OWL/result values; no module-level mutable
  reasoner state.
- Iterative graph algorithms in both backends; ontology depth MUST NOT consume Python or
  Rust call stack.
- No required NumPy dependency. Compact buffers use stdlib `array`, `bytes`, and
  `memoryview`; the native adapter may copy once when creating its session.
- No `print` in library code; use `logging` only for diagnostics. Completeness is data, not
  a log-only warning.
- Every bug fix starts with a minimal fixture that fails against the affected backend and
  records whether Java agrees.
- Apache-2.0 attribution and modified-file notices are required for translated code or
  copied upstream fixtures. See
  [`verification.md`](verification.md#11-provenance-and-licensing).

## 12. Specification map

| Document | Normative responsibility |
|---|---|
| [`compatibility.md`](compatibility.md) | language boundary, partial support, completeness, operation parity |
| [`contracts.md`](contracts.md) | public API, compiled IR, backend/session and result types |
| [`parsing.md`](parsing.md) | OWL object values, Functional Syntax parser/printer |
| [`indexing.md`](indexing.md) | polarity conversion, interning, deterministic normalised IR |
| [`saturation.md`](saturation.md) | property closure, conclusions, rules, scheduling, consistency |
| [`taxonomy-queries.md`](taxonomy-queries.md) | classification, realisation, directness, query semantics |
| [`native-packaging.md`](native-packaging.md) | Rust design, dispatcher, wheels, compiler-free fallback |
| [`verification.md`](verification.md) | Java oracle, canonicalisation, test corpus, benchmarks, release gates |
| [`traceability.md`](traceability.md) | upstream-to-spec-to-work-package ownership |
| [`workpackages/README.md`](workpackages/README.md) | parallel execution handout and dependency waves |

## 13. Change control

Compatibility exceptions are permitted only when a Java 0.6.0 result cannot be reproduced
without violating Python safety or a stated project goal. Each exception requires a minimal
fixture, oracle output, rationale, and an entry in `compatibility.md`. “Close enough” is not
an exception category.

Changing the ELK baseline is a separate project milestone: update `baseline.toml`, regenerate
all fixtures, classify every diff, and version the compatibility contract. Implementers MUST
not mix behaviours from `main`, 0.4.x, or later unreleased commits into the 0.6.0 target.
