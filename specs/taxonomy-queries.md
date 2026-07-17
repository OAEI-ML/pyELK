# Taxonomy, Realisation, and Query Semantics

This specification turns saturation results into public reasoning answers. It covers class
and object-property classification, instance realisation, complex class-expression queries,
and the eight supported entailment families.

References:

- [`taxonomy`](https://github.com/liveontologies/elk-reasoner/tree/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/taxonomy)
- [`SingletoneInstanceTaxonomy`](https://github.com/liveontologies/elk-reasoner/blob/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/taxonomy/SingletoneInstanceTaxonomy.java)
- [`reduction`](https://github.com/liveontologies/elk-reasoner/tree/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/reduction)
- [`query`](https://github.com/liveontologies/elk-reasoner/tree/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/query)
- [`entailments`](https://github.com/liveontologies/elk-reasoner/tree/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/entailments)
- [`Reasoner`](https://github.com/liveontologies/elk-reasoner/blob/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/Reasoner.java)

## 1. Relation terminology

For indexed class expressions `A` and `B`, write `A <= B` when saturation proves
`A SubClassOf B`. Named class equivalence is mutual reachability: `A <= B` and `B <= A`.
The taxonomy is the quotient of named classes by this equivalence.

Object-property equivalence and ordering use the saturated `SubPropertyChain` relation on
singleton property chains. Instance equality uses mutual class inclusion between individual
context roots.

All public edges are directed `(sub_node, super_node)`.

## 2. Class taxonomy

### 2.1 Nodes

Classification includes every committed named class plus `owl:Thing` and `owl:Nothing`.
Ignored-only classes do not appear.

- Group satisfiable named classes by mutual subsumption.
- The top node contains `owl:Thing` and every named class equivalent to it.
- The bottom node contains `owl:Nothing` and every unsatisfiable named class.
- No entity appears in two nodes.
- Every node member tuple is sorted; nodes are sorted by their canonical member.

An expression is unsatisfiable when its context has `ClassInconsistency`. Unsatisfiable named
classes are all equivalent to `owl:Nothing` under classical semantics even if no explicit
reverse inclusion was stored.

### 2.2 Direct edges

For distinct quotient nodes `X` and `Y`, a candidate edge `X -> Y` exists when any/every
representative proves `X <= Y`. It is direct iff there is no distinct node `Z` with
`X <= Z <= Y`.

Use an equivalence-aware transitive reduction. The implementation follows the observable
behaviour of `TransitiveReductionFactory` and `ClassTaxonomyComputationFactory` but may use a
more compact algorithm. Required properties:

- cycles are collapsed before reduction;
- top has no super edge and bottom has no sub edge;
- every non-top node reaches top;
- every non-bottom node is reachable from bottom through the conventional bottom-to-leaf
  edges used by ELK taxonomy output;
- redundant asserted/inferred edges do not appear as direct;
- direct edges are invariant to class/axiom/worker order.

An efficient implementation processes nodes in topological order and maintains reduced
ancestor bitsets or sorted sets. A quadratic scan over all triples is permitted only in the
slow test oracle, not production taxonomy construction.

### 2.3 Inconsistent ontology

If the ontology is inconsistent, class classification returns one node containing
`owl:Thing`, `owl:Nothing`, and every committed named class, with no direct edges. This is
the canonical form of ELK's `EquivalentClasses(owl:Nothing owl:Thing ...)` golden outputs.

This is the result of ELK's `getTaxonomyQuietly()` path. That fallback replaces the task
monitor with `getNoIncompletenessMonitor()`, so this particular result has no upstream
feature reasons even when the ontology feature registry is nonempty. It is `complete=True`
unless the caller chose the pyELK-only ignored-import policy. The registry itself is retained
for later consistency or entailment operations.

## 3. Object-property taxonomy

Classification includes every committed named object property plus
`owl:topObjectProperty` and `owl:bottomObjectProperty`.

- Equivalence is mutual singleton-chain subsumption.
- Top/bottom nodes and direct reduction follow the class-taxonomy invariants.
- Complex property chains are not public taxonomy members.
- A property that is provably bottom joins the bottom node.
- Task-specific completeness is evaluated using §6 of `compatibility.md`.

If the ontology is inconsistent, one node contains top, bottom, and all committed object
properties, with no direct edges. As with the class taxonomy, the quiet fallback result is
free of upstream task reasons and is complete unless an ignored-import policy issue remains.

## 4. Instance taxonomy and realisation

### 4.1 Instance nodes

Group committed named individuals by mutual inclusion of their nominal context roots.
`SameIndividual` therefore produces one `EntityNode[NamedIndividual]`. Different IRIs remain
separate unless equality is derived; there is no unique-name assumption.

Declared individuals with no other assertion still appear. Individuals mentioned only in a
rolled-back axiom do not.

### 4.2 Types

An instance node `i` has type class node `C` when saturation proves `i <= c` for a member
`c` of `C`. A type is direct iff there is no strictly more specific satisfiable type node of
`i` below it. Equivalent class members produce one type node.

- If an individual has no more-specific named type, top is its direct type.
- Bottom is never a type in a consistent ontology.
- `types(i, direct=False)` returns the upward closure of direct types, including top.
- `instances(C, direct=False)` is the inverse relation and includes instances of subclasses.
- Direct instances are those whose direct type set contains `C`'s equivalence node.

### 4.3 Inconsistency

If the ontology is inconsistent, ELK's quiet realisation has the single collapsed class
taxonomy node. If committed named individuals exist, it also has exactly one instance node
containing all of them, and that node has the collapsed class node as its sole direct type.
If no named individual exists, there is no instance node. This follows
`SingletoneInstanceTaxonomy` and is visible in the pinned inconsistent realisation goldens
(the Functional Syntax printer emits the individuals as declarations because their sole
type node is simultaneously top and bottom). The quiet fallback result has no upstream task
reasons. It is complete unless an ignored-import policy issue remains.

## 5. Taxonomy value validation

Before exposing a raw backend taxonomy, the Python adapter validates:

1. member IDs have the requested entity kind and occur once;
2. top and bottom node indices exist;
3. edges refer to distinct valid nodes and are unique;
4. the edge graph is acyclic;
5. no edge is transitively redundant;
6. all nodes lie on a bottom-to-top path, except the single-node inconsistent case;
7. native and Python member coverage equals committed entity enumeration.

Realisation additionally validates unique individual membership and minimal direct types.
Violations raise `BackendProtocolError`.

## 6. Named-entity queries

For a named class/property already in a taxonomy:

- `equivalent_*` returns its node (`equivalent_classes` wraps it in a
  one-element tuple; only an unindexed complex query yields an empty tuple —
  see `contracts.md` §4);
- `sub*(direct=True)` returns immediate incoming sub nodes;
- `super*(direct=True)` returns immediate outgoing super nodes;
- `direct=False` returns strict transitive closure and excludes the entity's own node;
- values are sorted by node canonical member.

For allowed fresh named entities:

- class/property equivalence is a singleton fresh node;
- its only non-strict semantic bounds are top and bottom; strict superclass result is top and
  strict subclass result is bottom, with direct and transitive views equal;
- a fresh individual is a singleton instance node with direct type top and no equality peers.

With fresh entities disallowed, validation occurs over the entire query expression/axiom and
one `FreshEntityError` contains the sorted set of all fresh entities.

The quiet inconsistent-ontology short-circuit precedes fresh-entity rejection for
satisfiability, class/property relation, instance, and type value queries, matching ELK's
quiet path: those operations return the collapsed inconsistent value even when the queried
entity is fresh and `allow_fresh_entities=False`. On a consistent ontology, and for
entailment compilation, the normal validation rule applies.

## 7. Complex class-expression queries

The facade compiles a query mini-IR against the session symbol table. The backend creates or
reuses a query root, installs the same occurrence-driven rules as ontology indexing, and
saturates it without adding query-only entities to public enumeration.

Let `Q` be the saturated query expression:

- `is_satisfiable(Q)` is false iff `Q` derives `ClassInconsistency`;
- `equivalent_classes(Q)` are named nodes `C` such that `Q <= C` and `C <= Q`;
- `superclasses(Q)` are named nodes for which `Q <= C`, excluding equivalents;
- `subclasses(Q)` are named nodes for which `C <= Q`, excluding equivalents;
- `instances(Q)` are instance nodes `i` for which `i <= Q`.

Direct supers are the minimal strict named super nodes under taxonomy order. Direct subs are
the maximal strict named sub nodes. Direct instances use the same minimal-type rule as
realisation. Results for `owl:Thing`, `owl:Nothing`, satisfiable expressions with no named
equivalent, and unsatisfiable expressions follow these definitions and the pinned class-query
goldens.

Unsupported query expressions return the best ELK-compatible value with query/ontology
incompleteness reasons in ignore mode; strict mode raises transactionally.

For a consistent ontology, `CompiledQuery.encoded is None` reproduces the exact
unindexed-query branches in `Reasoner.java`:

| Operation | `direct=True` | `direct=False` / scalar |
|---|---|---|
| satisfiable | — | `True` |
| equivalent classes | — | empty node collection |
| subclasses | bottom node | empty collection |
| superclasses | top node | empty collection |
| instances | empty collection | empty collection |

The direct/non-direct asymmetry is intentional: ELK first returns top/bottom as a synthetic
direct result, then expands to the strict transitive neighbours of that node for a
non-direct request. Query feature counts still determine completeness.

On an inconsistent ontology, the value-returning query API follows ELK's quiet paths before
ordinary selection:

- every expression is unsatisfiable;
- equivalent classes is the one collapsed class node;
- strict subclasses and superclasses are empty;
- instances is empty when the ontology has no named individuals and otherwise is the one
  node containing all committed named individuals, for either directness;
- any queried named individual's type view is the one collapsed class node, regardless of
  fresh-entity policy;
- equivalent object properties is the one collapsed property node, and strict sub/super
  property results are empty.

These quiet fallback results have no upstream feature reasons. They are complete unless an
ignored-import policy issue remains. `is_consistent()` still returns false with its own
task-specific completeness metadata, and supported entailment still uses explosion as
specified below.

## 8. Entailment

First compile the query. In an inconsistent ontology, every successfully indexed query from
a supported entailment family is true. A supported family containing an unindexable nested
construct still has `CompiledQuery.encoded=None` and returns false, as does an unsupported
axiom family; query completeness metadata explains the limitation. Otherwise:

| Query axiom | Decision |
|---|---|
| `SubClassOf(A,B)` | prove `A <= B`; an unsatisfiable `A` entails every `B`. |
| `EquivalentClasses(E...)` | every member is mutually subsumed with the first; one member is true. |
| `DisjointClasses(E...)` | every pair of member positions has unsatisfiable intersection according to ELK's n-ary reduction. |
| `ClassAssertion(C,a)` | prove nominal root `a <= C`. |
| `SameIndividual(a...)` | every member is mutually included with the first. |
| `DifferentIndividuals(a...)` | every pair is disjoint through the same nominal-disjointness test as ontology conversion. |
| `ObjectPropertyAssertion(R,a,b)` | prove `a <= ObjectSomeValuesFrom(R,b)`. |
| `ObjectPropertyDomain(R,C)` | prove `ObjectSomeValuesFrom(R,owl:Thing) <= C`. |

Zero/one/n-ary corner behaviour is oracle-tested where the pinned converter defines it,
rather than delegated to Python truthiness. A zero-member `EquivalentClasses` or
`SameIndividual` is accepted as an ontology-model edge value but is outside the entailment
query boundary: the facade raises `ValueError` before backend execution. This avoids
reproducing the pinned Java converter's unchecked empty-list access; conforming pyowl-core
parsers/builders cannot construct either malformed query.
Unsupported entailment axiom types have `value=False` plus their `QUERY_*` issue in ignore
mode. Query incompleteness is the union of ontology issues relevant to entailment and issues
caused by the query itself.

## 9. Entity enumeration

`all_classes`, `all_named_individuals`, and `all_object_properties` are available immediately
after compilation and do not force saturation. They return committed supported entities
sorted by UTF-8 IRI. Predefined top/bottom class and object-property entities are included in
the matching enumerations, consistent with the Java oracle fixture schema.

Whether a bare undeclared entity mentioned in a supported axiom counts is determined by
index occurrence, not declaration. Annotations and ignored axioms do not introduce it.

## 10. Caching and lifecycle

- Class/property taxonomies and realisation are immutable and cached after first build.
- Transitive query closures may be cached per node.
- Complex query results are keyed by canonical mini-IR bytes plus query kind/directness.
- Cache content cannot change result ordering or completeness.
- `close()` releases native memory and Python saturation/taxonomy graphs; already returned
  public immutable result values remain valid.

## 11. Tests

- Consume all 66 upstream class-classification pairs, 11 object-property pairs, 5
  realisation pairs, 26 class-query pairs, and 16 entailment ontology groups.
- Port taxonomy acyclicity, equivalence grouping, direct-edge/transitive-reduction, bottom,
  fresh-node, and instance-taxonomy invariant tests.
- Property-test taxonomy construction from random preorders against a slow quotient plus
  transitive-reduction oracle.
- Test direct versus transitive queries on chains, diamonds, cycles/equivalence, disconnected
  asserted roots, unsatisfiable leaves, fresh entities, and inconsistent ontologies.
- Test every supported entailment family positively, negatively, under inconsistency, with
  every converter-defined empty/one/n-ary list, the two explicitly rejected empty queries,
  and unsupported query features.
- Require exact Java/Python/Rust canonical equality; no Jaccard, count tolerance, or
  approximate edge metric is allowed.
