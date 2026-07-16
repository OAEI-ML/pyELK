# WP8 — Pure Class/Object-Property Taxonomy and Transitive Reduction

## Goal

Build canonical class and object-property equivalence nodes and direct edges from the pure
saturation/property closures, including bottom unsatisfiability, inconsistent collapse, and
an efficient equivalence-aware transitive reduction. Return validated raw taxonomy records;
the public facade is later.

## Read first

| Source | Sections / symbols |
|---|---|
| `specs/taxonomy-queries.md` | §§1–3, 5, 10–11 |
| `specs/contracts.md` | §§5, 8 |
| pinned `taxonomy`, `reduction` | class/property computation and reduction |
| pinned taxonomy tests/goldens | class and property families |

## Depends on

WP7.

## Owned paths

```text
src/pyelk/reasoning/reduction.py
src/pyelk/reasoning/taxonomy.py
tests/unit/reasoning/test_reduction.py
tests/unit/reasoning/test_taxonomy.py
tests/properties/test_taxonomy.py
benchmarks/bench_taxonomy.py
```

## Forbidden paths

Saturation/property/indexing/contracts, realization/query, backend adapters/public values,
Rust, completeness, parser, packaging, oracle data.

## Deliverables

1. Mutual-subsumption quotient for named classes and singleton object-property chains.
2. Top/bottom grouping, unsatisfiable class handling, ignored-entity exclusion, and
   inconsistent single-node shape.
3. Equivalence-aware direct-edge transitive reduction with bottom-to-leaf/top reachability.
4. Canonical `RawTaxonomy` construction and invariant validator integration.
5. Slow graph oracle in tests, upstream taxonomy test ports, random preorder properties,
   and sparse/dense benchmarks.

## Acceptance criteria

1. All 66 class and 11 object-property frozen taxonomies are exactly reproduced through the
   internal raw canonical form when run with the completed pure saturation branch.
2. Random preorders exactly match slow quotient/reduction; no emitted direct edge is
   transitively redundant.
3. Cycles, diamonds, multiple roots/leaves, top/bottom equivalents, unsatisfiable classes,
   empty ontology, and inconsistent ontology have explicit tests.
4. Every committed entity appears once; ignored-only entities never appear; every consistent
   node lies on a bottom-to-top path.
5. Generated sparse 100k-node taxonomy avoids an all-triples algorithm and recursion.
6. Only raw/backend-neutral records are returned; no public facade/native or forbidden edit.
