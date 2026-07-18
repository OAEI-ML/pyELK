# ELK-to-pyELK Traceability

All Java paths are relative to the pinned ELK 0.6.0 tree at commit
`b8ac5ce83db0704a7359d96aa382891e2f547863`. This table assigns every core source area a
fate, pyELK destination, normative specification, and work package.

## 1. Module and package map

| ELK source | Fate | pyELK destination | Spec | WP |
|---|---|---|---|---:|
| `elk-owl-parent/elk-owl-model/.../owl/interfaces`, `iris`, `predefined` | do not port as public values; map exact `pyowl_core` types to private ELK conversion | `src/pyelk/owl/__init__.py`, `indexing/conversion.py` | `parsing.md`, `indexing.md` | 1, 4 |
| `elk-owl-parent/elk-owl-model/.../comparison`, `printers`, `util` | compiler-only compatibility observations; public structural identity/writing remains pyowl-core | `indexing/conversion.py` | `parsing.md`, `indexing.md` | 4 |
| `elk-owl-parent/elk-owl-implementation/.../implementation`, `managers` | do not port; core owns construction/equality | none | `parsing.md` | 1 |
| `elk-owl-parent/elk-owl-parsing-javacc/.../Owl2FunctionalStyleParser.jj` | oracle grammar evidence only; no pyELK parser | pyowl-core dependency, frozen fixtures | `parsing.md`, `verification.md` | 2–3 |
| `elk-reasoner/.../loading/Owl2ParserLoader.java` | preserve observable load/conversion behavior without its Java threading/parser | `inputs.py`, `api.py` | `parsing.md` | 2, 10 |
| `reasoner/completeness/Feature.java` | exact enum manifest | `reasoning/completeness.py`, test manifest | `compatibility.md`, `contracts.md` | 3 |
| `TopIncompletenessMonitor.java`, `ObjectPropertyTaxonomyIncompleteness.java` | exact monitor logic | `reasoning/completeness.py` | `compatibility.md` | 3 |
| `indexing/conversion/ElkEntityConverterImpl.java` | exact entity support | `indexing/conversion.py` | `compatibility.md`, `indexing.md` | 4 |
| `ElkPolarityExpressionConverterImpl.java` | exact polarity conversion | `indexing/polarity.py` | `compatibility.md`, `indexing.md` | 4 |
| `ElkAxiomConverterImpl.java` | exact axiom conversion and disjoint threshold | `indexing/conversion.py` | `compatibility.md`, `indexing.md` | 4 |
| `indexing/classes/*`, `indexing/model/*` | structural interning, occurrence/rule-registration semantics; replace incremental mutability with frozen IR | `indexing/` | `indexing.md`, `contracts.md` | 4 |
| `saturation/properties/*` | property hierarchy/chain/range closure | `reasoning/properties.py`, `rust/pyelk-core/src/properties.rs` | `saturation.md` | 5, 11 |
| `saturation/conclusions/*`, `saturation/context/*` | conclusion identity and context storage | `reasoning/conclusions.py`, `contexts.py`, Rust equivalents | `saturation.md` | 6, 11 |
| `saturation/inferences/*` | premise/conclusion calculus, without proof objects | `reasoning/rules.py`, Rust equivalents | `saturation.md` | 6–7, 11 |
| non-incremental `saturation/rules/*` | rule dispatch and joins | `reasoning/rules.py`, Rust equivalents | `saturation.md` | 7, 11 |
| `saturation/SaturationState*`, `ClassExpressionSaturation*`, addition rule engine | agenda/session algorithm | `reasoning/saturation.py`, Rust equivalent | `saturation.md` | 7, 11 |
| `consistency/*` | ontology inconsistency decision | `reasoning/saturation.py` | `saturation.md` | 7 |
| `reduction/*` | equivalence-aware transitive reduction | `reasoning/taxonomy.py` | `taxonomy-queries.md` | 8 |
| `taxonomy/*`, including quiet singleton taxonomies | class/property nodes, edges, realization, inconsistent collapse | `reasoning/taxonomy.py`, `realization.py` | `taxonomy-queries.md` | 8–9 |
| `query/*` | complex expression and supported entailment query semantics | `reasoning/queries.py` | `compatibility.md`, `taxonomy-queries.md` | 9 |
| `entailments/model`, supported converter/evidence decision logic | decision semantics only; no proof graph | `reasoning/queries.py` | `taxonomy-queries.md` | 9 |
| `Reasoner.java`, non-incremental parts of `AbstractReasonerState.java` | Python facade, quiet-operation binding, and lazy stage behaviour | `inputs.py`, `api.py`, `config.py`, `result.py` | `contracts.md`, `compatibility.md` | 2, 10 |
| non-incremental stage dependencies in `ReasonerStageManager.java` | simplified immutable session stages | `api.py`, backend sessions | `SPEC.md`, `saturation.md` | 10–11 |
| `elk-reasoner/src/test/resources/test_input` | copy with attribution and canonicalise | `tests/data/elk-v0.6.0/` | `verification.md` | 3, 13 |
| relevant `elk-reasoner/src/test/java` invariants | translate/select | corresponding Python/Rust tests | subsystem specs | owning WP, 13 |

## 2. Explicitly excluded upstream areas

| ELK source | Reason |
|---|---|
| `reasoner/incremental/*`, incremental rules/stages/tests | user excluded incremental reasoning; immutable v1 sessions |
| `reasoner/proof/*`, `tracing/*`, `elk-proofs` | proofs/explanations/tracing excluded |
| proof-producing inference visitors/factories | no public proof identity; retain only inference semantics |
| `elk-owlapi` | Java adapter excluded; dev oracle may depend on its released artifact |
| `elk-protege`, distribution Protégé modules | UI/plugin extra |
| `elk-cli`, distribution CLI | application extra |
| `elk-ore-parent`, standalone ORE | evaluation/application extra |
| `elk-benchmark` | replace with Python/Rust/Java benchmark harness |
| progress monitors, Java executor lifecycle, logging/statistics-only classes | non-semantic operational API |
| configuration evictors and incremental toggles | only frozen config fields in `contracts.md` survive |
| OWL API parsing of RDF/XML/Turtle/OWL/XML/Manchester | Java adapters excluded; standalone formats come only from pyowl-core |

## 3. Inference-to-test manifests

`tests/data/manifests/inferences.toml` contains one row for every concrete non-incremental
class in `saturation/inferences`. `tests/data/manifests/property-inferences.toml` uses the
same shape for
`saturation/properties/inferences`:

```toml
[[inference]]
java_class = "SubClassInclusionTautology"
java_path = "elk-reasoner/src/main/java/.../SubClassInclusionTautology.java"
python_rule = "pyelk.reasoning.rules.subclass_inclusion_tautology"
unit_test = "tests/unit/reasoning/test_tautology.py::test_tautology"
rust_test = "pyelk_core::rules::tests::tautology"
status = "implemented"
```

The class manifest has 30 implemented rows and 34 explicitly excluded abstract,
incremental, tracing, or representation-only rows. The property manifest has three
implemented rows and nine explicitly excluded rows. CI resolves every Python symbol and
test pointer in `tests/unit/reasoning/rules/test_manifest.py` and
`tests/unit/reasoning/test_properties.py`; Rust unit-test names are checked by the native
crate suite. A pinned concrete inference cannot be absent or have a status other than
`implemented`.

## 4. Feature-to-test manifest

`tests/data/manifests/features.toml` records the exact enum order in `Feature.java`.
Every ontology feature records:

```text
constructor and polarity
index action: complete | partial | ignore | nonlogical
affected tasks and combination conditions
minimal ontology fixture
expected count
expected completeness issue
```

All 79 rows include a frozen fixture and a resolving test pointer. Every query feature
records the unsupported entailment axiom family and expected false value. The exhaustive
checks in `tests/unit/reasoning/test_completeness.py` and
`tests/unit/indexing/test_feature_corpus.py` compare the manifest with the production enum,
compiler counts, monitor conditions, and canonical issues. The separate
`PolicyFeature.IGNORED_IMPORT` remains a facade policy issue and is tested in
`tests/unit/test_api.py`.

## 5. Work-package ownership map

| Owned area | Sole primary owner before integration |
|---|---:|
| packaging scaffold, IR codec/contracts/test doubles | WP0 |
| pyowl-core dependency/version guard and public re-exports | WP1 (WP12 finalizes release metadata) |
| `inputs.py`, shared view/provider integration, obsolete parser removal | WP2 |
| completeness + oracle + manifests/frozen fixtures | WP3 |
| `indexing/` except codec | WP4 |
| pure property saturation | WP5 |
| pure conclusions/context/inference catalogue | WP6 |
| pure scheduler/consistency/internal session | WP7 |
| taxonomy | WP8 |
| realization/query | WP9 |
| public facade/config/result/dispatcher and final Python backend adapter | WP10 |
| Rust workspace and adapter | WP11 |
| build/release workflows and packaging tests | WP12 |
| cross-system parity/performance/integration fixes | WP13 |

Shared-file changes after the owning WP merges are made by WP13 or sent as a small dependent
PR to the owner. Same-wave agents do not edit each other's paths.

## 6. Implemented source-to-verification matrix

This matrix is the release-facing index from the upstream area through its normative
contract to production code and executable evidence. Every row is implemented; exclusions
are confined to the explicit scope table in §2.

| Area | Normative source/spec | Production implementation | Executable evidence | Status |
|---|---|---|---|---|
| pyowl-core version and capability boundary | `parsing.md` §§1, 5 | `src/pyelk/core.py` | `tests/unit/core/test_core_contract.py` | implemented |
| Paths, bytes, streams, views, providers, overlays, composites | `parsing.md` §§2–4 | `src/pyelk/inputs.py`, `src/pyelk/api.py` | `tests/unit/inputs/`, shared-snapshot and consumer integration tests | implemented |
| Structural conversion and deterministic IR | `indexing.md`, `contracts.md` §7 | `src/pyelk/indexing/` | `tests/unit/indexing/`, indexing and fingerprint properties | implemented |
| ELK feature counting and per-task completeness | `compatibility.md` §§2–6 | `src/pyelk/reasoning/completeness.py` | 79-row feature manifest and exhaustive completeness tests | implemented |
| Property hierarchy, chains, reflexivity, ranges | `saturation.md` §2 | `src/pyelk/reasoning/properties.py` | property-inference manifest and generated property saturation | implemented |
| Conclusions and contexts | `saturation.md` §§3–4 | `src/pyelk/reasoning/conclusions.py`, `contexts.py` | conclusion and context unit suites | implemented |
| Non-incremental inference calculus and registration | `saturation.md` §§5–7 | `src/pyelk/reasoning/rules.py`, `registration.py` | 30-row inference and 23-row registration manifests | implemented |
| Agenda, saturation, and consistency | `saturation.md` §§8–10 | `src/pyelk/reasoning/saturation.py` | saturation, consistency, and generated fixed-point suites | implemented |
| Class/property taxonomy and reduction | `taxonomy-queries.md` §§2–5 | `src/pyelk/reasoning/taxonomy.py` | taxonomy/reduction unit and generated property suites | implemented |
| Realization and same-individual nodes | `taxonomy-queries.md` §6 | `src/pyelk/reasoning/realization.py` | realization unit and frozen corpus cases | implemented |
| Class-expression and entailment queries | `compatibility.md` §§7–8, `taxonomy-queries.md` §§7–8 | query reasoning and compiler conversion | query/entailment unit and frozen corpus cases | implemented |
| Public lifecycle, result values, fresh entities | `contracts.md` §§2–6 | `src/pyelk/api.py`, `config.py`, `result.py` | facade, result, and configuration suites | implemented |
| Python backend | `contracts.md` §8 | `src/pyelk/backends/python.py` | Python backend suite and full frozen runner | implemented |
| Rust accelerator and worker determinism | `native-packaging.md` §§2–7 | both Rust crates and Python dispatcher | Rust crate, native core, and saturation differential suites | implemented |
| Backend/core diagnostics and fallback policy | `native-packaging.md` §8 | backend dispatcher and `BackendReport` | dispatch unit suite and installed smoke | implemented |
| Frozen ELK 0.6 parity and regression reduction | `verification.md` §§2–6 | `tests/parity/runner.py`, `tests/parity/minimize.py` | 124 ontology cases, 138 goldens, hash-seed/wheel runs, deterministic semantic minimizer | implemented |
| W3C Direct-EL classification | `verification.md` §6 | `tests/data/w3c/build_manifest.py` | 65-case W3C manifest validation | implemented |
| Shared-consumer and OAEI wire paths | `SPEC.md` §3.2, `parsing.md` §§3, 8 | core view/provider and verified wire boundaries | `tests/integration/test_consumer_paths.py` | implemented |
| Distribution and Java/compiler exclusion | `native-packaging.md` §§9–11 | build configuration, reproducible-sdist hook, and artifact auditor | byte-rebuilt source/fallback artifacts, pinned external ABI/platform audits, Python 3.10–3.14 installed lanes | implemented |
| Performance corpus and semantic timing guard | `verification.md` §9 | `benchmarks/`, `tools/benchmark.py` | manifest validation plus Java-free generated and hash-pinned biomedical harness tests | implemented |
| User examples and attribution | WP13 deliverables 7–8 | `README.md`, `NOTICE.pyelk` | verbatim README execution and artifact notice audit | implemented |

## 7. Release evidence routing

| Gate | Local executable | Release automation/evidence |
|---|---|---|
| Pure Python 3.10/3.12, no Java/compiler | `PYELK_PURE_PYTHON=1 python -m pytest` | foundation and compiler-free wheel jobs |
| Frozen semantic parity | `tests/parity/runner.py --backend python --workers 1` | installed Python and tier-one native wheel suites |
| Native equality, workers 1/2/N, repetition | native backend and Rust crate tests | cibuildwheel platform matrix plus ABI3 Python 3.11–3.14 jobs after the full Python 3.10 build test |
| Core shared-view identity and wire handoff | consumer/shared integration tests | full installed suite under both backend selections |
| Artifact reproducibility, metadata, notice, ABI, dependency, JVM policy | paired local builds and `tools/check_artifact.py` | byte-identical source/fallback rebuilds plus `abi3audit` and target-platform auditors |
| Performance and RSS | `tools/benchmark.py --suite full --native --workers N --enforce` plus the required biomedical flags in `benchmarks/README.md` | labelled runner with machine baseline and optional pinned Java report |
| Oracle regeneration | `tools/oracle.py regenerate` with pinned JDK/Maven | opt-in Java oracle workflow; reports remain development evidence |

Release publication is intentionally not performed by the verification code. Tier-one wheel
and labelled-performance results are produced on their declared runners, while the same
semantic assertions and artifact auditor are executable locally.
