# WP13 — Full Parity, Performance, Documentation, and Release Hardening

## Goal

Integrate all completed components, eliminate every Java/Python/Rust semantic or completeness
difference, run the full conformance/package/performance programme, fill traceability, and
produce a release-ready pyELK. This WP fixes integration defects; it does not silently waive
or replace unfinished primary deliverables.

## Read first

| Source | Sections |
|---|---|
| every spec in `specs/` | all |
| `specs/verification.md` | all, especially definition of done |
| every completed WP acceptance report | known limitations and evidence |
| pinned ELK release notes/tests/source map | compatibility baseline |

## Depends on

WP0 through WP12.

## Owned paths

```text
tests/integration/**
tests/parity/**                    # dependent extension of WP3 harness
tests/properties/**                # cross-system cases only
tests/data/w3c/**
benchmarks/**                      # integrated corpus/baselines/results
tools/benchmark.py
README.md
NOTICE.pyelk
specs/traceability.md              # implementation status/test pointers
cross-component fixes with explicit owner review
```

## Forbidden actions

Weakening expected results, adding approximate parity, skipping/xfailing a discrepancy,
changing the ELK baseline, deleting provenance, silently expanding scope, or publishing
artifacts. Release execution is a separate owner action after this WP passes.

## Deliverables

1. One end-to-end fixture runner for Java frozen JSON, Python, Rust workers 1/N, and installed
   wheels.
2. Classified resolution of every upstream semantic/completeness/error diff.
3. Full generated/metamorphic/backend differential suites and regression minimiser.
4. W3C EL manifest with exact ELK-complete/incomplete/out-of-parser classification.
5. Integrated performance corpus manifest, Java/Python/Rust stage measurements, scaling,
   RSS, regression baselines, and optimisation fixes.
6. Complete upstream feature/inference/source → spec → code → test traceability.
7. User documentation for install, fallback/native diagnosis, supported fragment,
   completeness handling, API examples, no-Java guarantee, and limitations.
8. Attribution/modified notices and release checklist evidence.

## Acceptance criteria

1. All 124 upstream ontology inputs and 138 goldens produce exact canonical results,
   completeness reasons, and error categories on pure Python and every tier-one Rust wheel.
2. Live Java regeneration yields zero unclassified diff; no compatibility exception exists
   unless explicitly approved and documented in `compatibility.md`.
3. Python/Rust equality holds for the extended generated corpus, all hash seeds, workers
   1/2/N, repeated/query permutations, and supported architectures.
4. Exhaustive tiny, metamorphic, parser/decoder fuzz smoke, lint/type/import, and full
   compiler/JRE-free suites pass.
5. Every feature and concrete inference manifest row resolves to implemented code and a
   passing test; traceability has no TODO/unknown status.
6. All sdist/wheel selection, ABI, dependency, no-Java, metadata, and offline smoke gates pass.
7. Native meets boundary/speed/Java-relative/RSS regression thresholds from the specs, or an
   owner-approved performance-only exception documents hardware, profile, cause, and follow-up;
   semantic gates can never be excepted this way.
8. README examples run verbatim against both backends and explain how to inspect/require
   completeness.
9. `git status` is clean after a complete verification run and generated artifacts are
   reproducible from documented commands.
