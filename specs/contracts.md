# Public and Internal Contracts

This document freezes the values exchanged between independently implemented components.
Work packages may add private helpers, but MUST NOT change these contracts without an
integration decision and matching spec update.

## 1. Public package surface

`pyelk.__init__` exports only:

```python
from pyelk.api import Ontology, Reasoner
from pyelk.config import ReasonerConfig
from pyelk.result import (
    CompletenessIssue,
    EntityNode,
    InstanceTaxonomy,
    PolicyFeature,
    ReasoningResult,
    Taxonomy,
)
from pyelk.owl import *  # explicit __all__, described in parsing.md
from pyelk.backends import backend_report
```

Private compiled/native types are never re-exported.

## 2. `Ontology`

```python
@dataclass(frozen=True, slots=True)
class Ontology:
    axioms: tuple[Axiom, ...] = ()
    ontology_iri: IRI | None = None
    version_iri: IRI | None = None
    imports: tuple[IRI, ...] = ()
    annotations: tuple[Annotation, ...] = ()

    @classmethod
    def parse(
        cls,
        source: str | bytes | PathLike[str] | TextIO | BinaryIO,
        *,
        format: Literal["functional"] = "functional",
    ) -> Ontology: ...

    def functional_syntax(self, *, canonical: bool = True) -> str: ...
    def with_axioms(self, *axioms: Axiom) -> Ontology: ...
```

`str` is interpreted as Functional Syntax text when it contains a newline or begins with
`Prefix(` / `Ontology(` after whitespace; otherwise a string is a path. Callers can avoid
heuristics with an open stream or `Path`.

`Ontology` preserves parsed order and duplicates for round trips. Compilation treats logical
axioms as an ontology set after feature scanning. `with_axioms` returns a new value.

Imports are metadata only. The parser never resolves or fetches them. Passing an ontology
with nonempty `imports` to `Reasoner` raises `UnresolvedImportError` unless
`ReasonerConfig(ignore_imports=True)`; ignored imports add a completeness issue. A caller
that loads an import closure combines its axioms explicitly.

## 3. Configuration

```python
@dataclass(frozen=True, slots=True)
class ReasonerConfig:
    backend: Literal["auto", "python", "rust"] = "auto"
    workers: int = 0
    allow_fresh_entities: bool = True
    unsupported: Literal["ignore", "error"] = "ignore"
    ignore_imports: bool = False
```

- `workers=0` means logical CPU count for Rust and one worker for Python.
- `workers<0` raises `ValueError`.
- Python accepts `workers>1` but reports `effective_workers=1`; it MUST NOT change results.
- Configuration is snapshotted by `Reasoner` and cannot be mutated.
- Environment backend overrides are resolved as specified in `native-packaging.md`.

## 4. Public `Reasoner`

```python
class Reasoner:
    def __init__(self, ontology: Ontology, config: ReasonerConfig | None = None): ...
    def close(self) -> None: ...
    def __enter__(self) -> Reasoner: ...
    def __exit__(self, *exc_info: object) -> None: ...

    @property
    def backend(self) -> BackendInfo: ...

    def is_consistent(self) -> ReasoningResult[bool]: ...
    def is_inconsistent(self) -> ReasoningResult[bool]: ...
    def classify(self) -> ReasoningResult[Taxonomy[Class]]: ...
    def classify_object_properties(
        self,
    ) -> ReasoningResult[Taxonomy[ObjectProperty]]: ...
    def realize(self) -> ReasoningResult[InstanceTaxonomy]: ...

    def is_satisfiable(self, expression: ClassExpression) -> ReasoningResult[bool]: ...
    def equivalent_classes(
        self, expression: ClassExpression
    ) -> ReasoningResult[tuple[EntityNode[Class], ...]]: ...
    def subclasses(
        self, expression: ClassExpression, *, direct: bool = False
    ) -> ReasoningResult[tuple[EntityNode[Class], ...]]: ...
    def superclasses(
        self, expression: ClassExpression, *, direct: bool = False
    ) -> ReasoningResult[tuple[EntityNode[Class], ...]]: ...
    def instances(
        self, expression: ClassExpression, *, direct: bool = False
    ) -> ReasoningResult[tuple[EntityNode[NamedIndividual], ...]]: ...
    def types(
        self, individual: NamedIndividual, *, direct: bool = False
    ) -> ReasoningResult[tuple[EntityNode[Class], ...]]: ...

    def sub_object_properties(
        self, prop: ObjectProperty, *, direct: bool = False
    ) -> ReasoningResult[tuple[EntityNode[ObjectProperty], ...]]: ...
    def super_object_properties(
        self, prop: ObjectProperty, *, direct: bool = False
    ) -> ReasoningResult[tuple[EntityNode[ObjectProperty], ...]]: ...
    def equivalent_object_properties(
        self, prop: ObjectProperty
    ) -> ReasoningResult[EntityNode[ObjectProperty]]: ...

    def is_entailed(self, axiom: Axiom) -> ReasoningResult[bool]: ...
    def all_classes(self) -> tuple[Class, ...]: ...
    def all_named_individuals(self) -> tuple[NamedIndividual, ...]: ...
    def all_object_properties(self) -> tuple[ObjectProperty, ...]: ...
```

All operations after `close()` raise `ReasonerClosedError`. `close()` is idempotent. No
method mutates the source ontology. Taxonomy/realization stages are cached per session.

One `Reasoner` is safe to call from multiple Python threads, matching the synchronized ELK
core surface. A facade-owned reentrant session lock serializes stage/query/diagnostic calls
and `close()`; native calls may release the GIL while that lock remains held and use internal
workers. `close()` waits for the current call, then closes exactly once. Returned public
values are immutable and remain usable. Parallel independent requests use separate reasoner
sessions; concurrent mutation of one session is not an optimisation target.

For an unsupported entailment query, default mode returns `value=False` and an incomplete
query feature, exactly as ELK's unindexed query state does. Strict mode raises
`UnsupportedQueryError`.

## 5. Public result values

```python
T = TypeVar("T")
E = TypeVar("E", bound=Entity)

class ReasoningTask(str, Enum):
    CONSISTENCY = "consistency"
    CLASS_TAXONOMY = "class_taxonomy"
    OBJECT_PROPERTY_TAXONOMY = "object_property_taxonomy"
    REALIZATION = "realization"
    CLASS_EXPRESSION_QUERY = "class_expression_query"
    ENTAILMENT_QUERY = "entailment_query"

class PolicyFeature(str, Enum):
    IGNORED_IMPORT = "PYELK_IGNORED_IMPORT"

@dataclass(frozen=True, slots=True, order=True)
class CompletenessIssue:
    task: ReasoningTask
    features: tuple[str, ...]       # upstream Feature names or PolicyFeature values
    constructors: tuple[str, ...]
    polarities: tuple[Literal["ANY", "NEGATIVE", "POSITIVE"], ...]

@dataclass(frozen=True, slots=True)
class ReasoningResult(Generic[T]):
    value: T
    complete: bool
    reasons: tuple[CompletenessIssue, ...]

    def require_complete(self) -> T: ...

@dataclass(frozen=True, slots=True)
class EntityNode(Generic[E]):
    members: tuple[E, ...]

    @property
    def canonical_member(self) -> E: ...

@dataclass(frozen=True, slots=True)
class Taxonomy(Generic[E]):
    nodes: tuple[EntityNode[E], ...]
    direct_edges: tuple[tuple[EntityNode[E], EntityNode[E]], ...]  # sub -> super
    top: EntityNode[E]
    bottom: EntityNode[E]

    def node(self, entity: E) -> EntityNode[E] | None: ...
    def subs(self, entity: E, *, direct: bool = False) -> tuple[EntityNode[E], ...]: ...
    def supers(self, entity: E, *, direct: bool = False) -> tuple[EntityNode[E], ...]: ...

@dataclass(frozen=True, slots=True)
class InstanceTaxonomy:
    class_taxonomy: Taxonomy[Class]
    instances: tuple[EntityNode[NamedIndividual], ...]
    direct_types: tuple[tuple[EntityNode[NamedIndividual], EntityNode[Class]], ...]
```

The `ReasoningTask`, `PolicyFeature`, and `CompletenessIssue` identities are defined early in
`reasoning/contracts.py` by WP0 so the completeness package can use them without depending
on the later facade. `result.py` re-exports those same objects and adds `ReasoningResult` and
the public graph values; it MUST NOT define second classes with equal-looking fields.

Empty `EntityNode` is invalid. Members, nodes, edges, and reasons are deduplicated and sorted
by canonical structural key. `complete == (not reasons)` is asserted in `__post_init__`.
`require_complete()` returns `value` or raises `IncompleteReasoningError(reasons)`.

## 6. Public exception categories

```text
PyElkError
├── ParseError(source, line, column, token, detail)
├── UnsupportedFeatureError(feature, axiom)
├── UnsupportedQueryError(feature, query)
├── UnresolvedImportError(imports)
├── FreshEntityError(entities)
├── IncompleteReasoningError(reasons)
├── BackendUnavailableError(requested, reason)
├── BackendProtocolError(expected, actual)
├── ReasonerClosedError
└── InternalReasonerError(stage, backend, detail)
```

Memory exhaustion, `KeyboardInterrupt`, and process-level failures are not wrapped. Rust
panics MUST be caught at the PyO3 boundary and converted to `InternalReasonerError`; safe
inputs MUST NOT unwind into CPython.

## 7. Canonical compiled ontology

`indexing` is the sole owner of the backend-neutral compiled representation.

```python
EntityId = NewType("EntityId", int)
ExpressionId = NewType("ExpressionId", int)
PropertyChainId = NewType("PropertyChainId", int)
DisjointGroupId = NewType("DisjointGroupId", int)
ReadableBuffer = bytes | bytearray | memoryview

class EntityKind(IntEnum):
    CLASS = 0
    NAMED_INDIVIDUAL = 1
    OBJECT_PROPERTY = 2
    DATA_PROPERTY = 3
    DATATYPE = 4
    ANNOTATION_PROPERTY = 5

class ExpressionTag(IntEnum):
    CLASS = 0
    INDIVIDUAL = 1
    OBJECT_INTERSECTION_OF = 2
    OBJECT_SOME_VALUES_FROM = 3
    OBJECT_HAS_SELF = 4
    DATA_HAS_VALUE = 5
    OBJECT_COMPLEMENT_OF = 6
    OBJECT_UNION_OF = 7

@dataclass(frozen=True, slots=True)
class EntityRecord:
    kind: EntityKind
    iri: str

@dataclass(frozen=True, slots=True)
class ExpressionRecord:
    tag: ExpressionTag
    arguments: tuple[int, ...]
    payload: bytes = b""

@dataclass(frozen=True, slots=True)
class ExpressionOccurrence:
    negative: int
    positive: int

@dataclass(frozen=True, slots=True)
class PropertyOccurrence:
    negative: int
    positive: int

class QueryIRKind(IntEnum):
    CLASS_EXPRESSION = 0
    ENTAILMENT = 1

@dataclass(frozen=True, slots=True)
class QueryEntityRecord:
    entity: EntityRecord
    ontology_id: EntityId | None

@dataclass(frozen=True, slots=True)
class QueryIR:
    kind: QueryIRKind
    entities: tuple[QueryEntityRecord, ...]
    expressions: tuple[ExpressionRecord, ...]
    expression_occurrences: tuple[ExpressionOccurrence, ...]
    property_occurrences: tuple[PropertyOccurrence, ...]
    root_expression: ExpressionId | None
    subsumption_obligations: tuple[tuple[ExpressionId, ExpressionId], ...]

    def encode(self) -> bytes: ...
    @classmethod
    def decode(cls, data: ReadableBuffer) -> QueryIR: ...

@dataclass(frozen=True, slots=True)
class CompiledOntology:
    entities: tuple[EntityRecord, ...]
    expressions: tuple[ExpressionRecord, ...]
    expression_occurrences: tuple[ExpressionOccurrence, ...]
    property_occurrences: tuple[PropertyOccurrence, ...]  # ascending object-property IDs
    property_chains: tuple[tuple[EntityId, ...], ...]
    subclass_axioms: tuple[tuple[ExpressionId, ExpressionId], ...]
    equivalent_class_axioms: tuple[tuple[ExpressionId, ExpressionId], ...]
    disjoint_groups: tuple[tuple[ExpressionId, ...], ...]
    subproperty_axioms: tuple[tuple[PropertyChainId, EntityId], ...]
    property_ranges: tuple[tuple[EntityId, ExpressionId], ...]
    feature_counts: tuple[int, ...]
    source_fingerprint: bytes

    def encode(self) -> bytes: ...
    @classmethod
    def decode(cls, data: ReadableBuffer) -> CompiledOntology: ...

@dataclass(frozen=True, slots=True)
class CompiledQuery:
    encoded: bytes | None
    feature_counts: tuple[int, ...]
    fresh_entities: tuple[EntityRecord, ...]
```

`subclass_axioms` includes conversions of class assertions, object-property assertions,
domains, reflexivity, same individuals, disjoint-union special cases, and explicit
subclasses. `equivalent_class_axioms` retains ELK's chosen definition orientation; it MUST
NOT be blindly expanded into two subclass rows because indexed-class definition rules use
the orientation. `disjoint_groups` preserves n-ary grouping.

IDs are `u32`; `0xffffffff` is reserved and never assigned. Entity IDs are ordered by
`(kind, UTF-8 IRI bytes)`. Expression IDs are assigned by a deterministic topological order
over `(tag, payload, argument IDs)`. A cyclic expression graph is invalid at the public model
layer. Property chains and every axiom table are deduplicated and lexicographically sorted.
The frozen ELK feature-count vector has exactly 79 positions in the upstream `Feature.java`
order; WP3 replaces this numeric guard with the checked-in named manifest without changing
the codec width.

`ExpressionRecord.payload` is empty for every v1 tag except `DATA_HAS_VALUE`. For that tag,
it is the nonempty, flat, length-delimited literal structural-key bytes defined by WP1. The
key includes ELK's exact stored lexical form and datatype IRI (including the `rdf:PlainLiteral`
mapping for plain/language literals). Datatype identity therefore affects structural
interning even though neither backend performs datatype reasoning.

Predefined entities are always present:

```text
owl:Thing
owl:Nothing
owl:topObjectProperty
owl:bottomObjectProperty
```

The IR does not contain Python hashes, object addresses, source line numbers, proof
provenance, or backend-specific storage.

### 7.1 Binary encoding

`encode()` is the only PyO3 input. It uses:

- magic `b"PYELKIR\0"`;
- little-endian `u16` schema major and minor;
- a section directory of `(tag:u16, offset:u64, length:u64, count:u64)`;
- UTF-8 string blob plus `u64` offsets;
- fixed-width primitive columns and CSR (`u64` offsets, `u32` values) for variable rows;
- BLAKE2b-256 checksum over all sections.

Unknown major versions are rejected. Unknown optional sections in the same major are
skipped. All offsets, lengths, enum values, UTF-8, ID ranges, CSR monotonicity, and checksum
are validated before allocation proportional to an untrusted field. `CompiledOntology`
encoding is deterministic and byte-identical across Python versions and architectures.

Exact section tags and the initial `(major=1, minor=0)` codec are owned by WP0 in
`src/pyelk/indexing/codec.py`; subsequent work packages consume but do not edit that file.

`CompiledQuery.feature_counts` uses the same frozen enum length/order as the ontology.
`fresh_entities` is sorted by `(kind, IRI)` and lists every query entity absent from the
session symbol table. `encoded=None` means the pinned ELK query converter could not index
the query; it is not a transport failure. Class-query backends return the operation-specific
ELK unindexed-query fallback, while unsupported entailment returns false. A non-`None`
payload is a self-contained v1 mini-IR with query-local entity/expression tables and session
entity references; both decoders apply the same defensive checks as ontology IR.

In that mini-IR, `entities` is sorted by `(kind, UTF-8 IRI bytes)`. An existing entity has
its session `ontology_id`; a fresh entity has `ontology_id=None`. The latter records, in
table order, MUST equal `CompiledQuery.fresh_entities`. A class-expression mini-IR has one
`root_expression` and no obligations. An entailment mini-IR has no root and contains its
canonical sorted-unique normalized subsumption obligations.

## 8. Backend protocol

The public facade creates exactly one backend session per `Reasoner`.

```python
@dataclass(frozen=True, slots=True)
class BackendInfo:
    name: Literal["python", "rust"]
    implementation_version: str
    ir_major: int
    ir_minor: int
    requested_workers: int
    effective_workers: int
    native_available: bool
    fallback_reason: str | None

@dataclass(frozen=True, slots=True)
class BackendAvailability:
    name: Literal["python", "rust"]
    available: bool | None  # None when a hard pure-mode guard forbids probing
    implementation_version: str | None
    ir_major: int | None
    ir_minor: int | None
    abi: str | None
    reason: str | None

@dataclass(frozen=True, slots=True)
class BackendReport:
    requested: str  # effective config/environment text, including invalid diagnostic input
    selected: Literal["python", "rust"] | None
    python: BackendAvailability
    rust: BackendAvailability
    selection_error: str | None

def backend_report() -> BackendReport: ...

class BackendConfig(Protocol):
    workers: int

class BackendFactory(Protocol):
    def create_session(
        self, compiled: CompiledOntology, config: BackendConfig
    ) -> BackendSession: ...

class BackendSession(Protocol):
    @property
    def info(self) -> BackendInfo: ...
    def close(self) -> None: ...
    def is_inconsistent(self) -> bool: ...  # true iff ontology is inconsistent
    def class_taxonomy(self) -> RawTaxonomy: ...
    def object_property_taxonomy(self) -> RawTaxonomy: ...
    def realization(self) -> RawRealization: ...
    def query_class_expression(
        self, encoded_expression: bytes | None, kind: QueryKind, direct: bool
    ) -> RawQueryResult: ...
    def entails(self, encoded_axiom: bytes | None) -> bool: ...
    def diagnostics(self) -> Mapping[str, int | float | str | bool]: ...
```

Raw results contain only validated `u32` entity IDs, node indices, booleans, and edges.
`api.py` is solely responsible for converting them to public values and attaching
completeness issues. Both adapters validate raw IDs and taxonomy invariants; invalid native
output raises `BackendProtocolError`, never an index error or crash.

The raw values are frozen as:

```python
class QueryKind(IntEnum):
    SATISFIABLE = 0
    EQUIVALENT_CLASSES = 1
    SUBCLASSES = 2
    SUPERCLASSES = 3
    INSTANCES = 4

QueryResultEntityId = NewType("QueryResultEntityId", int)

@dataclass(frozen=True, slots=True)
class RawTaxonomy:
    nodes: tuple[tuple[EntityId, ...], ...]
    direct_edges: tuple[tuple[int, int], ...]  # node indices, sub -> super
    top: int
    bottom: int

@dataclass(frozen=True, slots=True)
class RawRealization:
    class_taxonomy: RawTaxonomy
    instance_nodes: tuple[tuple[EntityId, ...], ...]
    direct_types: tuple[tuple[int, int], ...]  # instance-node -> class-node

@dataclass(frozen=True, slots=True)
class RawQueryResult:
    kind: QueryKind
    boolean: bool | None = None
    nodes: tuple[tuple[QueryResultEntityId, ...], ...] = ()
```

`SATISFIABLE` requires `boolean` and empty `nodes`; all other kinds require `boolean=None`.
Node/member/edge arrays are sorted unique and use canonical indices. Named-individual
`types()` is selected from `RawRealization`; it is not a separate query kind.

Query-result IDs use one deterministic ephemeral namespace. Existing ontology entities keep
their `EntityId` numeric value. For `ontology_entity_count = len(compiled.entities)`, fresh
entity rank `r` in `CompiledQuery.fresh_entities` is encoded as
`ontology_entity_count + r`; the sum MUST remain below reserved `0xffffffff`. These extended
IDs are legal only in `RawQueryResult`. The facade validates the range against the compiled
ontology/query pair, maps it back to the corresponding entity, and never exposes the numeric
ID publicly.

Rust serialises these records with the sibling wire format in
`reasoning/wire.py`: magic `b"PYELKRAW"`, v1.0 little-endian header, result-kind tag,
section directory, CSR node members, fixed-width edges/indices, and BLAKE2b-256 checksum.
The Python backend returns dataclasses directly; the Rust adapter decodes wire bytes into the
same dataclasses. Unknown major versions, invalid tags/indices/CSR/checksum, trailing required
data, and result-kind field violations raise `BackendProtocolError`.

Backend methods are coarse-grained and stage-cached. `query_class_expression` accepts a
mini-IR using the same expression codec; the session may intern and cache it privately.
Neither backend may call public Python API methods while computing.

## 9. Completeness evaluator contract

`reasoning/completeness.py` is backend-independent. It owns an enum with every ontology and
query feature from upstream `Feature.java`, consumes the ontology and query count vectors,
and implements:

```python
def issues_for(
    task: ReasoningTask,
    feature_counts: Sequence[int],
    *,
    query_feature_counts: Sequence[int] = (),
    policy_features: Sequence[PolicyFeature] = (),
    inconsistent: bool = False,
) -> tuple[CompletenessIssue, ...]: ...
```

This is the only place that decides completeness. Backends MUST NOT suppress or invent
issues. When `inconsistent=True`, it returns no issues for the quiet-collapse task set named
in `compatibility.md` and otherwise applies the normal task monitor. This reproduces the
explicit `getNoIncompletenessMonitor()` fallbacks in pinned ELK without discarding the
session's feature counts. The feature enum integer order is frozen in the IR codec and
tested against a checked-in manifest generated from the pinned Java enum.

`PolicyFeature` values are a separate, non-IR namespace for pyELK ingestion choices that
have no ELK `Feature` counterpart. `IGNORED_IMPORT` is the only v1 member. It is attached to
every reasoning task when imports were explicitly ignored and is never suppressed by an
inconsistent quiet fallback; otherwise pyELK could incorrectly claim completeness for an
unknown import closure. Policy values MUST NOT be inserted into the upstream enum or feature
count vectors. Its issue has `features=("PYELK_IGNORED_IMPORT",)`,
`constructors=("Import",)`, and `polarities=("ANY",)`.

Both vectors must have the manifest length. For class/entailment queries, evaluator order
and membership reproduce `IncompletenessManager.getQueryMonitor()`: the general ontology
monitor, unsupported-query-type monitor over the query vector, and top monitor over the
elementwise ontology-plus-query counts are combined and then canonical-deduplicated. For
non-query tasks, a nonempty query vector is a contract error.

## 10. Cross-component test doubles

WP0 provides:

- `TinyCompiledOntologyBuilder` under `tests/helpers/`, never production code;
- `FakeBackendSession` with configurable raw results;
- canonical `assert_taxonomy_valid` and `assert_realization_valid` helpers;
- codec golden bytes for empty and one-axiom ontologies.

Later work packages use these rather than adding temporary imports or stubbing unfinished
packages in production paths.
