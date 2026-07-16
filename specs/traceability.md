# ELK-to-pyELK Traceability

All Java paths are relative to the pinned ELK 0.6.0 tree at commit
`b8ac5ce83db0704a7359d96aa382891e2f547863`. This table assigns every core source area a
fate, pyELK destination, normative specification, and work package.

## 1. Module and package map

| ELK source | Fate | pyELK destination | Spec | WP |
|---|---|---|---|---:|
| `elk-owl-parent/elk-owl-model/.../owl/interfaces`, `iris`, `predefined` | model behaviour, Pythonic values | `src/pyelk/owl/` | `parsing.md` | 1 |
| `elk-owl-parent/elk-owl-model/.../comparison`, `printers`, `util` | structural keys and canonical printer; selective | `owl/keys.py`, `parsing/printer.py` | `parsing.md` | 1–2 |
| `elk-owl-parent/elk-owl-implementation/.../implementation`, `managers` | reproduce immutable construction/equality only; do not port visitor boilerplate | `src/pyelk/owl/` | `parsing.md` | 1 |
| `elk-owl-parent/elk-owl-parsing-javacc/.../Owl2FunctionalStyleParser.jj` | reimplement grammar | `src/pyelk/parsing/` | `parsing.md` | 2 |
| `elk-reasoner/.../loading/Owl2ParserLoader.java` | streaming/batching semantics, no Java thread copy | `parsing/parser.py`, `api.py` | `parsing.md` | 2, 10 |
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
| `Reasoner.java`, non-incremental parts of `AbstractReasonerState.java` | Python facade, quiet-operation binding, and lazy stage behaviour | `ontology.py`, `api.py`, `config.py`, `result.py` | `contracts.md`, `compatibility.md` | 2, 10 |
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
| OWL API parsing of RDF/XML/Turtle/OWL/XML/Manchester | format adapters are post-v1 and must compile to the same model |

## 3. Required inference-to-test manifest

WP6 creates `tests/data/manifests/inferences.toml` with one row for every concrete
non-incremental class in `saturation/inferences`. WP5 creates the same shape in
`tests/data/manifests/property-inferences.toml` for
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

CI fails if a pinned concrete inference is absent, points to a missing symbol/test, or has a
status other than `implemented`. Abstract base classes and incremental-only inferences are
listed in a separate ignored section with an explicit reason.

## 4. Required feature-to-test manifest

WP3 creates `tests/data/manifests/features.toml` from the exact enum order in `Feature.java`.
Every ontology feature records:

```text
constructor and polarity
index action: complete | partial | ignore | nonlogical
affected tasks and combination conditions
minimal ontology fixture
expected count
expected completeness issue
```

Every `QUERY_*` feature records the unsupported entailment axiom family and expected
`value=False`. CI checks that all enum values are represented and that no implementation-only
feature has been inserted into the frozen IR enum. The separate
`PolicyFeature.IGNORED_IMPORT` has its own facade test and is never encoded in that manifest.

## 5. Work-package ownership map

| Owned area | Sole primary owner before integration |
|---|---:|
| packaging scaffold, IR codec/contracts/test doubles | WP0 |
| `owl/` | WP1 |
| `parsing/` | WP2 |
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
