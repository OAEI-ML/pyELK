# Work Packages — Parallel Agent Handout

Each `WP*.md` file is a bounded implementation assignment. It names owned paths, forbidden
paths, dependencies, upstream references, deliverables, tests, and exact acceptance criteria.
One work package is one branch and one reviewable pull request unless the project owner
explicitly splits it.

## 1. Before starting any work package

1. Read [`../SPEC.md`](../SPEC.md) completely.
2. Read every document/section in the WP's **Read first** table.
3. Verify [`../baseline.toml`](../baseline.toml); all Java reading uses the pinned commit.
4. Confirm every dependency WP is present in the branch base. Do not add production stubs for
   an unfinished dependency.
5. Check the owned/forbidden paths. If the assignment requires a forbidden edit, stop and
   propose a contract change instead of bypassing ownership.
6. Run the existing checks before editing and record the baseline.

## 2. Rules for every WP

- Branch: `wp<N>-<slug>`; one primary WP per branch.
- Python 3.10+, `src/` layout, public typing, immutable public values.
- Keep `ruff format`, `ruff check`, strict `mypy`, `import-linter`, and applicable pytest
  suites green.
- Every production deliverable has unit/property tests in the same PR.
- No Java, network, compiler, or native extension is required by ordinary tests.
- Java source defines behaviour but is never copied into the runtime tree. Mark translated
  source/fixtures and preserve Apache-2.0 attribution.
- Do not weaken exact assertions, skip a failing parity case, or add a tolerance. Minimise and
  report a discrepancy.
- Do not edit another WP's files “for convenience.” Use its public contract or send a small
  follow-up to the owner.
- Do not expose internal numeric IDs or native types through the public API.
- No recursive ontology graph traversal, global mutable reasoner state, or Python callbacks
  from native hot loops.
- Paste acceptance evidence and commands in the PR description.

## 3. Shared definition of done

```text
owned deliverables complete
all acceptance criteria demonstrated
new tests pass
full available suite passes
pure-Python forced suite passes
native forced/differential suite passes when applicable
lint + format + types + import graph pass
no unrelated/forbidden files changed
traceability manifests updated by the owning WP
```

A passing unit subset does not make an incomplete WP complete.

## 4. Dependency graph

```text
WP0 foundation
├── WP1 OWL model ───────────┬── WP2 parser <── WP3
│                            └── WP4 indexing ─┬── WP5 properties ─┐
├── WP3 completeness/oracle ────────┘          └── WP6 calculus ───┴── WP7 engine
│                                                                      ├── WP8 taxonomy
│                                                                      ├── WP9 queries/realization
│                                                                      └── WP11 Rust backend
│
WP2 + WP3 + WP4 + WP8 + WP9 ─────────────────────────────────────────────> WP10 facade
WP10 + WP11 ─────────────────────────────────────────────────────────────> WP12 packaging
all WPs ─────────────────────────────────────────────────────────────────> WP13 integration
```

Parallel waves:

```text
0: WP0
1: {WP1, WP3}
2: {WP2, WP4}
3: {WP5, WP6}
4: WP7
5: {WP8, WP9, WP11}
6: WP10
7: WP12
8: WP13
```

WP11 starts only after the complete pure-Python saturation engine exists. It may implement
native taxonomy/query concurrently with WP8/WP9 because their contracts are frozen, but
backend differential sign-off remains a WP13 gate.

## 5. Package index

| WP | Assignment | Depends on | Primary owned area |
|---:|---|---|---|
| [WP0](WP0-foundation.md) | scaffold, codec, backend contracts, test doubles | — | build scaffold, `indexing/codec.py`, `reasoning/contracts.py` |
| [WP1](WP1-owl-model.md) | immutable OWL model and structural keys | WP0 | `src/pyelk/owl/` |
| [WP2](WP2-functional-parser.md) | streaming Functional Syntax parser/printer | WP1, WP3 | `src/pyelk/parsing/`, `ontology.py` |
| [WP3](WP3-completeness-oracle.md) | feature matrix, completeness, Java oracle, frozen corpus | WP0 | completeness, oracle, upstream data |
| [WP4](WP4-indexing.md) | polarity conversion and deterministic compiled IR | WP1, WP3 | `src/pyelk/indexing/` except codec |
| [WP5](WP5-properties.md) | pure-Python property hierarchy/chain/range closure | WP4 | `reasoning/properties.py` |
| [WP6](WP6-calculus.md) | conclusions, contexts, inference/rule catalogue | WP4 | conclusion/context/rule modules |
| [WP7](WP7-saturation.md) | pure-Python scheduler, consistency, internal session | WP5, WP6 | saturation + internal session |
| [WP8](WP8-taxonomy.md) | class/property taxonomy and reduction | WP7 | `reasoning/taxonomy.py` |
| [WP9](WP9-realization-queries.md) | realisation, class queries, entailment | WP7 | realization/query modules |
| [WP10](WP10-facade.md) | public results/config/API and dispatcher integration | WP2, WP3, WP4, WP8, WP9 | public facade + backend dispatcher |
| [WP11](WP11-rust-backend.md) | complete Rust engine and thin PyO3 adapter | WP7 | Cargo workspace, Rust adapter |
| [WP12](WP12-packaging.md) | paired wheels, sdist fallback, release CI/audits | WP10, WP11 | setup/build/release workflows |
| [WP13](WP13-integration.md) | exact full parity, performance, final hardening | WP0–WP12 | cross-system fixes/benchmarks |

## 6. Contract change protocol

Frozen after WP0: IR major/minor framing, backend protocol, raw result framing, public
exception categories, import graph, and test-double interfaces.

Frozen after WP1/WP3: OWL structural keys and feature enum order.

A needed change requires:

1. minimal failing case;
2. affected WPs and compatibility impact;
3. spec patch;
4. codec/protocol version decision;
5. owner approval;
6. coordinated dependent updates.

Agents do not silently extend a tuple, reinterpret an enum value, or accept both old/new
shapes.

## 7. Integration etiquette

- Rebase a WP only onto completed dependencies, not another active same-wave branch.
- Generated files must be reproducible from checked-in scripts/manifests.
- Cross-WP fixtures live under the owner named in `traceability.md`.
- WP13 may change multiple areas, but only to integrate completed behaviour; it does not
  absorb missing primary deliverables from unfinished WPs.
