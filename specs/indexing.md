# Indexing and Normalisation

Indexing converts public OWL values into the deterministic `CompiledOntology` consumed by
both backends. It reproduces ELK's structural interning, polarity-aware occurrence tracking,
axiom conversions, and rollback behaviour without copying ELK's mutable incremental index.

Primary references:

- [`indexing/model`](https://github.com/liveontologies/elk-reasoner/tree/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/indexing/model)
- [`indexing/classes`](https://github.com/liveontologies/elk-reasoner/tree/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/indexing/classes)
- [`indexing/conversion`](https://github.com/liveontologies/elk-reasoner/tree/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/indexing/conversion)
- [`DirectIndex`](https://github.com/liveontologies/elk-reasoner/blob/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/indexing/classes/DirectIndex.java)

## 1. API and ownership

```python
def compile_ontology(
    ontology: Ontology,
    *,
    unsupported: Literal["ignore", "error"] = "ignore",
    ignore_imports: bool = False,
) -> CompiledOntology: ...

def compile_query_expression(
    expression: ClassExpression,
    symbols: SymbolTableView,
) -> CompiledQuery: ...

def compile_entailment_query(
    axiom: Axiom,
    symbols: SymbolTableView,
) -> CompiledQuery: ...
```

`indexing/` owns entity/expression IDs, the binary codec, logical fingerprints, and all axiom
conversion. Reasoning modules treat a `CompiledOntology` as immutable. Neither backend may
re-interpret public OWL objects independently.

Query compilation records exact query-local feature counts and every fresh entity in the
`CompiledQuery` contract. If ELK's converter rejects a class expression or entailment axiom,
the compiler returns `encoded=None` after transactional rollback while retaining those
counts. It never substitutes a made-up supported expression; the backend/facade applies the
pinned unindexed-query value rules.

## 2. Compile phases

1. Validate import policy and public object invariants.
2. Add the four predefined entities.
3. Visit logical axioms in document order in isolated transactions.
4. Track polarity and exact upstream `Feature` occurrence counts.
5. Commit supported/partial conversions; roll back unsupported containing axioms while
   retaining the unsupported feature occurrence.
6. Freeze structural objects, assign deterministic IDs, and rewrite temporary handles.
7. Deduplicate logical axiom rows where duplicate copies cannot change ELK's result; retain
   member multiplicity inside n-ary objects.
8. Sort tables, encode a canonical semantic fingerprint, and validate the final IR.

Compilation is iterative. Nesting depth greater than Python's recursion limit is accepted up
to an explicit configurable safety ceiling of 1,000,000 nodes per axiom.

## 3. Transactional conversion

Each logical axiom is converted through an `IndexTransaction` containing provisional
entities, expressions, occurrence increments, and output rows. On success, the transaction
merges into the builder. On an `UnsupportedConstruct`:

- all provisional structural objects, supported-feature counts, and conversion rows from
  that axiom are discarded;
- exactly the rejected feature occurrence is committed to the ontology feature registry;
- default mode proceeds with the next axiom;
- strict mode raises `UnsupportedFeatureError(feature, axiom)` after rollback.

Annotations use a no-op conversion and never start a logical transaction. An annotation
property declaration also has no reasoning effect and is not an incomplete feature.

This behaviour is tested with an unsupported node after a supported first operand, ensuring
no “ghost” class or rule leaks into `all_classes()` or saturation.

## 4. Structural interning

The mutable builder interns temporary structural keys for:

```text
Class
NamedIndividual-as-context-root
ObjectProperty
DataHasValue
ObjectComplementOf
ObjectHasSelf
ObjectIntersectionOf(binary)
ObjectSomeValuesFrom
ObjectUnionOf(n-ary)
PropertyChain(named or complex suffix chain)
ClassExpressionList (n-ary disjoint members)
```

Temporary handles are opaque integers. Occurrence polarity is metadata on a structural
object, not part of its identity. The same expression appearing positively and negatively
therefore has one final expression ID with both occurrence flags.

`DataHasValue` stores its data-property entity as the expression argument and the complete
nonempty literal structural key from `owl/keys.py` as its opaque payload. That flat,
length-delimited key includes the exact pinned-ELK stored lexical form and datatype IRI;
plain and language literals use the `rdf:PlainLiteral` mapping in `parsing.md` §2.1. Backends
compare these bytes only for structural equality and perform no datatype reasoning.

Required simplifications match the pinned converter:

- empty intersection → `owl:Thing`;
- n-ary intersection → left-associated binary intersections in source operand order;
- empty union → `owl:Nothing`;
- singleton union → its operand;
- n-ary union → one list-valued union record;
- `ObjectHasValue(R,a)` → `ObjectSomeValuesFrom(R,a)` where `a` is an individual context
  root;
- empty one-of → `owl:Nothing`;
- singleton one-of → the individual context root;
- n-ary one-of → union of individual context roots;
- named property is a property chain of length one;
- complex property chain is constructed from right to left internally but serialises in
  semantic left-to-right order.

Repeated operands are retained through conversion. In particular,
`DisjointClasses(A, A)` must produce `A ∧ A SubClassOf owl:Nothing`, and duplicate
conjunct/disjunct tests must converge to ELK's result.

## 5. Axiom conversion records

### 5.1 Subclass-like rows

Emit `(sub, super)` for:

- `SubClassOf(sub, super)`;
- `ClassAssertion(C, a)` as `(a, C)`;
- `ObjectPropertyAssertion(R, a, b)` as `(a, ObjectSomeValuesFrom(R,b))`;
- `ObjectPropertyDomain(R, C)` as
  `(ObjectSomeValuesFrom(R, owl:Thing), C)`;
- `ReflexiveObjectProperty(R)` as `(owl:Thing, ObjectHasSelf(R))`;
- every direction created by `SameIndividual`;
- zero/one/many `DisjointUnion` conversions described below.

### 5.2 Equivalent-class rows

`EquivalentClasses(E0, E1, ... En)` uses `E0` as the initial hub. For each later `Ei`:

- if the hub is not a named class and `Ei` is a named class, record definition
  `(defined=Ei, definition=E0)`;
- otherwise record `(defined_or_first=E0, other=Ei)`.

The backend installs both logical directions while preserving the named definition
orientation for composed/decomposed rules. Zero/one-member equivalence produces no rule.

`SameIndividual(a0, ... an)` does not use this table: for each `ai` after the first, emit
both `(a0, ai)` and `(ai, a0)` subclass-like rows.

### 5.3 Disjointness

The binarisation threshold is exactly 2.

- For at most two member positions, emit a binary group for every ordered-position pair
  `(i,j)` with `i<j`; duplicate values in two positions remain a self-disjoint pair.
- For more than two positions, emit one n-ary member-list record with all positions and
  multiplicity preserved.

`DifferentIndividuals` uses identical conversion with individual context roots.

`DisjointUnion(D, members)` first emits the disjoint conversion, then:

- zero members: equivalent row `D ≡ owl:Nothing`;
- one member: full equivalent row `D ≡ member`;
- at least two: subclass rows `member_i SubClassOf D` only, and increment
  `DISJOINT_UNION`.

### 5.4 Object properties

- `EquivalentObjectProperties(P0, ... Pn)`: for every `Pi` after the first, emit
  `P0 SubPropertyOf Pi` and `Pi SubPropertyOf P0`.
- `SubObjectPropertyOf(P, Q)`: chain `(P)` to `Q`.
- `SubObjectPropertyOf(ObjectPropertyChain(P1,...,Pn), Q)`: preserve ordered nonempty chain
  to `Q`.
- `TransitiveObjectProperty(P)`: chain `(P,P)` to `P`.
- `ObjectPropertyRange(P,C)`: range row `(P,C)` and `OBJECT_PROPERTY_RANGE` occurrence.

Empty property chains are rejected as malformed programmatic values. Inverse property
members reject the whole axiom with `OBJECT_INVERSE_OF`.

## 6. Polarity and feature tracking

The indexer has positive, negative, and dual visitors. Dual increments both occurrence
directions. It records every feature from upstream `Feature.java`; the completeness module
later decides task impact.

Minimum required polarity events:

| Event | Feature |
|---|---|
| nonempty `ObjectOneOf` | `OBJECT_ONE_OF` |
| positive multi-`ObjectUnionOf` | `OBJECT_UNION_OF_POSITIVE` |
| negative `ObjectComplementOf` | `OBJECT_COMPLEMENT_OF_NEGATIVE` |
| positive `ObjectComplementOf` | `OBJECT_COMPLEMENT_OF_POSITIVE` (not generally incomplete, but relevant to property taxonomy) |
| negative `ObjectHasSelf` | `OBJECT_HAS_SELF_NEGATIVE` |
| positive `ObjectHasValue` | `OBJECT_HAS_VALUE_POSITIVE` |
| any `DataHasValue` | `DATA_HAS_VALUE` |
| positive bottom object property | `BOTTOM_OBJECT_PROPERTY_POSITIVE` |
| negative top object property | `TOP_OBJECT_PROPERTY_NEGATIVE` |
| positive `owl:Nothing` | `OWL_NOTHING_POSITIVE` |
| property chain length >1 or transitivity conversion | `OBJECT_PROPERTY_CHAIN` |
| property assertion/range/reflexivity | corresponding exact feature |

Feature counts are signed during a transaction but nonnegative in the frozen ontology.
Counts match the number of committed constructor occurrences ELK would track, not merely a
boolean. Completeness reasons later deduplicate them.

## 7. Occurrence-driven rule metadata

ELK attaches some rules only when an indexed object has an occurrence in the polarity that
can use the rule. pyELK's compiled IR carries enough occurrence flags for a backend to do the
same:

The frozen `ExpressionOccurrence` and `PropertyOccurrence` records are defined in
`contracts.md` and parallel the expression and object-property tables.

`expression_occurrences[i]` describes `expressions[i]`. `property_occurrences` follows the
ascending-`EntityId` subsequence of `entities` whose kind is `OBJECT_PROPERTY`; its length
must equal that filtered subsequence. Both are explicit fields of `CompiledOntology` and
explicit IR sections.

Backends MUST use them to register:

- conjunction composition from conjunct occurrences;
- conjunction decomposition;
- existential link/propagation rules;
- complement contradiction rules;
- disjoint-member rules;
- union-from-disjunct rules;
- named-class definition/equivalence rules;
- property range and chain composition rules.

WP4 freezes an explicit generated rule-registration manifest in
`src/pyelk/indexing/registration.py`; Python and Rust tests consume the same golden rows.

## 8. Deterministic freezing

After conversion:

1. collect every committed entity and predefined entity;
2. assign entity IDs by `(EntityKind, UTF-8 IRI)`;
3. topologically assign expressions so all arguments precede their parent, breaking ties by
   `(ExpressionTag, payload, rewritten argument tuple)`;
4. assign property-chain IDs lexicographically by property-ID tuple;
5. rewrite and sort conversion tables;
6. preserve multiplicity within expression/member tuples but deduplicate identical axiom
   rows/groups;
7. encode all 79 feature counts in the frozen upstream enum order;
8. compute BLAKE2b-256 of canonical logical Functional Syntax plus the IR schema major.

Freezing the same `Ontology` twice is byte-identical under every `PYTHONHASHSEED`. Semantic
input permutations need only produce equal reasoning results; byte-identical IR for every
permutation is a desirable optimisation, not a v1 requirement where ELK's conversion
orientation is order-sensitive internally.

## 9. Validation invariants

- Every referenced ID exists and has the required entity/expression kind.
- Expression arguments precede the expression in final topological order.
- Intersection has exactly two final arguments; union has at least two.
- Some-values-from has one object-property entity ID and one expression ID.
- Has-self has one object-property ID.
- Property chains are nonempty and contain only object-property IDs.
- Conversion rows and top-level tables are sorted unique.
- Disjoint-group member arrays retain positions and may contain duplicate IDs.
- Feature counts are nonnegative and have the exact manifest length.
- No object from a rolled-back axiom is reachable unless another committed axiom uses it.

The Python decoder and Rust decoder both reject a violated invariant before saturation.

## 10. Tests

- Port conversion tests around declarations, equivalences, disjointness, polarity, property
  ranges/chains, same/different individuals, and unsupported rollback.
- Golden-test every matrix row in `compatibility.md`, including feature counts.
- Property-test interning: equal structural keys share IDs; unequal keys do not.
- Property-test compile/encode/decode and hash-seed determinism.
- Compare Java and Python entity enumeration so ignored-only entities do not leak.
- Fuzz the binary decoder independently in Python and Rust with truncated, reordered,
  oversized, invalid-enum, invalid-UTF-8, bad-CSR, and bad-checksum inputs.
