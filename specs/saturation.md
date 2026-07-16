# Property and Class Saturation

This specification defines the complete pure-Python reasoning calculus and the semantic
requirements for the Rust engine. ELK 0.6.0 uses a consequence-based, context-oriented,
semi-naive saturation procedure. pyELK may reorganise storage and fuse rule applications,
but every conclusion relevant to an in-scope result must be the same.

References:

- [`saturation`](https://github.com/liveontologies/elk-reasoner/tree/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/saturation)
- [`saturation/conclusions/model`](https://github.com/liveontologies/elk-reasoner/tree/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/saturation/conclusions/model)
- [`saturation/inferences`](https://github.com/liveontologies/elk-reasoner/tree/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/saturation/inferences)
- [`saturation/rules`](https://github.com/liveontologies/elk-reasoner/tree/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/saturation/rules)
- [`saturation/properties`](https://github.com/liveontologies/elk-reasoner/tree/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/saturation/properties)

The formulas and premise side conditions in the pinned inference classes are normative. This
document groups them into an implementable engine and removes tracing/incremental machinery.

## 1. Stages

```text
CompiledOntology
  -> initialise named properties and chain axioms
  -> saturate property-chain subsumption and inherited ranges
  -> register occurrence-driven class rules
  -> create and saturate demanded class/individual contexts
  -> compute ontology consistency
  -> expose immutable SaturationSnapshot to taxonomy/query stages
```

Property saturation is a prerequisite of class saturation. No class rule may consult a
partially built property closure.

## 2. Property saturation

### 2.1 Conclusions

```python
@dataclass(frozen=True, slots=True)
class SubPropertyChain:
    sub_chain: PropertyChainId
    super_chain: PropertyChainId

@dataclass(frozen=True, slots=True)
class PropertyRange:
    property: EntityId
    range: ExpressionId
```

A named property has a singleton chain. Seed every chain with
`SubPropertyChain(chain, chain)`, every compiled subproperty row, and every explicit range.

### 2.2 Required inferences

- `SubPropertyChainTautology`
- `SubPropertyChainExpandedSubObjectPropertyOf`
- `PropertyRangeInherited`

The property closure also computes the left/right composition maps used by
`BackwardLinkComposition` and `ForwardLinkComposition`. For each complex chain
`R1 ... Rn` and each known subchain/property relation, the closure records all legal prefix
and suffix matches exactly as `SaturatedPropertyChain` and
`PropertyHierarchyCompositionComputationFactory` do.

The implementation MUST handle:

- cycles and equivalence in the property hierarchy;
- repeated properties and transitivity `(R,R) -> R`;
- chains that share prefixes/suffixes;
- reflexive properties in class saturation;
- inherited ranges along subproperty relations;
- top/bottom object properties with the completeness flags in `compatibility.md`.

### 2.3 Algorithm

The Python backend uses a duplicate-suppressed deque of property conclusions plus adjacency
indices keyed by subchain, superchain, left property, right suffix, and property. Each novel
conclusion joins only with compatible existing conclusions. A rescan of all pairs per agenda
item is forbidden.

Rust may use compact CSR plus mutable small vectors/bitsets, but must return the same closure.
Property conclusion ordering is not observable.

## 3. Context roots and conclusions

A context root is an indexed class expression that can serve as the left side of a saturated
subsumption: named class, named individual, or supported complex query root.

The pure backend represents the following conclusion identities:

```python
ContextInitialization(root)
SubContextInitialization(destination, sub_destination_property)
SubClassInclusionDecomposed(destination, subsumer)
SubClassInclusionComposed(destination, subsumer)
ForwardLink(destination, chain, target)
BackwardLink(destination, relation, source)
Propagation(destination, relation, carry_existential)
DisjointSubsumer(destination, disjoint_group, position)
ClassInconsistency(destination)
```

`destination` is the owning context unless the upstream conclusion defines another trace
root. Since proofs are out of scope, pyELK stores only structural conclusion identity, not
the inference that produced it.

### 3.1 `ContextState`

```python
@dataclass(slots=True)
class ContextState:
    root: ExpressionId
    initialized: bool
    saturated: bool
    inconsistent: bool
    composed_subsumers: set[ExpressionId]
    decomposed_subsumers: set[ExpressionId]
    forward_links: dict[PropertyChainId, set[ExpressionId]]
    backward_links: dict[EntityId, set[ExpressionId]]
    propagations: dict[EntityId, set[ExpressionId]]
    disjoint_positions: dict[DisjointGroupId, set[int]]
    initialized_subcontexts: set[EntityId]
    todo: deque[Conclusion]
    queued: bool
```

Private storage may replace sets with bitsets/sorted vectors. The logical partition between
composed and decomposed subsumers is retained because it controls different rule families.

## 4. Initial conclusions

Creating a context enqueues `ContextInitialization(root)` exactly once. Initialization
derives:

- root subsumes itself (`SubClassInclusionTautology`);
- root is subsumed by `owl:Thing` (`SubClassInclusionOwlThing`);
- rules registered globally on context initialization, including top-class definitions;
- required subcontext initialization for reflexive/propagation processing.

Contexts are allocated lazily, but an eager implementation may allocate all committed named
class and individual roots. It may not make ignored-only entities visible.

## 5. Normative inference catalogue

Every concrete non-incremental inference below is required. A backend may fuse an inference
with insertion of its conclusion, but the premise conditions and destination are those of the
pinned Java class.

### 5.1 Subclass inclusion and definitions

```text
ContextInitializationNoPremises
SubContextInitializationNoPremises
SubClassInclusionTautology
SubClassInclusionOwlThing
SubClassInclusionExpandedSubClassOf
SubClassInclusionExpandedDefinition
SubClassInclusionExpandedFirstEquivalentClass
SubClassInclusionExpandedSecondEquivalentClass
SubClassInclusionComposedDefinedClass
SubClassInclusionComposedOfDecomposed
```

Operationally, an asserted/indexed `A SubClassOf B` and an existing inclusion of `A` in a
context derive `B`. A named definition `A ≡ D` installs both definition directions using the
composed/decomposed form required by the expression shape. Equivalent rows must not lose the
named-class preference recorded by indexing.

### 5.2 Intersections and unions

```text
SubClassInclusionDecomposedFirstConjunct
SubClassInclusionDecomposedSecondConjunct
SubClassInclusionDecomposedConjunct
SubClassInclusionComposedObjectIntersectionOf
SubClassInclusionComposedObjectUnionOf
```

- Having an intersection as a decomposed subsumer derives every conjunct.
- Having all registered conjunct premises derives the composed intersection.
- Having a disjunct derives a negatively occurring union where the occurrence-driven rule is
  registered.
- Duplicate operands and nested binarised intersections are processed without special-case
  loss.

### 5.3 Existentials, self, links, propagation, and ranges

```text
SubClassInclusionComposedObjectSomeValuesFrom
ForwardLinkOfObjectSomeValuesFrom
BackwardLinkOfObjectSomeValuesFrom
ForwardLinkOfObjectHasSelf
BackwardLinkOfObjectHasSelf
ForwardLinkComposition
BackwardLinkComposition
BackwardLinkReversedExpanded
PropagationGenerated
SubClassInclusionRange
SubClassInclusionObjectHasSelfPropertyRange
```

Required behaviour:

1. A supported existential subsumer creates the corresponding forward relation from the
   destination context to its filler root and a backward relation at the target.
2. Backward/forward links compose only through relations authorised by the saturated
   property-chain prefix/suffix maps.
3. Propagation conclusions carry an indexed existential back across a compatible relation.
4. `ObjectHasSelf(R)` behaves as a reflexive link at the same root in its supported polarity.
5. Property ranges add their class expression to fillers reached by the property and through
   inherited subproperties/chains according to ELK's range rules.
6. Cross-context conclusions are enqueued at the destination context through the global
   writer; they are never inserted directly while another worker owns that context.

### 5.4 Disjointness, negation, bottom, and contradiction

```text
DisjointSubsumerFromSubsumer
ClassInconsistencyOfDisjointSubsumers
ClassInconsistencyOfObjectComplementOf
ClassInconsistencyOfOwlNothing
ClassInconsistencyPropagated
```

- A context containing a member of an indexed disjoint group records the exact member
  position.
- Two different positions from one group derive inconsistency. Equal expression IDs at two
  positions still count as different positions and make that expression unsatisfiable.
- A supported expression and its complement in the same context derive inconsistency.
- `owl:Nothing` as subsumer derives inconsistency.
- Inconsistency propagates over the backward-link rule prescribed by ELK; do not globally
  mark every context inconsistent merely because one named class is unsatisfiable.

## 6. Rule registration and dispatch

The Python reference has explicit rule functions grouped exactly by triggering conclusion:

```text
context initialization
subcontext initialization
composed subsumer
decomposed subsumer
forward link
backward link
propagation
disjoint subsumer
class inconsistency
```

Occurrence-driven linked rules from `indexing/registration.py` are immutable session data.
For a novel premise, dispatch applies:

1. static rules for its conclusion kind;
2. linked rules registered on the premise expression/property/context;
3. joins against already stored compatible premises;
4. insertion of each derived conclusion through duplicate suppression.

Rules with multiple premises must be complete regardless of which premise arrives last. The
implementation either registers symmetric triggers or, on either trigger, looks up all other
premises already stored. A rule may not rely on a lucky agenda order.

The following non-incremental rule classes define the required dispatch surface:

```text
RootContextInitializationRule
OwlThingContextInitRule
SuperClassFromSubClassRule
ComposedFromDecomposedSubsumerRule
EquivalentClassFirstFromSecondRule
EquivalentClassSecondFromFirstRule
IndexedClassFromDefinitionRule
IndexedClassDecompositionRule
IndexedObjectComplementOfDecomposition
ObjectIntersectionFromFirstConjunctRule
ObjectIntersectionFromSecondConjunctRule
IndexedObjectIntersectionOfDecomposition
ObjectUnionFromDisjunctRule
IndexedObjectSomeValuesFromDecomposition
IndexedObjectHasSelfDecomposition
PropagationFromExistentialFillerRule
PropagationInitializationRule
SubsumerPropagationRule
SubsumerBackwardLinkRule
BackwardLinkFromForwardLinkRule
BackwardLinkChainFromBackwardLinkRule
NonReflexiveBackwardLinkCompositionRule
ReflexiveBackwardLinkCompositionRule
ContradictionFromNegationRule
ContradictionFromOwlNothingRule
ContradictionCompositionRule
ContradictionOverBackwardLinkRule
ContradictionPropagationRule
OwlNothingDecompositionRule
DisjointSubsumerFromMemberRule
```

Classes used only for deletion, overdeletion, incremental addition, pruning, tracing, rule
statistics, proof production, or inference checking are out of scope.

## 7. Agenda algorithm

### 7.1 Pure Python

```text
enqueue seed inference/conclusion into context
if context changed empty -> nonempty, enqueue context ID globally

while global context queue not empty:
    claim one context (queued = false, exclusive ownership)
    while its local todo is not empty:
        pop one candidate
        if candidate identity already stored: continue
        store candidate
        apply all rules triggered by candidate
        enqueue local/cross-context products
    mark saturated
    if a concurrent/cross write arrived after the empty check, requeue exactly once
```

The pure backend is single-threaded but implements the same queue-state transitions so its
logic is a direct oracle for Rust. Conclusion identity sets provide semi-naive duplicate
suppression. Recursive rule calls are forbidden.

### 7.2 Rust concurrency

Rust uses a global injector plus worker-local deques/work stealing (Rayon or crossbeam is an
implementation choice). At most one worker mutates a context at a time. Cross-context writes
use a thread-safe inbox and an atomic queued/claimed state that cannot lose wakeups.

Allowed synchronisation designs include per-context mutexes, sharded locks, or owner-worker
mailboxes. Required properties are:

- no data race or simultaneous mutable access to one context;
- each novel conclusion is eventually processed;
- an idle transition cannot race with a producer and strand work;
- worker panic/cancellation cannot leave a reusable partially valid session;
- output is independent of worker count and schedule.

Hash iteration order must never decide whether a rule fires. The release note requirement
that derived conclusions are independent of application order is an acceptance gate.

## 8. Saturation modes and caching

The session supports internal stage levels:

```python
class Stage(IntEnum):
    COMPILED = 0
    PROPERTIES = 1
    CONSISTENCY = 2
    CLASSIFIED = 3
    REALIZED = 4
```

Stages advance monotonically and are idempotent. A consistency request may saturate only
`owl:Thing` and named individuals plus transitively demanded fillers. Classification adds all
named class roots. Realisation adds named individual roots. It is also valid to saturate all
committed roots eagerly, provided outputs and performance gates hold.

Complex query contexts are cached by canonical mini-IR expression key and do not alter the
ontology's entity enumeration or taxonomy nodes. Cache eviction policy is not parity
relevant; an implementation may use a bounded LRU or retain queries until `close()`.

## 9. Consistency

An ontology is inconsistent when saturation derives inconsistency for the top context or for
a committed named-individual context whose existence is asserted by the ontology. An
unsatisfiable named class alone does not make the ontology inconsistent.

On inconsistency:

- `is_consistent().value` is false and `is_inconsistent().value` true;
- successfully indexed supported entailment queries return true by classical explosion;
  unindexable/unsupported queries remain false as specified in `taxonomy-queries.md`;
- taxonomy and realisation shapes follow the pinned ELK golden fixtures, as specified in
  `taxonomy-queries.md`; backends may short-circuit only to that exact canonical shape;
- feature occurrence counts are retained. Result-level completeness remains task-specific:
  ELK's quiet taxonomy, realisation, satisfiability, and class-query fallbacks deliberately
  use a no-incompleteness monitor, while consistency and entailment retain their applicable
  monitors. The facade MUST apply the table in `compatibility.md`, not globally erase or
  globally preserve reasons.

## 10. Saturation snapshot

The Python core exposes an immutable internal view:

```python
@dataclass(frozen=True, slots=True)
class SaturationSnapshot:
    property_subsumers: tuple[tuple[PropertyChainId, ...], ...]
    property_ranges: tuple[tuple[ExpressionId, ...], ...]
    contexts: Mapping[ExpressionId, FrozenContext]
    inconsistent_ontology: bool
```

Rust need not materialise this Python value. Its taxonomy/query methods expose equivalent
information through raw result methods. Debug builds MAY export a compact snapshot for
backend differential diagnosis, guarded from the public API.

## 11. Correctness tests

- Unit-test each concrete inference with minimal premises and absence-of-premise controls.
- Port upstream concurrent saturator, class-expression saturation, property saturation,
  link-consistency, and context-invariant tests.
- Compare Python closure to a deliberately slow exhaustive fixed-point interpreter on random
  tiny compiled ontologies.
- Run every tiny case under all permutations of seed/rule agenda order.
- Generate cyclic property hierarchies, repeated/transitive chains, diamonds, reflexivity,
  range inheritance, existential cycles, self restrictions, complement/disjointness, and
  inconsistent individuals.
- Assert Python/Rust exact equality for conclusions in debug snapshot mode and exact public
  equality in release mode.
- Stress lost-wakeup races with thousands of cross-context writes and workers 1, 2, and CPU
  count under ThreadSanitizer-compatible Rust tests where available.
- Track conclusion/rule counts against Java diagnostics for selected fixtures as diagnostic
  evidence; public parity remains the release gate.
