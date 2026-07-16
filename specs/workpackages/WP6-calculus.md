# WP6 — Conclusions, Context Storage, and Pure Inference Calculus

## Goal

Implement structural conclusion identities, duplicate-suppressing context storage, and every
non-incremental ELK class inference/rule as deterministic pure functions. Do not implement the
global saturation scheduler yet.

## Read first

| Source | Sections / symbols |
|---|---|
| `specs/saturation.md` | §§3–6, 10–11 |
| `specs/indexing.md` | §7 occurrence registration |
| pinned `saturation/conclusions/model` | all required identities |
| pinned `saturation/inferences` and non-incremental `rules` | all concrete classes |
| `specs/traceability.md` | §3 manifest contract |

## Depends on

WP4.

## Owned paths

```text
src/pyelk/reasoning/conclusions.py
src/pyelk/reasoning/contexts.py
src/pyelk/reasoning/rules.py
tests/unit/reasoning/test_conclusions.py
tests/unit/reasoning/test_contexts.py
tests/unit/reasoning/rules/**
tests/data/manifests/inferences.toml
```

## Forbidden paths

Global scheduler/consistency, property implementation, taxonomy/query, backends, indexing,
completeness, Rust, facade, packaging, oracle corpus.

## Deliverables

1. Frozen conclusion values and compact identity keys for all nine conclusion families.
2. `ContextState` storage/lookup/insertion API, local todo, queued/saturated flags, and frozen
   debug view.
3. Rule dispatcher grouped by trigger family with a producer protocol for local/cross-context
   products.
4. Every concrete non-incremental inference/rule and occurrence-linked registration lookup.
5. Multi-premise rules complete for every premise arrival order.
6. Full class-inference manifest mapping Java class → Python symbol → positive unit test;
   explicit ignored list for abstract/incremental/tracing-only classes. Property inferences
   remain in WP5's separate manifest.
7. Context/inference invariant tests and small rule-level benchmarks where useful.

## Acceptance criteria

1. Manifest coverage script finds no unclassified pinned concrete inference/property
   inference; every implemented row resolves to a symbol and test.
2. Each rule has minimal positive, each-premise-negative, duplicate, and arrival-permutation
   tests.
3. Self-disjointness, complement, bottom, existential/self links, chain composition,
   propagation, ranges, definitions, intersections/unions, and inconsistency propagation all
   match pinned premise/destination semantics.
4. Adding one conclusion twice stores/processes it once; context freeze is deterministic.
5. Rule execution is iterative, makes no global state mutation, and works with a fake
   property view/producer while WP5 is parallel.
6. No scheduler/backend/public API or forbidden edit is present.
