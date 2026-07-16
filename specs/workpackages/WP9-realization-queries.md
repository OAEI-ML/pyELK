# WP9 — Pure Realisation, Complex Queries, and Entailment

## Goal

Implement backend-neutral pure algorithms for same-individual nodes/direct types, complex
class-expression queries, named entity queries, and exactly the eight supported entailment
families. Operate on saturation and raw-taxonomy views so this WP can run parallel with WP8.

## Read first

| Source | Sections / symbols |
|---|---|
| `specs/taxonomy-queries.md` | §§4, 6–11 |
| `specs/compatibility.md` | §§7–10 |
| `specs/contracts.md` | §§4–5, 8 |
| pinned `query`, supported `entailments`, instance taxonomy computation | observable paths |
| pinned class-query/entailment/realisation tests | all non-incremental cases |

## Depends on

WP7. WP8 is intentionally not a code dependency; accept `RawTaxonomy`/taxonomy-view inputs.

## Owned paths

```text
src/pyelk/reasoning/realization.py
src/pyelk/reasoning/queries.py
src/pyelk/reasoning/entailment.py
tests/unit/reasoning/test_realization.py
tests/unit/reasoning/test_queries.py
tests/unit/reasoning/test_entailment.py
tests/properties/test_queries.py
```

## Forbidden paths

Taxonomy implementation, saturation/indexing/contracts, completeness/public result/facade,
backend adapters, Rust, parser, packaging, oracle data.

## Deliverables

1. Same-individual quotient, minimal direct types, inverse instance views, and the
   inconsistent one-all-individual-node shape (or no node when there are no individuals) as
   raw records.
2. Named class/property/individual direct/transitive query helpers over raw views.
3. Complex query mini-IR saturation orchestration and equivalence/sub/super/instance
   selection/directness.
4. Decision functions for the eight supported entailment axiom families, explosion for
   successfully indexed queries, and false for unindexable queries even under inconsistency.
5. Unsupported entailment decision `False` plus query feature metadata hook (completeness
   attachment remains WP10).
6. Fresh-entity semantic helpers; policy validation remains WP10.
7. Unit, exhaustive tiny, metamorphic, and translated upstream query/realisation tests using
   fake/raw taxonomy views.

## Acceptance criteria

1. Five realisation families, 26 class-query families, and all positive/negative supported
   entailment cases match frozen expected values after integration with a raw taxonomy view.
2. Same/different individuals, no-UNA, direct versus transitive types/instances, fresh values,
   unsatisfiable expressions, and inconsistent explosion have explicit tests.
3. Every supported axiom type has positive, negative, converter-defined empty/one/n-ary, and
   inconsistent tests; zero-member equivalent/same queries are rejected, and every
   unsupported query type returns false with its exact feature hook.
4. Random tiny queries exactly match a slow semantic selector over exhaustive saturation.
5. Query-only expressions/entities never enter ontology enumeration/taxonomy and repeated
   query caching does not change results.
6. No import of WP8 implementation, public facade, native module, or forbidden edit.
