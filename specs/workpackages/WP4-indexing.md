# WP4 — Polarity-Aware Indexing and Deterministic IR

## Goal

Compile immutable OWL values into the frozen backend-neutral IR with ELK 0.6.0's exact
entity/expression support, polarity, axiom conversion, transactional rollback, occurrence
counts, rule-registration metadata, and deterministic IDs.

## Read first

| Source | Sections / symbols |
|---|---|
| `specs/indexing.md` | all |
| `specs/compatibility.md` | §§2–6 |
| `specs/contracts.md` | §§7, 9 |
| pinned entity/expression/axiom converters | complete source |
| pinned `indexing/classes` and `indexing/model` | structural/occurrence semantics |

## Depends on

WP1 and WP3 (plus WP0 transitively).

## Owned paths

```text
src/pyelk/indexing/__init__.py
src/pyelk/indexing/builder.py
src/pyelk/indexing/compiler.py
src/pyelk/indexing/conversion.py
src/pyelk/indexing/polarity.py
src/pyelk/indexing/registration.py
tests/unit/indexing/**                 # except WP0 codec tests
tests/properties/test_indexing.py
tests/data/manifests/registration.toml
```

## Forbidden paths

WP0 `ir.py`/`codec.py` contracts, OWL/parser/completeness, reasoning engine/taxonomy/query,
backends, Rust, facade, build/oracle data.

## Deliverables

1. Per-axiom transaction builder with complete rollback and strict/default modes.
2. Positive/negative/dual expression visitors and exact feature increments.
3. Structural interning and all simplifications/binarisation from `indexing.md`.
4. Axiom conversion tables for class, disjointness/individual, property, range/reflexive,
   and partial cases.
5. Occurrence-driven rule-registration manifest/data consumed later by both engines.
6. Deterministic freeze/rewrite/sort into WP0 records and source fingerprint.
7. Query-expression and entailment `CompiledQuery` builders, including exact local feature
   counts, complete fresh-entity enumeration, and transactional `encoded=None` results for
   unindexable queries.
8. Validation, golden conversion rows, and property tests.

## Acceptance criteria

1. Every row of the compatibility expression/axiom matrix has a minimal test asserting
   conversion rows, committed entities, feature counts, and complete rollback when ignored.
2. Java and Python feature counts/entity enumeration/conversion-observable results agree for
   the feature corpus.
3. Disjoint threshold 2, duplicate member positions, 0/1/n cases, equivalence orientation,
   left-associated intersections, right-built property chains, and partial `DisjointUnion`
   match the pinned converter.
4. Encode output for one ontology is byte-identical over repeated compiles, Python 3.10/latest,
   and multiple hash seeds.
5. Random compiled IR passes WP0 validation; compile → encode → decode preserves records.
6. Ignored-only entities/rules never appear; annotations never change logical rows/fingerprint.
7. No reasoning rule is executed in indexing and no forbidden path changed.
