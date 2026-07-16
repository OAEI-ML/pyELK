# WP1 — Immutable OWL Object Model and Structural Keys

## Goal

Implement the backend-independent OWL values required for ELK-compatible reasoning and
lossless generic nodes for recognised unsupported constructors. Freeze equality, hashing,
predefined entities, logical/document structural keys, and Pythonic factories.

## Read first

| Source | Sections / symbols |
|---|---|
| `specs/parsing.md` | §§1–3, 7 |
| `specs/compatibility.md` | §§1–4 |
| `specs/contracts.md` | §§1–2, 7 |
| pinned `elk-owl-model/.../owl/interfaces` | relevant entities, expressions, axioms |
| pinned `elk-owl-implementation/.../implementation` | structural equality/factory behaviour |

## Depends on

WP0.

## Owned paths

```text
src/pyelk/owl/**
tests/unit/owl/**
tests/properties/test_owl_values.py
```

## Forbidden paths

Parser, `ontology.py`, compiled indexing/codec, reasoning, backend adapters, public facade,
Rust, build metadata, oracle/frozen upstream data.

## Deliverables

1. Base `IRI`, `OWLObject`, `Entity`, `ClassExpression`, `Axiom`, annotations, literals.
2. Required entity/expression/axiom values from `parsing.md` §§2.1–2.3.
3. `UnsupportedExpression`, `UnsupportedAxiom`, and `AnnotationAxiom` generic nodes.
4. Predefined top/bottom class/object-property constants.
5. Exact validation/coercion rules and frozen tuple fields.
6. Iterative flat-byte field/`structural_key`/`logical_axiom_key` walkers with deterministic
   UTF-8 ordering, correct ordered/unordered handling, annotation omission, and retained
   multiplicity; recursive dataclass-generated equality/hash is disabled.
7. Convenience factories and explicit `pyelk.owl.__all__`.
8. Unit/property tests for equality, hashes, Unicode, literals, arities, duplicate members,
   property-chain order, and hash-seed-independent keys.

## Acceptance criteria

1. Every construct marked C or P in `compatibility.md` is programmatically representable;
   every I construct can be represented by a generic node with its exact feature name.
2. Values are transitively immutable/hashable and survive pickle where supported by Python
   3.10+ without identity assumptions.
3. `DisjointClasses(A,A)` retains two member positions; property-chain reversal changes the
   key; commutative canonical keys ignore order but retain duplicate child keys.
4. Document keys include annotations while logical keys do not.
5. Plain/language/explicit-datatype literal keys and case preservation match the pinned ELK
   parser/object equality, including counterintuitive `rdf:PlainLiteral` spellings.
6. Public model imports no parser, indexer, backend, Java, rdflib, or native module.
7. Equality, hashing, and key generation on an expression deeper than Python's recursion
   limit complete without `RecursionError` and agree with shallow equivalents.
8. Full checks and the forced-pure suite pass; only owned paths changed.
