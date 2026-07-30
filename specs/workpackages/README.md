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
- Python 3.10+, `src/` layout, public typing, exact immutable `pyowl_core` public values.
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
WP0 -> {WP1 core contract, WP3 completeness/oracle}
{WP1, WP3} -> WP2 shared-view input
{WP1, WP2, WP3} -> WP4 indexing -> {WP5 properties, WP6 calculus}
{WP5, WP6} -> WP7 engine -> {WP8 taxonomy, WP9 queries/realization, WP11 Rust}
{WP2, WP3, WP4, WP8, WP9} -> WP10 facade
{WP10, WP11} -> WP12 packaging
WP0..WP12 -> WP13 integration
WP13 + pyowl-core WP17 -> WP14 encoded structural compiler
```

Parallel waves:

```text
0: WP0
1: {WP1, WP3}
2: WP2
3: WP4
4: {WP5, WP6}
5: WP7
6: {WP8, WP9, WP11}
7: WP10
8: WP12
9: WP13
10: WP14 after the pyowl-core encoded-view candidate freezes
```

WP11 starts only after the complete pure-Python saturation engine exists. It may implement
native taxonomy/query concurrently with WP8/WP9 because their contracts are frozen, but
backend differential sign-off remains a WP13 gate.

## 5. Package index

| WP | Assignment | Depends on | Primary owned area |
|---:|---|---|---|
| [WP0](WP0-foundation.md) | scaffold, codec, backend contracts, test doubles | — | build scaffold, `indexing/codec.py`, `reasoning/contracts.py` |
| [WP1](WP1-owl-model.md) | pyowl-core contract and exact public re-exports | WP0, pyowl-core 0.1 | `core.py`, re-export-only `owl/__init__.py` |
| [WP2](WP2-shared-snapshot-input.md) | standalone/shared snapshot ingestion and Exact-OM handshake | WP1, WP3 | `inputs.py`, input integration tests |
| [WP3](WP3-completeness-oracle.md) | feature matrix, completeness, Java oracle, frozen corpus | WP0 | completeness, oracle, upstream data |
| [WP4](WP4-indexing.md) | polarity conversion and deterministic compiled IR | WP1, WP2, WP3 | `src/pyelk/indexing/` except codec |
| [WP5](WP5-properties.md) | pure-Python property hierarchy/chain/range closure | WP4 | `reasoning/properties.py` |
| [WP6](WP6-calculus.md) | conclusions, contexts, inference/rule catalogue | WP4 | conclusion/context/rule modules |
| [WP7](WP7-saturation.md) | pure-Python scheduler, consistency, internal session | WP5, WP6 | saturation + internal session |
| [WP8](WP8-taxonomy.md) | class/property taxonomy and reduction | WP7 | `reasoning/taxonomy.py` |
| [WP9](WP9-realization-queries.md) | realisation, class queries, entailment | WP7 | realization/query modules |
| [WP10](WP10-facade.md) | public results/config/API and dispatcher integration | WP2, WP3, WP4, WP8, WP9 | public facade + backend dispatcher |
| [WP11](WP11-rust-backend.md) | complete Rust engine and thin PyO3 adapter | WP7 | Cargo workspace, Rust adapter |
| [WP12](WP12-packaging.md) | paired wheels, sdist fallback, release CI/audits | WP10, WP11 | setup/build/release workflows |
| [WP13](WP13-integration.md) | exact full parity, performance, final hardening | WP0–WP12 | cross-system fixes/benchmarks |
| [WP14](WP14-native-structural-compiler.md) | direct encoded-view-to-Rust ELK compilation and release evidence | WP13, pyowl-core WP17 | native structural compiler, adapter, differential/performance evidence |

WP14's repository-owned implementation checkpoint and historical external gates are recorded in
the [WP14 handoff report](../../reports/workpackages/WP14.md). The 0.1.0 release-owner disposition
in that report closes the release decision and binds the advertised capability to the released
pyowl-core source revision.

## 6. Contract change protocol

Frozen after WP0: IR major/minor framing, backend protocol, raw result framing, public
exception categories, import graph, and test-double interfaces.

Frozen after WP1/WP3: pyowl-core API/model/wire/adapter compatibility line and ELK feature
enum order. Public OWL structural identity is owned by pyowl-core, not frozen locally.

`pyproject.toml` is sequentially shared: WP0 owns the historical scaffold, WP1 owns only the
pyowl-core runtime/import-boundary amendment, and WP12 owns final release/build metadata.
WP14 may make the later coordinated core range/feature and compiler-schema change required by its
encoded-view handoff; it does not otherwise reopen packaging ownership.

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
