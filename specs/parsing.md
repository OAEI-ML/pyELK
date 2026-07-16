# OWL Values and Functional-Syntax Parsing

The native ELK core reads OWL 2 Functional-Style Syntax through
`elk-owl-parsing-javacc`; other serialisations arrive through the out-of-scope OWL API
adapter. pyELK v1 therefore implements Functional Syntax and programmatic values only.

References:

- [OWL 2 structural specification and Functional Syntax](https://www.w3.org/TR/owl2-syntax/)
- [`Owl2FunctionalStyleParser.jj`](https://github.com/liveontologies/elk-reasoner/blob/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-owl-parent/elk-owl-parsing-javacc/src/main/javacc/org/semanticweb/elk/owl/parsing/javacc/Owl2FunctionalStyleParser.jj)
- [`Owl2ParserLoader`](https://github.com/liveontologies/elk-reasoner/blob/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-reasoner/src/main/java/org/semanticweb/elk/loading/Owl2ParserLoader.java)
- [`OwlFunctionalStylePrinter`](https://github.com/liveontologies/elk-reasoner/blob/b8ac5ce83db0704a7359d96aa382891e2f547863/elk-owl-parent/elk-owl-model/src/main/java/org/semanticweb/elk/owl/printers/OwlFunctionalStylePrinter.java)

## 1. Value rules

Public OWL values are immutable, slotted, transitively hashable dataclasses. They contain no
integer reasoner IDs and do not hold a reference to an ontology, parser, or backend.

```python
@dataclass(frozen=True, slots=True, order=True)
class IRI:
    value: str

class OWLObject: ...
class Entity(OWLObject): ...
class ClassExpression(OWLObject): ...
class Individual(OWLObject): ...
class Axiom(OWLObject):
    annotations: tuple[Annotation, ...]
```

Recursive OWL dataclasses use `eq=False` and inherit iterative structural equality/hash from
`OWLObject`; generated dataclass tuple equality/hash is forbidden because it consumes the
Python call stack on deeply nested expressions. That equality preserves the public stored
field order and annotations. Simple leaf values may use generated equality.

Concrete inheritance follows the OWL surface model: `Class` is both `Entity` and
`ClassExpression`; `NamedIndividual` is both `Entity` and `Individual`, but is not a public
`ClassExpression`; and `ObjectProperty` is both `Entity` and
`ObjectPropertyExpression`. Indexing maps an individual to an internal nominal context root,
which is an `ExpressionRecord`, without making invalid public class expressions legal.

All constructors accept `IRI | str` where an IRI is expected and coerce strings to `IRI`.
IRIs are Unicode strings but canonical ordering compares their UTF-8 bytes. Empty IRIs and
strings containing illegal control characters raise `ValueError`; pyELK does not perform
network normalisation or percent-decoding.

The public model preserves operand and axiom order for non-canonical printing. The compiler
creates its own canonical structural keys. This separation is important: property-chain
order is semantic, while many class/axiom operand lists are semantically unordered; repeated
members can still be significant for ELK conversions such as `DisjointClasses(A, A)`.

## 2. Required public values

### 2.1 Entities and literals

```text
Class
NamedIndividual
ObjectProperty
DataProperty
Datatype
AnnotationProperty
Literal(lexical_form, datatype=None, language=None)
Annotation(property, value, annotations=())
```

`Literal` rejects simultaneous `datatype` and `language`. Language-tagged literals compare
using the exact case-sensitive spelling retained by ELK 0.6.0; pyELK MUST NOT lowercase the
tag even though BCP 47 language tags are semantically case-insensitive. Lexical forms are
not datatype-value-normalised because ELK's core does not implement datatype reasoning.

Literal structural identity uses a dedicated pinned-ELK key. A parsed untagged plain literal
maps to `(lexical + "@", rdf:PlainLiteral)`; a language literal maps to
`(lexical + "@" + language, rdf:PlainLiteral)`; and an explicitly typed literal uses its
lexical form unchanged with its datatype. The public `language` field stores the tag without
the leading `@`. This intentionally preserves ELK's observable structural quirks for
`DataHasValue`. The public printer still emits valid Functional Syntax (`"x"`, `"x"@en`, or
`"x"^^datatype`) instead of copying ELK's internal lexical storage.

Predefined singletons are exported as `OWL_THING`, `OWL_NOTHING`,
`OWL_TOP_OBJECT_PROPERTY`, and `OWL_BOTTOM_OBJECT_PROPERTY`. Constructing the same IRI
directly yields an equal value; identity is not promised.

### 2.2 Indexed class expressions

```python
@dataclass(frozen=True, slots=True, eq=False)
class ObjectIntersectionOf(ClassExpression):
    operands: tuple[ClassExpression, ...]

@dataclass(frozen=True, slots=True, eq=False)
class ObjectSomeValuesFrom(ClassExpression):
    property: ObjectPropertyExpression
    filler: ClassExpression

@dataclass(frozen=True, slots=True, eq=False)
class ObjectHasValue(ClassExpression):
    property: ObjectPropertyExpression
    individual: NamedIndividual

@dataclass(frozen=True, slots=True, eq=False)
class ObjectHasSelf(ClassExpression):
    property: ObjectPropertyExpression

@dataclass(frozen=True, slots=True, eq=False)
class ObjectOneOf(ClassExpression):
    individuals: tuple[Individual, ...]

@dataclass(frozen=True, slots=True, eq=False)
class ObjectComplementOf(ClassExpression):
    operand: ClassExpression

@dataclass(frozen=True, slots=True, eq=False)
class ObjectUnionOf(ClassExpression):
    operands: tuple[ClassExpression, ...]

@dataclass(frozen=True, slots=True, eq=False)
class DataHasValue(ClassExpression):
    property: DataProperty
    value: Literal
```

`ObjectPropertyExpression` is a named `ObjectProperty` in the supported fragment. The
individual-to-class-expression bridge exists only in the compiled IR for nominals,
assertions, and query processing.

Programmatic constructors permit zero/one/n-ary intersection, union, and one-of values so
the edge conversions in `compatibility.md` are testable even where the W3C surface grammar
imposes a minimum arity.

### 2.3 Supported logical axioms

```text
Declaration(entity)
SubClassOf(sub_class, super_class)
EquivalentClasses(class_expressions)
DisjointClasses(class_expressions)
DisjointUnion(defined_class, class_expressions)
ClassAssertion(class_expression, individual)
SameIndividual(individuals)
DifferentIndividuals(individuals)
ObjectPropertyAssertion(property, subject, object)
EquivalentObjectProperties(properties)
SubObjectPropertyOf(sub_property_or_chain, super_property)
ObjectPropertyDomain(property, domain)
ObjectPropertyRange(property, range)
ReflexiveObjectProperty(property)
TransitiveObjectProperty(property)
ObjectPropertyChain(properties)
```

Fields use tuples and the exact Functional Syntax argument order. Arity validation matches
the W3C grammar when parsed, while direct constructors allow edge-case arities used by ELK
unit tests.

### 2.4 Unsupported but diagnosable objects

The parser must accept every constructor recognised by the pinned JavaCC grammar even when
reasoning ignores it. Implementing a public dataclass for all 100+ OWL object types is not
required. Unsupported values use lossless generic nodes:

```python
@dataclass(frozen=True, slots=True, eq=False)
class UnsupportedExpression(ClassExpression):
    constructor: str
    arguments: tuple[OWLObject | IRI | Literal | str | int, ...]
    feature: str

@dataclass(frozen=True, slots=True, eq=False)
class UnsupportedAxiom(Axiom):
    constructor: str
    arguments: tuple[OWLObject | IRI | Literal | str | int, ...]
    feature: str
    annotations: tuple[Annotation, ...] = ()

@dataclass(frozen=True, slots=True, eq=False)
class AnnotationAxiom(Axiom):
    constructor: str
    arguments: tuple[object, ...]
    annotations: tuple[Annotation, ...] = ()
```

The `feature` is the exact upstream `Feature` enum name for the current constructor and, for
polarity-sensitive expressions, is finalised during indexing. Generic nodes must print back
to valid Functional Syntax without retaining original whitespace.

Required generic coverage includes all data restrictions/axioms, anonymous individuals,
universal/cardinality restrictions, inverse properties, negative assertions, functional,
inverse-functional, symmetric/asymmetric/irreflexive and disjoint properties, keys,
datatype definitions, SWRL, and remaining annotation forms.

## 3. Factories and convenience functions

`pyelk.owl` exports Pythonic helpers without hiding the value classes:

```python
def class_(iri: IRI | str) -> Class: ...
def individual(iri: IRI | str) -> NamedIndividual: ...
def object_property(iri: IRI | str) -> ObjectProperty: ...
def intersection(*xs: ClassExpression) -> ClassExpression: ...
def some(prop: ObjectProperty, filler: ClassExpression) -> ObjectSomeValuesFrom: ...
def has_value(prop: ObjectProperty, value: NamedIndividual) -> ObjectHasValue: ...
```

`intersection()` is a semantic convenience: zero returns `OWL_THING`, one returns its
operand, and multiple returns `ObjectIntersectionOf`. Raw class constructors never perform
those simplifications.

## 4. Lexer

The lexer is iterative and incremental. It accepts `str`, UTF-8 `bytes`, text/binary streams,
and paths without reading the entire document into one string. A bounded lookahead buffer is
allowed. Token objects contain kind, decoded value, byte offset, line, and column.

It implements the pinned grammar's:

- identifiers and all OWL Functional Syntax keywords;
- full IRIs `<...>` and prefix names, including default prefix `:`;
- `Prefix(name:=<iri>)` declarations;
- quoted strings and the standard escapes used by OWL Functional Syntax;
- language tags and datatype markers;
- integers/nonnegative integers used by unsupported cardinality nodes;
- whitespace and `#` comments outside IRIs/strings;
- exact end-of-input and balanced-parenthesis errors.

Invalid UTF-8, illegal escapes, unclosed strings/IRIs, unknown prefixes, and
unexpected tokens raise `ParseError` with the first offending span. Parser tests compare
category and position, not Java's English wording.

## 5. Parser and document handling

```python
def _parse_document(source: Source) -> ParsedDocument: ...
def iter_axioms(source: Source) -> Iterator[Axiom]: ...
def parse_class_expression(
    text: str, *, prefixes: Mapping[str, IRI] | None = None
) -> ClassExpression: ...
def parse_axiom(text: str, *, prefixes: Mapping[str, IRI] | None = None) -> Axiom: ...
```

`ParsedDocument` is a private acyclic hand-off containing the document fields; public
`Ontology.parse` in `ontology.py` calls `_parse_document` and constructs the immutable
document. Thus `parsing` imports only `owl`, while `ontology` imports `owl` and `parsing`.
`iter_axioms` yields after a complete axiom and keeps at most the current object graph plus a
small token buffer. `Ontology.parse` materialises the yielded tuple. The default parser does
not start a worker thread; Python callers can place iteration on their own thread.

Document rules:

1. Prefix declarations apply to the remainder of the document; duplicate prefix names use
   the last declaration, matching the parser fixture oracle.
2. `Ontology(` may contain optional ontology/version IRIs, direct imports, ontology
   annotations, and axioms.
3. Import declarations are recorded but never opened.
4. Axiom annotations are parsed recursively and retained.
5. Unknown constructor keywords are syntax errors. Known-but-unsupported constructors
   become generic nodes, not parse errors.
6. An unsupported constructor nested in a supported axiom remains nested so indexing can
   transactionally reject the containing axiom and record the correct feature/polarity.
7. The parser accepts the 124 pinned ELK Functional Syntax ontology fixtures.

## 6. Canonical printer

```python
def functional_syntax(obj: OWLObject | Ontology, *, canonical: bool = True) -> str: ...
```

Both modes produce valid UTF-8 Functional Syntax with escaped literals and full IRIs.

- `canonical=False` preserves stored operand, axiom, annotation, and import order.
- `canonical=True` uses full IRIs, sorts prefix-free axioms by structural key, sorts
  semantically unordered operands while retaining multiplicity, preserves property-chain
  order, and uses one space between tokens with two-space ontology indentation.
- Canonical printing is idempotent: parse → canonical print → parse → canonical print is
  byte-identical.
- Canonical printing does not discard annotations or unsupported nodes.

Canonical output is used as the Java-oracle input and source fingerprint. It is not required
to match ELK's printer whitespace.

## 7. Structural keys

Every OWL value has an internal `structural_key(obj) -> bytes` defined in `owl/keys.py`.
It is a flat, length-delimited canonical token encoding built with an explicit stack; nested
Python tuples are not used as recursive comparison keys.

- entities encode entity-kind and UTF-8 IRI;
- literals encode the pinned ELK lexical/datatype key described in §2.1;
- ordered constructors encode constructor tag and length-delimited child keys in order;
- semantically unordered constructors/axioms encode constructor tag and sorted child keys
  while retaining duplicates;
- document keys append sorted annotation keys; logical indexing keys omit annotations;
- a separate order-preserving field key backs public `__eq__`/`__hash__`.

Keys and public hashes must not depend on Python's salted hash for ordering or compilation;
`__hash__` may fold the flat field-key bytes with Python's process-local hash because hash
values themselves are not serialised. Equality remains exact field equality and is
collision-safe.

## 8. Test requirements

- Port the upstream implementation parser/printer tests and `owl2primer.owl` fixture with
  attribution.
- Round-trip every ELK `test_input/**/*.owl` file.
- Property-test strings, Unicode IRIs/literals, prefix expansion, arbitrary nesting, and
  chunk boundaries one byte before/inside/after every token type.
- Assert streaming memory is `O(max-axiom-size + stored-output)`, not `O(file-size)` for
  `iter_axioms`.
- Test all unsupported constructors map to the exact feature name used by indexing.
- Test malformed input never panics, loops, or allocates from an unbounded declared length.
