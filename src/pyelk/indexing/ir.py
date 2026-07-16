"""Frozen backend-neutral records for ontology and query compilation.

This module owns structural validation but no OWL conversion semantics. Numeric identifiers
are session-local unsigned 32-bit values; ``0xffffffff`` is reserved by the wire protocols.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from heapq import heappop, heappush
from typing import Any, NewType, TypeAlias, TypeGuard, cast

EntityId = NewType("EntityId", int)
ExpressionId = NewType("ExpressionId", int)
PropertyChainId = NewType("PropertyChainId", int)
DisjointGroupId = NewType("DisjointGroupId", int)
ReadableBuffer: TypeAlias = bytes | bytearray | memoryview

U32_RESERVED = 0xFFFFFFFF
U32_MAX = U32_RESERVED - 1
U64_MAX = 0xFFFFFFFFFFFFFFFF
FEATURE_VECTOR_LENGTH = 79
FINGERPRINT_SIZE = 32

OWL_NOTHING_IRI = "http://www.w3.org/2002/07/owl#Nothing"
OWL_THING_IRI = "http://www.w3.org/2002/07/owl#Thing"
OWL_BOTTOM_OBJECT_PROPERTY_IRI = "http://www.w3.org/2002/07/owl#bottomObjectProperty"
OWL_TOP_OBJECT_PROPERTY_IRI = "http://www.w3.org/2002/07/owl#topObjectProperty"


class EntityKind(IntEnum):
    """Entity kinds in their frozen IR order."""

    CLASS = 0
    NAMED_INDIVIDUAL = 1
    OBJECT_PROPERTY = 2
    DATA_PROPERTY = 3
    DATATYPE = 4
    ANNOTATION_PROPERTY = 5


class ExpressionTag(IntEnum):
    """Expression constructors in their frozen IR order."""

    CLASS = 0
    INDIVIDUAL = 1
    OBJECT_INTERSECTION_OF = 2
    OBJECT_SOME_VALUES_FROM = 3
    OBJECT_HAS_SELF = 4
    DATA_HAS_VALUE = 5
    OBJECT_COMPLEMENT_OF = 6
    OBJECT_UNION_OF = 7


class QueryIRKind(IntEnum):
    """Shape of a query mini-IR payload."""

    CLASS_EXPRESSION = 0
    ENTAILMENT = 1


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_u32(value: object, field: str) -> int:
    if not _is_int(value) or not 0 <= value <= U32_MAX:
        raise ValueError(f"{field} must be an unsigned 32-bit ID excluding 0xffffffff")
    return value


def _check_u64(value: object, field: str) -> int:
    if not _is_int(value) or not 0 <= value <= U64_MAX:
        raise ValueError(f"{field} must be an unsigned 64-bit integer")
    return value


def _check_tuple(value: object, field: str) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{field} must be a tuple")
    return value


def _entity_key(record: EntityRecord) -> tuple[int, bytes]:
    return int(record.kind), record.iri.encode("utf-8")


def _check_sorted_unique(values: tuple[object, ...], field: str) -> None:
    ordered = cast(tuple[Any, ...], values)
    try:
        if any(ordered[index - 1] >= ordered[index] for index in range(1, len(ordered))):
            raise ValueError(f"{field} must be strictly sorted and unique")
    except TypeError as error:
        raise ValueError(f"{field} contains non-comparable values") from error


@dataclass(frozen=True, slots=True)
class EntityRecord:
    """A public entity kind and its full IRI."""

    kind: EntityKind
    iri: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EntityKind):
            raise ValueError("entity kind must be an EntityKind")
        if not isinstance(self.iri, str) or not self.iri:
            raise ValueError("entity IRI must be a nonempty string")
        try:
            self.iri.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("entity IRI must be valid UTF-8") from error


@dataclass(frozen=True, slots=True)
class ExpressionRecord:
    """A normalized expression with tag-specific integer arguments."""

    tag: ExpressionTag
    arguments: tuple[int, ...]
    payload: bytes = b""

    def __post_init__(self) -> None:
        if not isinstance(self.tag, ExpressionTag):
            raise ValueError("expression tag must be an ExpressionTag")
        _check_tuple(self.arguments, "expression arguments")
        for index, argument in enumerate(self.arguments):
            _check_u32(argument, f"expression argument {index}")
        if not isinstance(self.payload, bytes):
            raise ValueError("expression payload must be bytes")


@dataclass(frozen=True, slots=True)
class ExpressionOccurrence:
    """Positive and negative occurrence counts for one expression."""

    negative: int
    positive: int

    def __post_init__(self) -> None:
        _check_u64(self.negative, "negative expression occurrence")
        _check_u64(self.positive, "positive expression occurrence")


@dataclass(frozen=True, slots=True)
class PropertyOccurrence:
    """Positive and negative occurrence counts for one object property."""

    negative: int
    positive: int

    def __post_init__(self) -> None:
        _check_u64(self.negative, "negative property occurrence")
        _check_u64(self.positive, "positive property occurrence")


@dataclass(frozen=True, slots=True)
class QueryEntityRecord:
    """A query entity and its optional corresponding ontology entity ID."""

    entity: EntityRecord
    ontology_id: EntityId | None

    def __post_init__(self) -> None:
        if not isinstance(self.entity, EntityRecord):
            raise ValueError("query entity must contain an EntityRecord")
        if self.ontology_id is not None:
            _check_u32(self.ontology_id, "query ontology entity ID")


def _validate_entities(entities: tuple[EntityRecord, ...], *, predefined: bool) -> None:
    _check_tuple(entities, "entities")
    if any(not isinstance(record, EntityRecord) for record in entities):
        raise ValueError("entities must contain only EntityRecord values")
    keys = tuple(_entity_key(record) for record in entities)
    _check_sorted_unique(keys, "entities")
    if len(entities) > U32_RESERVED:
        raise ValueError("too many entities for u32 IDs")
    if predefined:
        required = {
            (EntityKind.CLASS, OWL_NOTHING_IRI),
            (EntityKind.CLASS, OWL_THING_IRI),
            (EntityKind.OBJECT_PROPERTY, OWL_BOTTOM_OBJECT_PROPERTY_IRI),
            (EntityKind.OBJECT_PROPERTY, OWL_TOP_OBJECT_PROPERTY_IRI),
        }
        actual = {(record.kind, record.iri) for record in entities}
        missing = required - actual
        if missing:
            raise ValueError(f"compiled ontology is missing predefined entities: {missing!r}")


def _validate_occurrences(
    expression_occurrences: tuple[ExpressionOccurrence, ...],
    property_occurrences: tuple[PropertyOccurrence, ...],
    *,
    expression_count: int,
    object_property_count: int,
) -> None:
    _check_tuple(expression_occurrences, "expression occurrences")
    _check_tuple(property_occurrences, "property occurrences")
    if len(expression_occurrences) != expression_count:
        raise ValueError("expression occurrence count must equal expression count")
    if len(property_occurrences) != object_property_count:
        raise ValueError("property occurrence count must equal object-property entity count")
    if any(not isinstance(value, ExpressionOccurrence) for value in expression_occurrences):
        raise ValueError("invalid expression occurrence record")
    if any(not isinstance(value, PropertyOccurrence) for value in property_occurrences):
        raise ValueError("invalid property occurrence record")


def _validate_expressions(
    expressions: tuple[ExpressionRecord, ...], entities: tuple[EntityRecord, ...]
) -> None:
    _check_tuple(expressions, "expressions")
    if len(expressions) > U32_RESERVED:
        raise ValueError("too many expressions for u32 IDs")
    for expression_id, record in enumerate(expressions):
        if not isinstance(record, ExpressionRecord):
            raise ValueError("expressions must contain only ExpressionRecord values")
        arguments = record.arguments
        if record.tag is ExpressionTag.CLASS:
            _validate_entity_arguments(arguments, entities, (EntityKind.CLASS,), expression_id)
            _require_empty_payload(record, expression_id)
        elif record.tag is ExpressionTag.INDIVIDUAL:
            _validate_entity_arguments(
                arguments, entities, (EntityKind.NAMED_INDIVIDUAL,), expression_id
            )
            _require_empty_payload(record, expression_id)
        elif record.tag is ExpressionTag.OBJECT_INTERSECTION_OF:
            if len(arguments) != 2:
                raise ValueError("intersection expressions must have exactly two arguments")
            _validate_expression_arguments(arguments, expression_id)
            _require_empty_payload(record, expression_id)
        elif record.tag is ExpressionTag.OBJECT_SOME_VALUES_FROM:
            if len(arguments) != 2:
                raise ValueError("existential expressions must have property and filler arguments")
            _validate_entity_index(arguments[0], entities, EntityKind.OBJECT_PROPERTY)
            _validate_expression_arguments((arguments[1],), expression_id)
            _require_empty_payload(record, expression_id)
        elif record.tag is ExpressionTag.OBJECT_HAS_SELF:
            _validate_entity_arguments(
                arguments, entities, (EntityKind.OBJECT_PROPERTY,), expression_id
            )
            _require_empty_payload(record, expression_id)
        elif record.tag is ExpressionTag.DATA_HAS_VALUE:
            _validate_entity_arguments(
                arguments, entities, (EntityKind.DATA_PROPERTY,), expression_id
            )
            if not record.payload:
                raise ValueError("data-has-value expressions require a literal structural key")
        elif record.tag is ExpressionTag.OBJECT_COMPLEMENT_OF:
            if len(arguments) != 1:
                raise ValueError("complement expressions must have exactly one argument")
            _validate_expression_arguments(arguments, expression_id)
            _require_empty_payload(record, expression_id)
        elif record.tag is ExpressionTag.OBJECT_UNION_OF:
            if len(arguments) < 2:
                raise ValueError("union expressions must have at least two arguments")
            _validate_expression_arguments(arguments, expression_id)
            _require_empty_payload(record, expression_id)
    _validate_expression_order(expressions)


def _expression_dependencies(record: ExpressionRecord) -> frozenset[int]:
    if record.tag is ExpressionTag.OBJECT_SOME_VALUES_FROM:
        return frozenset((record.arguments[1],))
    if record.tag in {
        ExpressionTag.OBJECT_INTERSECTION_OF,
        ExpressionTag.OBJECT_COMPLEMENT_OF,
        ExpressionTag.OBJECT_UNION_OF,
    }:
        return frozenset(record.arguments)
    return frozenset()


def _validate_expression_order(expressions: tuple[ExpressionRecord, ...]) -> None:
    """Verify the deterministic Kahn order frozen by the indexing contract."""

    keys = tuple((int(record.tag), record.payload, record.arguments) for record in expressions)
    if len(keys) != len(set(keys)):
        raise ValueError("expressions must be structurally unique")

    dependents: list[list[int]] = [[] for _ in expressions]
    remaining_dependencies: list[int] = []
    available: list[tuple[tuple[int, bytes, tuple[int, ...]], int]] = []
    for expression_id, record in enumerate(expressions):
        dependencies = _expression_dependencies(record)
        remaining_dependencies.append(len(dependencies))
        if not dependencies:
            heappush(available, (keys[expression_id], expression_id))
        for dependency in dependencies:
            dependents[dependency].append(expression_id)

    for expected_id in range(len(expressions)):
        if not available:
            raise ValueError("expression dependency graph must be acyclic")
        _, actual_id = heappop(available)
        if actual_id != expected_id:
            raise ValueError("expressions must use deterministic topological key order")
        for dependent in dependents[actual_id]:
            remaining_dependencies[dependent] -= 1
            if remaining_dependencies[dependent] == 0:
                heappush(available, (keys[dependent], dependent))


def _validate_entity_arguments(
    arguments: tuple[int, ...],
    entities: tuple[EntityRecord, ...],
    kinds: tuple[EntityKind, ...],
    expression_id: int,
) -> None:
    if len(arguments) != 1:
        raise ValueError(
            f"expression {expression_id} must have exactly one {kinds[0].name} entity argument"
        )
    _validate_entity_index(arguments[0], entities, kinds[0])


def _validate_entity_index(
    entity_id: int, entities: tuple[EntityRecord, ...], expected_kind: EntityKind
) -> None:
    if not 0 <= entity_id < len(entities):
        raise ValueError(f"entity ID {entity_id} is out of range")
    if entities[entity_id].kind is not expected_kind:
        raise ValueError(
            f"entity ID {entity_id} must have kind {expected_kind.name}, "
            f"got {entities[entity_id].kind.name}"
        )


def _validate_expression_arguments(arguments: tuple[int, ...], expression_id: int) -> None:
    if any(not 0 <= argument < expression_id for argument in arguments):
        raise ValueError("expression dependencies must precede their parent")


def _require_empty_payload(record: ExpressionRecord, expression_id: int) -> None:
    if record.payload:
        raise ValueError(f"expression {expression_id} tag {record.tag.name} forbids a payload")


def _validate_sorted_pairs(
    rows: tuple[tuple[int, int], ...],
    field: str,
    first_limit: int,
    second_limit: int,
) -> None:
    _check_tuple(rows, field)
    for row in rows:
        if not isinstance(row, tuple) or len(row) != 2:
            raise ValueError(f"{field} rows must be pairs")
        first = _check_u32(row[0], f"{field} first ID")
        second = _check_u32(row[1], f"{field} second ID")
        if first >= first_limit or second >= second_limit:
            raise ValueError(f"{field} contains an out-of-range ID")
    _check_sorted_unique(rows, field)


def _validate_feature_counts(feature_counts: tuple[int, ...]) -> None:
    _check_tuple(feature_counts, "feature counts")
    if len(feature_counts) != FEATURE_VECTOR_LENGTH:
        raise ValueError(
            f"feature count vector must contain {FEATURE_VECTOR_LENGTH} pinned ELK features"
        )
    for index, count in enumerate(feature_counts):
        _check_u64(count, f"feature count {index}")


@dataclass(frozen=True, slots=True)
class CompiledOntology:
    """Complete immutable input consumed identically by both backends."""

    entities: tuple[EntityRecord, ...]
    expressions: tuple[ExpressionRecord, ...]
    expression_occurrences: tuple[ExpressionOccurrence, ...]
    property_occurrences: tuple[PropertyOccurrence, ...]
    property_chains: tuple[tuple[EntityId, ...], ...]
    subclass_axioms: tuple[tuple[ExpressionId, ExpressionId], ...]
    equivalent_class_axioms: tuple[tuple[ExpressionId, ExpressionId], ...]
    disjoint_groups: tuple[tuple[ExpressionId, ...], ...]
    subproperty_axioms: tuple[tuple[PropertyChainId, EntityId], ...]
    property_ranges: tuple[tuple[EntityId, ExpressionId], ...]
    feature_counts: tuple[int, ...]
    source_fingerprint: bytes

    def __post_init__(self) -> None:
        _validate_entities(self.entities, predefined=True)
        _validate_expressions(self.expressions, self.entities)
        self._validate_named_entity_expressions()
        object_property_count = sum(
            record.kind is EntityKind.OBJECT_PROPERTY for record in self.entities
        )
        _validate_occurrences(
            self.expression_occurrences,
            self.property_occurrences,
            expression_count=len(self.expressions),
            object_property_count=object_property_count,
        )
        self._validate_property_chains()
        _validate_sorted_pairs(
            self.subclass_axioms,
            "subclass axioms",
            len(self.expressions),
            len(self.expressions),
        )
        _validate_sorted_pairs(
            self.equivalent_class_axioms,
            "equivalent-class axioms",
            len(self.expressions),
            len(self.expressions),
        )
        self._validate_disjoint_groups()
        _validate_sorted_pairs(
            self.subproperty_axioms,
            "subproperty axioms",
            len(self.property_chains),
            len(self.entities),
        )
        for _, super_property in self.subproperty_axioms:
            _validate_entity_index(super_property, self.entities, EntityKind.OBJECT_PROPERTY)
        _validate_sorted_pairs(
            self.property_ranges,
            "property ranges",
            len(self.entities),
            len(self.expressions),
        )
        for property_id, _ in self.property_ranges:
            _validate_entity_index(property_id, self.entities, EntityKind.OBJECT_PROPERTY)
        _validate_feature_counts(self.feature_counts)
        if (
            not isinstance(self.source_fingerprint, bytes)
            or len(self.source_fingerprint) != FINGERPRINT_SIZE
        ):
            raise ValueError(f"source fingerprint must be exactly {FINGERPRINT_SIZE} bytes")

    def _validate_named_entity_expressions(self) -> None:
        expected = {
            (ExpressionTag.CLASS, entity_id)
            for entity_id, entity in enumerate(self.entities)
            if entity.kind is EntityKind.CLASS
        }
        expected.update(
            (ExpressionTag.INDIVIDUAL, entity_id)
            for entity_id, entity in enumerate(self.entities)
            if entity.kind is EntityKind.NAMED_INDIVIDUAL
        )
        actual = {
            (record.tag, record.arguments[0])
            for record in self.expressions
            if record.tag in {ExpressionTag.CLASS, ExpressionTag.INDIVIDUAL}
        }
        if actual != expected:
            raise ValueError("every named class and individual must have one expression record")

    def _validate_property_chains(self) -> None:
        _check_tuple(self.property_chains, "property chains")
        for chain in self.property_chains:
            if not isinstance(chain, tuple) or not chain:
                raise ValueError("property chains must be nonempty tuples")
            for property_id in chain:
                _check_u32(property_id, "property-chain entity ID")
                _validate_entity_index(property_id, self.entities, EntityKind.OBJECT_PROPERTY)
        _check_sorted_unique(self.property_chains, "property chains")

    def _validate_disjoint_groups(self) -> None:
        _check_tuple(self.disjoint_groups, "disjoint groups")
        for group in self.disjoint_groups:
            if not isinstance(group, tuple) or len(group) < 2:
                raise ValueError("disjoint groups must retain at least two member positions")
            for expression_id in group:
                _check_u32(expression_id, "disjoint expression ID")
                if expression_id >= len(self.expressions):
                    raise ValueError("disjoint group contains an out-of-range expression ID")
        _check_sorted_unique(self.disjoint_groups, "disjoint groups")

    def encode(self) -> bytes:
        """Encode this ontology using the frozen v1 binary protocol."""

        from pyelk.indexing.codec import encode_ontology

        return encode_ontology(self)

    @classmethod
    def decode(cls, data: ReadableBuffer) -> CompiledOntology:
        """Decode and defensively validate a frozen v1 ontology payload."""

        from pyelk.indexing.codec import decode_ontology

        return decode_ontology(data)


@dataclass(frozen=True, slots=True)
class QueryIR:
    """Self-contained query-local expression graph and normalized obligations."""

    kind: QueryIRKind
    entities: tuple[QueryEntityRecord, ...]
    expressions: tuple[ExpressionRecord, ...]
    expression_occurrences: tuple[ExpressionOccurrence, ...]
    property_occurrences: tuple[PropertyOccurrence, ...]
    root_expression: ExpressionId | None
    subsumption_obligations: tuple[tuple[ExpressionId, ExpressionId], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, QueryIRKind):
            raise ValueError("query IR kind must be a QueryIRKind")
        _check_tuple(self.entities, "query entities")
        if any(not isinstance(record, QueryEntityRecord) for record in self.entities):
            raise ValueError("query entities must contain QueryEntityRecord values")
        entity_records = tuple(record.entity for record in self.entities)
        _validate_entities(entity_records, predefined=False)
        ontology_ids = tuple(
            int(record.ontology_id) for record in self.entities if record.ontology_id is not None
        )
        if len(set(ontology_ids)) != len(ontology_ids):
            raise ValueError("query ontology entity IDs must be unique")
        _validate_expressions(self.expressions, entity_records)
        self._validate_named_entity_expressions(entity_records)
        object_property_count = sum(
            record.kind is EntityKind.OBJECT_PROPERTY for record in entity_records
        )
        _validate_occurrences(
            self.expression_occurrences,
            self.property_occurrences,
            expression_count=len(self.expressions),
            object_property_count=object_property_count,
        )
        _validate_sorted_pairs(
            self.subsumption_obligations,
            "query subsumption obligations",
            len(self.expressions),
            len(self.expressions),
        )
        if self.kind is QueryIRKind.CLASS_EXPRESSION:
            if self.root_expression is None:
                raise ValueError("class-expression query IR requires a root expression")
            root = _check_u32(self.root_expression, "query root expression")
            if root >= len(self.expressions):
                raise ValueError("query root expression is out of range")
            if self.subsumption_obligations:
                raise ValueError("class-expression query IR cannot contain entailment obligations")
        else:
            if self.root_expression is not None:
                raise ValueError("entailment query IR cannot contain a class-query root")

    def _validate_named_entity_expressions(self, entity_records: tuple[EntityRecord, ...]) -> None:
        referenced = {
            (record.tag, record.arguments[0])
            for record in self.expressions
            if record.tag in {ExpressionTag.CLASS, ExpressionTag.INDIVIDUAL}
        }
        expected = {
            (ExpressionTag.CLASS, entity_id)
            for entity_id, entity in enumerate(entity_records)
            if entity.kind is EntityKind.CLASS
        }
        expected.update(
            (ExpressionTag.INDIVIDUAL, entity_id)
            for entity_id, entity in enumerate(entity_records)
            if entity.kind is EntityKind.NAMED_INDIVIDUAL
        )
        if referenced != expected:
            raise ValueError("every query class and individual must have one expression record")

    def encode(self) -> bytes:
        """Encode this query using the frozen v1 mini-IR protocol."""

        from pyelk.indexing.codec import encode_query_ir

        return encode_query_ir(self)

    @classmethod
    def decode(cls, data: ReadableBuffer) -> QueryIR:
        """Decode and validate a frozen v1 query mini-IR payload."""

        from pyelk.indexing.codec import decode_query_ir

        return decode_query_ir(data)


@dataclass(frozen=True, slots=True)
class CompiledQuery:
    """A query payload plus feature and fresh-entity metadata for the facade."""

    encoded: bytes | None
    feature_counts: tuple[int, ...]
    fresh_entities: tuple[EntityRecord, ...]

    def __post_init__(self) -> None:
        if self.encoded is not None and not isinstance(self.encoded, bytes):
            raise ValueError("compiled query payload must be bytes or None")
        _validate_feature_counts(self.feature_counts)
        _validate_entities(self.fresh_entities, predefined=False)
        if self.encoded is not None:
            query = QueryIR.decode(self.encoded)
            encoded_fresh = tuple(
                record.entity for record in query.entities if record.ontology_id is None
            )
            if encoded_fresh != self.fresh_entities:
                raise ValueError("compiled query fresh entities must match its encoded mini-IR")


__all__ = [
    "FEATURE_VECTOR_LENGTH",
    "FINGERPRINT_SIZE",
    "CompiledOntology",
    "CompiledQuery",
    "DisjointGroupId",
    "EntityId",
    "EntityKind",
    "EntityRecord",
    "ExpressionId",
    "ExpressionOccurrence",
    "ExpressionRecord",
    "ExpressionTag",
    "PropertyChainId",
    "PropertyOccurrence",
    "QueryEntityRecord",
    "QueryIR",
    "QueryIRKind",
    "ReadableBuffer",
]
