# WP3 — Completeness Matrix, Java Oracle, and Frozen ELK Corpus

## Goal

Freeze every ELK feature/completeness rule, build a dev-only Java 0.6.0 oracle, copy the
upstream reasoning corpus with provenance, and generate canonical JSON expected results.
Ordinary tests must consume frozen data without Java.

## Read first

| Source | Sections / symbols |
|---|---|
| `specs/compatibility.md` | all |
| `specs/contracts.md` | §§5, 9 |
| `specs/verification.md` | §§1–5, 11–12 |
| `specs/baseline.toml` | all |
| pinned `Feature.java`, completeness monitors | exact enum/order/conditions |
| pinned reasoner test resources and manifest creators | corpus/query semantics |

## Depends on

WP0.

## Owned paths

```text
src/pyelk/reasoning/completeness.py
tools/java-oracle/**
tools/oracle.py
tests/data/manifests/features.toml
tests/data/elk-v0.6.0/**
tests/parity/test_frozen_elk.py
tests/unit/reasoning/test_completeness.py
```

## Forbidden paths

OWL/parser/indexing semantics, saturation/taxonomy/query implementation, backend dispatcher,
Rust, build/release metadata, public facade.

## Deliverables

1. Frozen `Feature` enum in exact pinned Java order, constructor/polarity metadata, general
   and object-property-task monitor logic, and query-feature mapping.
2. `issues_for()` implementing single and combination triggers plus the inconsistent quiet
   no-monitor short-circuit exactly.
3. Feature manifest with one minimal fixture/test pointer per ontology/query enum member.
4. Java JSON-lines oracle pinned to ELK 0.6.0/JDK configuration and fail-closed feature-count
   bridge.
5. Idempotent fixture generation/diff tool with hashes and provenance manifest.
6. Attributed copy of 124 ontology inputs and 138 upstream golden outputs.
7. Canonical JSON for applicable operations, completeness flags/reasons, and errors.
8. A frozen-data pytest harness that can initially validate schema/manifests and later accepts
   any `Reasoner` factory supplied by WP13.

## Acceptance criteria

1. Enum names/order equal `Feature.java`; CI fails for a missing/extra/reordered member.
2. Every general single feature, both general combinations, and every object-property
   special condition has positive/negative unit tests; every quiet task and both unaffected
   task families have an inconsistent-monitor test.
3. Two clean oracle regenerations produce byte-identical JSON/manifests.
4. Oracle outputs agree with all upstream golden files after canonicalisation or contain an
   explicitly investigated generator bug fixed in this WP.
5. Removing Java/Maven from `PATH` still runs all non-`java_oracle` tests from frozen data.
6. Runtime/sdist paths contain no JAR/class file and do not import oracle code.
7. Copied resources carry repository/tag/commit/path/hash/licence/modified provenance.
8. Full checks pass and only owned paths changed.

## Implementation status

Status: **complete and reproducibly verified** on 2026-07-17.

- `Feature`, its 79-entry manifest, all monitor combinations, query mapping, quiet-task
  behavior, and policy isolation are implemented and cross-checked against every frozen
  Java issue record.
- The 124 ontology inputs, 138 original golden outputs, 79 minimal feature fixtures, and
  203 canonical oracle outputs are hash-inventoried with Apache-2.0 provenance.
- `python tools/oracle.py regenerate --check` succeeds with the pinned Temurin 17.0.19+10,
  Maven 3.9.16, and ELK 0.6.0 artifacts; two fresh oracle processes reproduce the committed
  tree byte-for-byte.
- `python tools/oracle.py verify` and all ordinary tests succeed without Java, Maven,
  compilers, network access, or an executable on `PATH`, on Python 3.10 and 3.12.
- The frozen-data evaluator hook is intentionally awaiting a `Reasoner` factory from WP13,
  as specified in deliverable 8; no WP3 API or acceptance gate is blocked by that later
  integration.
