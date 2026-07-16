# WP7 — Pure-Python Saturation Scheduler and Consistency

## Goal

Assemble compiled indexing, property closure, contexts, and rules into the complete
pure-Python fixed-point engine. Implement lazy stage progression, demanded-root saturation,
cross-context scheduling, debug snapshots, query-root support, and ontology consistency.

## Read first

| Source | Sections / symbols |
|---|---|
| `specs/saturation.md` | all |
| `specs/contracts.md` | §§7–10 |
| pinned addition rule engine, saturation state/writers | non-incremental paths |
| pinned consistency state/tests | all observable behaviour |

## Depends on

WP5 and WP6.

## Owned paths

```text
src/pyelk/reasoning/saturation.py
src/pyelk/reasoning/consistency.py
src/pyelk/reasoning/session.py
tests/unit/reasoning/test_saturation.py
tests/unit/reasoning/test_consistency.py
tests/properties/test_saturation_fixedpoint.py
benchmarks/bench_saturation.py
```

## Forbidden paths

Taxonomy/realization/query, final backend adapters/dispatcher, contracts/IR/indexing,
completeness, Rust, public facade, packaging, oracle corpus.

## Deliverables

1. Single-thread global active-context queue with exact no-lost-work state transitions.
2. Novel-conclusion insertion, rule dispatch, local/cross-context producer, and saturation
   flags.
3. Monotone property → consistency → classification-root → realization-root stage support.
4. Temporary canonical complex-query roots and cache-safe saturation.
5. Global consistency logic distinguishing unsatisfiable named classes from inconsistent
   top/individual contexts.
6. Immutable debug `SaturationSnapshot` and diagnostics/counters.
7. Exhaustive tiny fixed-point comparator, order permutations, upstream saturation/consistency
   test ports, and performance fixtures.

## Acceptance criteria

1. Random tiny compiled ontologies exactly equal the exhaustive interpreter and remain equal
   for all tested agenda/seed permutations.
2. All applicable upstream class-saturation, link-consistency, context-invariant, interrupt-
   independent, and consistency fixtures pass.
3. An unsatisfiable unused named class leaves ontology consistency true; inconsistent top or
   asserted individual makes it false.
4. Repeated/later stage calls do not redo completed work or change snapshots/results.
5. Deep/cyclic generated ontologies terminate without recursion; duplicate-heavy inputs do
   not multiply conclusion processing.
6. Debug snapshot and diagnostics are deterministic across hash seeds.
7. The engine remains internal; WP10 will implement the final `BackendSession` adapter after
   taxonomy/query work. No production stub methods or forbidden edits are added.
