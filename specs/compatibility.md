# Compatibility Contract

This document defines the observable behaviour that pyELK reproduces from ELK Reasoner
0.6.0. It is intentionally narrower than “all of OWL 2 EL” and broader than the strict
OWL 2 EL grammar in a few polarity-safe cases.

Authoritative upstream references, all pinned to
`b8ac5ce83db0704a7359d96aa382891e2f547863`:

- [`ElkAxiomConverterImpl`](https://github.com/liveontologies/elk-reasoner/blob/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/indexing/conversion/ElkAxiomConverterImpl.java)
- [`ElkPolarityExpressionConverterImpl`](https://github.com/liveontologies/elk-reasoner/blob/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/indexing/conversion/ElkPolarityExpressionConverterImpl.java)
- [`Feature`](https://github.com/liveontologies/elk-reasoner/blob/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/completeness/Feature.java)
- [`TopIncompletenessMonitor`](https://github.com/liveontologies/elk-reasoner/blob/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/completeness/TopIncompletenessMonitor.java)
- [`ObjectPropertyTaxonomyIncompleteness`](https://github.com/liveontologies/elk-reasoner/blob/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/completeness/ObjectPropertyTaxonomyIncompleteness.java)
- [`ElkQueryAxiomIndexingVisitor`](https://github.com/liveontologies/elk-reasoner/blob/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/query/ElkQueryAxiomIndexingVisitor.java)

## 1. Semantic assumptions

pyELK uses OWL 2 Direct Semantics: open world, monotonic entailment, and no unique-name
assumption. Different IRIs do not imply different individuals. Equality asserted through
`SameIndividual` is propagated; `DifferentIndividuals` can make an ontology inconsistent
when equality is also derivable.

The strict W3C OWL 2 EL profile remains a conformance reference, but ELK 0.6.0 has two
important differences:

1. several data-property/datatype constructs permitted by OWL 2 EL are not reasoned over;
2. some constructs outside the profile are handled in one logical polarity or by a partial
   conversion.

pyELK MUST reproduce those differences and their completeness metadata. It MUST NOT claim
full OWL 2 EL support until a separately named mode implements it without changing the
ELK-compatible mode.

## 2. Status vocabulary

| Code | Meaning |
|---|---|
| **C** | Converted and complete for general class/instance reasoning, subject to combinations listed later. |
| **P** | Converted wholly or partly, but an affected result is marked potentially incomplete. |
| **I** | Unsupported during indexing. The entire containing logical axiom is ignored and the feature occurrence is recorded. |
| **N** | Accepted as non-logical input and ignored without making logical results incomplete. |

“Complete” here means complete relative to ELK's 0.6.0 procedure, not a promise about an
out-of-profile OWL ontology.

Logical polarity is assigned recursively:

- in `SubClassOf(L, R)`, `L` is negative and `R` is positive;
- all operands of `EquivalentClasses` are dual;
- operands of `DisjointClasses` and class assertions are converted as specified by the
  upstream axiom converter;
- intersection, existential filler, property, and nominal conversions preserve the current
  polarity unless the pinned converter explicitly switches it;
- `ObjectComplementOf` switches polarity.

## 3. Class and property expressions

| Construct | Status | Required conversion / feature behaviour |
|---|---:|---|
| named `Class`, `owl:Thing`, `owl:Nothing` | C | Structurally interned. Positive `owl:Nothing` additionally affects object-property-taxonomy completeness. |
| named `ObjectProperty` | C | Structurally interned. Negative `owl:topObjectProperty` and positive `owl:bottomObjectProperty` are P. |
| `ObjectIntersectionOf` | C | Zero operands become `owl:Thing`; one is identity; n-ary input is left-associated binary indexing in source order. |
| `ObjectSomeValuesFrom` | C | Intern `(property, filler)` at current polarity. |
| `ObjectHasValue(R, a)` | C/P | Convert to `ObjectSomeValuesFrom(R, singleton(a))`; positive occurrence combined with any object-property range makes general results incomplete. |
| `ObjectHasSelf` | C/P | Positive is supported; negative occurrence is tracked as incomplete. |
| `DataHasValue` | P | Structurally interned for equality detection but every occurrence makes general results incomplete. No datatype reasoning is performed. |
| `ObjectOneOf` | P | Empty becomes `owl:Nothing`; singleton becomes its individual; n-ary becomes union. Every nonempty occurrence records `OBJECT_ONE_OF`. |
| `ObjectComplementOf` | C/P | Positive occurrence is supported for contradiction reasoning; negative occurrence records incompleteness. |
| `ObjectUnionOf` | C/P | Empty becomes `owl:Nothing`; singleton is identity; n-ary is interned. Negative occurrences are decomposable; positive occurrences record incompleteness. |
| anonymous individual | I | Record `ANONYMOUS_INDIVIDUAL`; ignore the containing axiom. |
| `ObjectInverseOf` | I | Record `OBJECT_INVERSE_OF`; ignore the containing axiom. |
| object/data universal restriction | I | Record the matching `*_ALL_VALUES_FROM` feature. |
| object/data min, max, exact cardinality | I | Record the matching cardinality feature. |
| `DataSomeValuesFrom` | I | Record `DATA_SOME_VALUES_FROM`, even though W3C OWL 2 EL admits it. |
| data property or datatype entity | I/P | Data declarations/axioms are unsupported; structural `DataHasValue` remains the exception above. |

The Python object model and parser MAY represent additional OWL objects so unsupported input
can be diagnosed. Representability does not imply indexing support.

## 4. Axiom indexing matrix

| Axiom | Status | Conversion |
|---|---:|---|
| annotation axioms | N | Parse and retain in the document model if requested; do not index. |
| `Declaration` | C | Register a supported entity. A declaration of an unsupported entity kind is ignored with that feature. |
| `SubClassOf` | C | Negative-convert the subclass and positive-convert the superclass. |
| `EquivalentClasses` | C | Use the first expression as the hub; create definition/equivalence conversions with ELK's named-class preference. |
| `DisjointClasses` | C | For 0–2 members, emit pairwise conjunction-to-bottom conversions; for >2, retain the n-ary disjoint-members object. |
| `ClassAssertion` | C | Convert the individual negatively and the class expression positively. |
| `SameIndividual` | C | Reduce every member after the first to both subclass directions with the first. |
| `DifferentIndividuals` | C | Use the same binary/n-ary disjointness scheme as `DisjointClasses`. |
| `ObjectPropertyAssertion` | C/P | Convert `R(a,b)` to `a SubClassOf ObjectSomeValuesFrom(R,b)`; combined with property range is incomplete. |
| `EquivalentObjectProperties` | C | Reduce every property after the first to both subproperty directions. |
| `SubObjectPropertyOf` | C | Support named subproperties and nonempty named property chains; chains are indexed from right to left as in ELK. |
| `ObjectPropertyDomain` | C | Convert to `ObjectSomeValuesFrom(R, owl:Thing) SubClassOf Domain`. |
| `ObjectPropertyRange` | C/P | Index the range rule; record the feature. It becomes incomplete only in combinations in §6. |
| `ReflexiveObjectProperty` | C/P | Convert `owl:Thing SubClassOf ObjectHasSelf(R)`; object-property taxonomy has a chain combination caveat. |
| `TransitiveObjectProperty` | C | Convert to `SubObjectPropertyOf(ObjectPropertyChain(R,R),R)`. |
| `DisjointUnion(C, members)` | C/P | 0 members: `C ≡ owl:Nothing`; 1: full equivalence; ≥2: pairwise/n-ary disjointness plus each member `SubClassOf C`, but omit `C SubClassOf union(members)` and record `DISJOINT_UNION`. |
| data-property assertions/domain/range | I | Ignore whole axiom and record its feature. |
| data subproperty/equivalence/functionality | I | Ignore whole axiom and record its feature. |
| datatype definition | I | Ignore whole axiom and record `DATATYPE_DEFINITION`. |
| negative object/data property assertion | I | Ignore whole axiom and record its feature. |
| functional/inverse-functional object property | I | Ignore whole axiom and record its feature. |
| inverse, symmetric, asymmetric, irreflexive property | I | Ignore whole axiom and record its feature. |
| disjoint object/data properties | I | Ignore whole axiom and record its feature. |
| `HasKey`, SWRL | I | Ignore whole axiom and record its feature. |

Conversion MUST be transactional. If any nested object throws an unsupported-feature
condition after earlier operands were interned, occurrence counts and rules created for the
containing axiom are rolled back before the axiom is ignored. This matches the v0.6.0 change
described as “make index updates revertible.”

OWL annotations do not change the logical identity used by reasoning. Duplicate logical
axioms may carry separate annotations in the document model, but the compiled ontology may
deduplicate them after feature occurrences have been counted consistently.

## 5. Ontology-level incompleteness features

For consistency, class taxonomy, class-expression queries, realization, and supported
entailment, a result is potentially incomplete if the ontology contains at least one of:

```text
ANONYMOUS_INDIVIDUAL
ASYMMETRIC_OBJECT_PROPERTY
BOTTOM_OBJECT_PROPERTY_POSITIVE
DATA_ALL_VALUES_FROM
DATA_EXACT_CARDINALITY
DATA_HAS_VALUE
DATA_MAX_CARDINALITY
DATA_MIN_CARDINALITY
DATA_PROPERTY
DATA_PROPERTY_ASSERTION
DATA_PROPERTY_DOMAIN
DATA_PROPERTY_RANGE
DATA_SOME_VALUES_FROM
DATATYPE
DATATYPE_DEFINITION
DISJOINT_DATA_PROPERTIES
DISJOINT_OBJECT_PROPERTIES
DISJOINT_UNION
EQUIVALENT_DATA_PROPERTIES
FUNCTIONAL_DATA_PROPERTY
FUNCTIONAL_OBJECT_PROPERTY
HAS_KEY
INVERSE_FUNCTIONAL_OBJECT_PROPERTY
INVERSE_OBJECT_PROPERTIES
IRREFLEXIVE_OBJECT_PROPERTY
NEGATIVE_DATA_PROPERTY_ASSERTION
NEGATIVE_OBJECT_PROPERTY_ASSERTION
OBJECT_ALL_VALUES_FROM
OBJECT_COMPLEMENT_OF_NEGATIVE
OBJECT_EXACT_CARDINALITY
OBJECT_HAS_SELF_NEGATIVE
OBJECT_INVERSE_OF
OBJECT_MAX_CARDINALITY
OBJECT_MIN_CARDINALITY
OBJECT_ONE_OF
OBJECT_UNION_OF_POSITIVE
SUB_DATA_PROPERTY_OF
SWRL_RULE
SYMMETRIC_OBJECT_PROPERTY
TOP_OBJECT_PROPERTY_NEGATIVE
```

It is also incomplete when either full combination is present:

- `OBJECT_PROPERTY_RANGE` and `OBJECT_PROPERTY_ASSERTION`;
- `OBJECT_PROPERTY_RANGE` and `OBJECT_HAS_VALUE_POSITIVE`.

A combination reason is reported only when every member occurs. A lone range, assertion, or
positive has-value occurrence does not trigger this monitor.

## 6. Object-property-taxonomy completeness

Object-property taxonomy results inherit ontology-level inconsistency/incompleteness and
have additional limitations. They are potentially incomplete when any of these occurs:

- positive `owl:Nothing` (`OWL_NOTHING_POSITIVE`);
- `DisjointClasses` (`DISJOINT_CLASSES`);
- positive `ObjectComplementOf` (`OBJECT_COMPLEMENT_OF_POSITIVE`);
- both `REFLEXIVE_OBJECT_PROPERTY` and `OBJECT_PROPERTY_CHAIN`.

The reason set and task association MUST match the pinned
`ObjectPropertyTaxonomyIncompleteness` monitor, not a single global boolean.

## 7. Supported operations

### 7.1 Core operation binding

ELK exposes both exception-throwing methods and `*Quietly` value-returning methods for an
inconsistent ontology. pyELK's public value API binds classification, realisation,
satisfiability, equivalent/sub/super-class, and instance operations to the quiet variants
used by ELK's own non-incremental golden-test harness. Named type and object-property views
are derived from those same quiet taxonomies. The OWL API adapter's
`InconsistentOntologyException` surface and Java method-for-method duplication are outside
scope; this project targets the core semantic values.

The binding is normative:

| pyELK task | Pinned core reference |
|---|---|
| class taxonomy | `getTaxonomyQuietly()` |
| object-property taxonomy | `getObjectPropertyTaxonomyQuietly()` |
| realisation | `getInstanceTaxonomyQuietly()` |
| satisfiability | `isSatisfiableQuietly()` |
| equivalent/sub/super classes | corresponding `*Quietly()` method |
| instances | `getInstancesQuietly()` |
| types and named object-property views | projection of the corresponding quiet taxonomy |
| consistency and entailment | ordinary core operation; no quiet replacement |

On inconsistency, each quiet fallback above returns ELK's collapsed value with a
no-incompleteness monitor. Therefore its pyELK `ReasoningResult` has no upstream-feature
reasons. This narrow short-circuit overrides §§5–6 only for those operations after
inconsistency is established; feature counts remain available and consistency/entailment
metadata is unaffected. The pyELK-only `PYELK_IGNORED_IMPORT` policy issue is retained if the
caller explicitly ignored imports. Exact collapsed taxonomy, realisation, and query values
are in `taxonomy-queries.md`.

The public core supports these tasks:

| Task | Required behaviour |
|---|---|
| ontology consistency | `is_consistent` and inverse `is_inconsistent`; inconsistency proves every successfully indexed supported entailment query, while unindexed queries remain false. |
| class classification | equivalence nodes plus direct subsumption edges, including top/bottom. |
| object-property classification | equivalence nodes plus direct subproperty edges, including top/bottom object properties. |
| realization | same-individual nodes, direct type nodes, and inverse instance links. |
| expression satisfiability | temporary query saturation without mutating the ontology snapshot. |
| equivalent classes | named taxonomy node for a name; saturation-derived named equivalents for a complex expression. |
| subclasses/superclasses | direct or transitive nodes; strict relation excludes the expression's equivalent node. |
| instances/types | direct or transitive nodes with the same directness definition as ELK. |
| entity enumeration | all indexed named classes, named individuals, and object properties. |

Fresh entities are allowed by default. A fresh class/property/individual is treated as a
declaration with no nontrivial axioms for the purpose of that query. With
`allow_fresh_entities=False`, any fresh query entity raises `FreshEntityError` before
backend execution. `owl:Thing`, `owl:Nothing`, and top/bottom object properties are never
fresh.

## 8. Entailment boundary

`is_entailed()` supports exactly these axiom families:

1. `ClassAssertion`
2. `DifferentIndividuals`
3. `DisjointClasses`
4. `EquivalentClasses`
5. `ObjectPropertyAssertion`
6. `ObjectPropertyDomain`
7. `SameIndividual`
8. `SubClassOf`

Entailment queries normally obey the Functional Syntax structural arities. Programmatically
constructed zero/one-member lists follow the pinned converter where it defines them. The
only excluded malformed cases are zero-member `EquivalentClasses` and `SameIndividual`,
whose Java query conversion performs unchecked empty-list access; pyELK rejects these with
`ValueError` before backend execution. This is an input-boundary safety rule, not a
different answer for a valid OWL query.

All other axiom query types return a `ReasoningResult[bool]` marked incomplete with the
matching `QUERY_*` feature and a value following ELK's unsupported-query adapter. In strict
mode they raise `UnsupportedQueryError`. They MUST NOT be implemented merely because the
corresponding ontology axiom is indexed; ELK notably does not expose entailment for
subobject-property, range, reflexivity, transitivity, or equivalent-object-property axioms.

The exact unsupported-query mapping is:

| Axiom query | Feature |
|---|---|
| `AnnotationAssertion` | `QUERY_ANNOTATION_ASSERTION_AXIOM` |
| `AnnotationPropertyDomain` | `QUERY_ANNOTATION_PROPERTY_DOMAIN_AXIOM` |
| `AnnotationPropertyRange` | `QUERY_ANNOTATION_PROPERTY_RANGE_AXIOM` |
| `SubAnnotationPropertyOf` | `QUERY_SUB_ANNOTATION_PROPERTY_OF_AXIOM` |
| `DataPropertyAssertion` | `QUERY_DATA_PROPERTY_ASSERTION_AXIOM` |
| `NegativeDataPropertyAssertion` | `QUERY_NEGATIVE_DATA_PROPERTY_ASSERTION_AXIOM` |
| `NegativeObjectPropertyAssertion` | `QUERY_NEGATIVE_OBJECT_PROPERTY_ASSERTION_AXIOM` |
| `DisjointUnion` | `QUERY_DISJOINT_UNION_AXIOM` |
| `DataPropertyDomain` | `QUERY_DATA_PROPERTY_DOMAIN_AXIOM` |
| `DataPropertyRange` | `QUERY_DATA_PROPERTY_RANGE_AXIOM` |
| `DisjointDataProperties` | `QUERY_DISJOINT_DATA_PROPERTIES_AXIOM` |
| `EquivalentDataProperties` | `QUERY_EQUIVALENT_DATA_PROPERTIES_AXIOM` |
| `FunctionalDataProperty` | `QUERY_FUNCTIONAL_DATA_PROPERTY_AXIOM` |
| `SubDataPropertyOf` | `QUERY_SUB_DATA_PROPERTY_OF_AXIOM` |
| `DatatypeDefinition` | `QUERY_DATATYPE_DEFINITION_AXIOM` |
| `Declaration` | `QUERY_DECLARATION_AXIOM` |
| `HasKey` | `QUERY_HAS_KEY_AXIOM` |
| `AsymmetricObjectProperty` | `QUERY_ASYMMETRIC_OBJECT_PROPERTY_AXIOM` |
| `DisjointObjectProperties` | `QUERY_DISJOINT_OBJECT_PROPERTIES_AXIOM` |
| `EquivalentObjectProperties` | `QUERY_EQUIVALENT_OBJECT_PROPERTIES_AXIOM` |
| `FunctionalObjectProperty` | `QUERY_FUNCTIONAL_OBJECT_PROPERTY_AXIOM` |
| `InverseFunctionalObjectProperty` | `QUERY_INVERSE_FUNCTIONAL_OBJECT_PROPERTY_AXIOM` |
| `InverseObjectProperties` | `QUERY_INVERSE_OBJECT_PROPERTIES_AXIOM` |
| `IrreflexiveObjectProperty` | `QUERY_IRREFLEXIVE_OBJECT_PROPERTY_AXIOM` |
| `ObjectPropertyRange` | `QUERY_OBJECT_PROPERTY_RANGE_AXIOM` |
| `ReflexiveObjectProperty` | `QUERY_REFLEXIVE_OBJECT_PROPERTY_AXIOM` |
| `SubObjectPropertyOf` | `QUERY_SUB_OBJECT_PROPERTY_OF_AXIOM` |
| `SymmetricObjectProperty` | `QUERY_SYMMETRIC_OBJECT_PROPERTY_AXIOM` |
| `TransitiveObjectProperty` | `QUERY_TRANSITIVE_OBJECT_PROPERTY_AXIOM` |
| SWRL | `QUERY_SWRL_RULE` |

## 9. Completeness result contract

Every reasoning operation returns `ReasoningResult[T]`:

```python
@dataclass(frozen=True, slots=True)
class ReasoningResult(Generic[T]):
    value: T
    complete: bool
    reasons: tuple[CompletenessIssue, ...]
```

`complete` is true exactly when `reasons` is empty. Issues are canonical, deduplicated,
sorted values containing task, feature enum names, constructor names, polarity, and optional
feature-combination members. Counts are retained in session diagnostics but do not duplicate
issues.

Default unsupported treatment is `ignore`, matching ELK. Unsupported ontology axioms are
ignored and surfaced through completeness. `unsupported="error"` raises
`UnsupportedFeatureError` transactionally at the first unsupported containing axiom; this is
an opt-in safety mode and its exception ordering need not match Java.

Strict-mode unsupported ontology/query validation happens during compilation and therefore
precedes any quiet inconsistent-ontology value short-circuit. Quiet fallback parity is
measured under the default `ignore` mode used by the pinned oracle.

Strict ontology compilation raises `UnsupportedFeatureError`. Strict class-expression or
entailment query compilation raises `UnsupportedQueryError` for both an unsupported query
family (`QUERY_*`) and an unsupported nested constructor; the exception records the exact
feature and complete query value.

Completeness metadata is part of exact parity. A backend returning the correct edge set but
the wrong completeness flag or reasons fails parity.

The separate `PYELK_IGNORED_IMPORT` reason is an ingestion-policy issue, not an upstream
`Feature` enum value. It appears only when `ignore_imports=True`, which has no Java-oracle
equivalent because oracle fixtures supply a closed ontology. It never alters logical values
computed from the supplied axioms.

## 10. Canonical equality boundary

Parity comparisons use public IRIs and structural values, never internal IDs:

- an equivalence node is a sorted tuple of entity IRIs;
- a taxonomy is the set of nodes plus the set of direct `(sub_node, super_node)` edges;
- realization adds same-individual nodes and direct `(instance_node, type_node)` edges;
- non-direct query results are sets of equivalence nodes;
- completeness reasons are sorted by `(task, features, polarity)`;
- booleans and exception categories compare exactly;
- parse failures compare by category and source span, not English message text.

All outputs MUST be identical for Python/Rust, workers 1/N, input permutations, and repeated
runs. Java set ordering is explicitly outside the comparison.

## 11. Compatibility exceptions

There are no approved semantic exceptions at project start. Discovered differences are
recorded here only after a minimal Java-oracle fixture proves the discrepancy and the project
owner accepts it. Until then, a difference is a bug or unfinished work.
