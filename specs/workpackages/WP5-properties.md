# WP5 — Pure-Python Object-Property Saturation

## Goal

Implement the complete deterministic pure-Python closure for object-property hierarchy,
complex chains, transitivity, composition side maps, and inherited ranges. This becomes the
semantic oracle for Rust property saturation.

## Read first

| Source | Sections / symbols |
|---|---|
| `specs/saturation.md` | §§1–2 |
| `specs/indexing.md` | §§5.4, 6–9 |
| pinned `saturation/properties` | all non-incremental classes |
| pinned property inference tests | all |

## Depends on

WP4.

## Owned paths

```text
src/pyelk/reasoning/properties.py
tests/unit/reasoning/test_properties.py
tests/properties/test_property_saturation.py
tests/data/manifests/property-inferences.toml
benchmarks/bench_properties.py
```

## Forbidden paths

Contracts/IR/indexing, other reasoning modules, backend adapters, Rust, facade, packaging,
oracle/frozen data/manifests outside owned tests.

## Deliverables

1. Immutable `PropertySaturation` result/view and mutable builder hidden from callers.
2. Tautology, subproperty expansion, range inheritance, and chain prefix/suffix composition.
3. Duplicate-suppressed semi-naive agenda with compatible-premise indices.
4. Cycles/equivalence, shared chains, repeated/transitive properties, reflexivity metadata,
   top/bottom, and range inheritance handling.
5. A deliberately slow exhaustive closure in the test module only.
6. Upstream property test ports, random differential properties, and benchmarks.
7. Property-inference manifest mapping every pinned concrete property inference to the
   Python symbol and its positive test.

## Acceptance criteria

1. Manifest coverage finds no unclassified concrete property inference; every row resolves
   to a Python symbol and positive minimal test, with missing-premise negative coverage.
2. Random tiny hierarchies/chains/ranges exactly equal exhaustive fixed point under at least
   10,000 Hypothesis cases in the extended lane.
3. All upstream property-chain saturation fixtures and object-property classification input
   prerequisites pass at the closure level.
4. Output is identical under axiom/agenda permutations and repeated calls.
5. A sparse 100k-property generated chain test completes without recursion or quadratic
   all-pairs rescanning; benchmark results are recorded, not compared across machines yet.
6. No taxonomy/public API or native code is introduced and only owned paths changed.
