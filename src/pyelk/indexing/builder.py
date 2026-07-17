"""Transactional structural interning and deterministic compiled-IR freezing.

The mutable objects in this module are private, short-lived compiler state.  Public OWL
values are never copied or retained: transactions contain only compact entity records,
temporary integer handles, occurrence counters, and normalized conversion rows.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from heapq import heappop, heappush
from typing import Final

from pyelk.indexing.ir import (
    FEATURE_VECTOR_LENGTH,
    U64_MAX,
    CompiledOntology,
    EntityId,
    EntityKind,
    EntityRecord,
    ExpressionId,
    ExpressionOccurrence,
    ExpressionRecord,
    ExpressionTag,
    PropertyChainId,
    PropertyOccurrence,
    QueryEntityRecord,
    QueryIR,
    QueryIRKind,
)
from pyelk.indexing.polarity import IndexPolarity

_OWL_THING: Final = EntityRecord(EntityKind.CLASS, "http://www.w3.org/2002/07/owl#Thing")
_OWL_NOTHING: Final = EntityRecord(EntityKind.CLASS, "http://www.w3.org/2002/07/owl#Nothing")
_OWL_TOP_OBJECT_PROPERTY: Final = EntityRecord(
    EntityKind.OBJECT_PROPERTY,
    "http://www.w3.org/2002/07/owl#topObjectProperty",
)
_OWL_BOTTOM_OBJECT_PROPERTY: Final = EntityRecord(
    EntityKind.OBJECT_PROPERTY,
    "http://www.w3.org/2002/07/owl#bottomObjectProperty",
)
PREDEFINED_ENTITIES: Final = (
    _OWL_THING,
    _OWL_NOTHING,
    _OWL_TOP_OBJECT_PROPERTY,
    _OWL_BOTTOM_OBJECT_PROPERTY,
)


@dataclass(frozen=True, slots=True)
class _ExpressionKey:
    tag: ExpressionTag
    entities: tuple[EntityRecord, ...] = ()
    expressions: tuple[int, ...] = ()
    payload: bytes = b""

    def dependencies(self) -> frozenset[int]:
        return frozenset(self.expressions)

    def rewritten_arguments(
        self,
        entity_ids: dict[EntityRecord, int],
        expression_ids: dict[int, int],
    ) -> tuple[int, ...]:
        if self.tag is ExpressionTag.OBJECT_SOME_VALUES_FROM:
            return (entity_ids[self.entities[0]], expression_ids[self.expressions[0]])
        if self.entities:
            return tuple(entity_ids[entity] for entity in self.entities)
        return tuple(expression_ids[expression] for expression in self.expressions)


def _checked_add(current: int, increment: int, field: str) -> int:
    result = current + increment
    if result < 0 or result > U64_MAX:
        raise OverflowError(f"{field} exceeds the frozen unsigned 64-bit range")
    return result


class MutableIndex:
    """Compact normalized state shared by ontology and query transactions."""

    __slots__ = (
        "_expression_ids",
        "compatibility_observations",
        "disjoint_groups",
        "entities",
        "equivalent_class_axioms",
        "expression_occurrences",
        "expressions",
        "feature_counts",
        "property_chains",
        "property_occurrences",
        "property_ranges",
        "subclass_axioms",
        "subproperty_axioms",
    )

    def __init__(self) -> None:
        self.entities: set[EntityRecord] = set()
        self.expressions: list[_ExpressionKey] = []
        self._expression_ids: dict[_ExpressionKey, int] = {}
        self.expression_occurrences: list[list[int]] = []
        self.property_occurrences: dict[EntityRecord, list[int]] = {}
        self.property_chains: set[tuple[EntityRecord, ...]] = set()
        self.subclass_axioms: set[tuple[int, int]] = set()
        self.equivalent_class_axioms: set[tuple[int, int]] = set()
        self.disjoint_groups: set[tuple[int, ...]] = set()
        self.subproperty_axioms: set[tuple[tuple[EntityRecord, ...], EntityRecord]] = set()
        self.property_ranges: set[tuple[EntityRecord, int]] = set()
        self.feature_counts = [0] * FEATURE_VECTOR_LENGTH
        self.compatibility_observations: set[bytes] = set()

    def add_entity(self, entity: EntityRecord) -> None:
        self.entities.add(entity)

    def intern_expression(
        self,
        tag: ExpressionTag,
        *,
        entities: tuple[EntityRecord, ...] = (),
        expressions: tuple[int, ...] = (),
        payload: bytes = b"",
        polarity: IndexPolarity = IndexPolarity.NEUTRAL,
    ) -> int:
        for entity in entities:
            self.add_entity(entity)
        key = _ExpressionKey(tag, entities, expressions, payload)
        handle = self._expression_ids.get(key)
        if handle is None:
            handle = len(self.expressions)
            self._expression_ids[key] = handle
            self.expressions.append(key)
            self.expression_occurrences.append([0, 0])
        occurrence = self.expression_occurrences[handle]
        occurrence[0] = _checked_add(
            occurrence[0], polarity.negative, "negative expression occurrence"
        )
        occurrence[1] = _checked_add(
            occurrence[1], polarity.positive, "positive expression occurrence"
        )
        return handle

    def expression_tag(self, handle: int) -> ExpressionTag:
        return self.expressions[handle].tag

    def record_object_property(
        self,
        entity: EntityRecord,
        polarity: IndexPolarity,
    ) -> None:
        if entity.kind is not EntityKind.OBJECT_PROPERTY:
            raise TypeError("object-property occurrences require an object-property entity")
        self.add_entity(entity)
        occurrence = self.property_occurrences.setdefault(entity, [0, 0])
        occurrence[0] = _checked_add(
            occurrence[0], polarity.negative, "negative property occurrence"
        )
        occurrence[1] = _checked_add(
            occurrence[1], polarity.positive, "positive property occurrence"
        )

    def add_feature(self, index: int, count: int = 1) -> None:
        if not 0 <= index < FEATURE_VECTOR_LENGTH:
            raise IndexError(index)
        self.feature_counts[index] = _checked_add(
            self.feature_counts[index], count, f"feature count {index}"
        )

    def observe_compatibility_spelling(self, observation: bytes) -> None:
        if not isinstance(observation, bytes) or not observation:
            raise TypeError("compatibility spelling observations must be nonempty bytes")
        self.compatibility_observations.add(observation)

    def add_property_chain(self, properties: tuple[EntityRecord, ...]) -> None:
        if not properties:
            raise ValueError("property chains must be nonempty")
        if any(entity.kind is not EntityKind.OBJECT_PROPERTY for entity in properties):
            raise TypeError("property chains contain only object properties")
        self.entities.update(properties)
        self.property_chains.add(properties)

    def add_subclass(self, sub_expression: int, super_expression: int) -> None:
        self.subclass_axioms.add((sub_expression, super_expression))

    def add_equivalent_class(self, defined: int, other: int) -> None:
        self.equivalent_class_axioms.add((defined, other))

    def add_disjoint_group(self, members: tuple[int, ...]) -> None:
        if len(members) < 2:
            raise ValueError("a disjoint group requires at least two positions")
        self.disjoint_groups.add(members)

    def add_subproperty(
        self,
        sub_chain: tuple[EntityRecord, ...],
        super_property: EntityRecord,
    ) -> None:
        self.add_property_chain(sub_chain)
        if super_property.kind is not EntityKind.OBJECT_PROPERTY:
            raise TypeError("a super-property must be an object property")
        self.add_entity(super_property)
        self.subproperty_axioms.add((sub_chain, super_property))

    def add_property_range(self, prop: EntityRecord, range_expression: int) -> None:
        if prop.kind is not EntityKind.OBJECT_PROPERTY:
            raise TypeError("a property range requires an object property")
        self.add_entity(prop)
        self.property_ranges.add((prop, range_expression))

    def compatibility_digest(self) -> bytes:
        digest = hashlib.sha256(b"pyelk:elk-literal-compatibility-inputs:v1\x00")
        for value in sorted(self.compatibility_observations):
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
        return digest.digest()


class IndexTransaction(MutableIndex):
    """One isolated axiom/query conversion that can commit or roll back atomically."""

    __slots__ = ("_closed",)

    def __init__(self) -> None:
        super().__init__()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def commit_into(self, target: OntologyBuilder) -> dict[int, int]:
        if self._closed:
            raise RuntimeError("index transaction is already closed")
        mapping = target.merge(self)
        self._closed = True
        return mapping

    def rollback(self) -> None:
        if self._closed:
            raise RuntimeError("index transaction is already closed")
        self._closed = True
        self.entities.clear()
        self.expressions.clear()
        self._expression_ids.clear()
        self.expression_occurrences.clear()
        self.property_occurrences.clear()
        self.property_chains.clear()
        self.subclass_axioms.clear()
        self.equivalent_class_axioms.clear()
        self.disjoint_groups.clear()
        self.subproperty_axioms.clear()
        self.property_ranges.clear()
        self.compatibility_observations.clear()
        self.feature_counts[:] = [0] * FEATURE_VECTOR_LENGTH


class OntologyBuilder(MutableIndex):
    """Long-lived compiler builder receiving successful isolated transactions."""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__()
        self.entities.update(PREDEFINED_ENTITIES)

    def merge(self, transaction: MutableIndex) -> dict[int, int]:
        self.entities.update(transaction.entities)
        expression_mapping: dict[int, int] = {}
        for local_handle, key in enumerate(transaction.expressions):
            translated = self.intern_expression(
                key.tag,
                entities=key.entities,
                expressions=tuple(expression_mapping[item] for item in key.expressions),
                payload=key.payload,
            )
            local_occurrence = transaction.expression_occurrences[local_handle]
            occurrence = self.expression_occurrences[translated]
            occurrence[0] = _checked_add(
                occurrence[0], local_occurrence[0], "negative expression occurrence"
            )
            occurrence[1] = _checked_add(
                occurrence[1], local_occurrence[1], "positive expression occurrence"
            )
            expression_mapping[local_handle] = translated
        for entity, local_occurrence in transaction.property_occurrences.items():
            occurrence = self.property_occurrences.setdefault(entity, [0, 0])
            occurrence[0] = _checked_add(
                occurrence[0], local_occurrence[0], "negative property occurrence"
            )
            occurrence[1] = _checked_add(
                occurrence[1], local_occurrence[1], "positive property occurrence"
            )
        self.property_chains.update(transaction.property_chains)
        self.subclass_axioms.update(
            (expression_mapping[first], expression_mapping[second])
            for first, second in transaction.subclass_axioms
        )
        self.equivalent_class_axioms.update(
            (expression_mapping[first], expression_mapping[second])
            for first, second in transaction.equivalent_class_axioms
        )
        self.disjoint_groups.update(
            tuple(expression_mapping[item] for item in group)
            for group in transaction.disjoint_groups
        )
        self.subproperty_axioms.update(transaction.subproperty_axioms)
        self.property_ranges.update(
            (prop, expression_mapping[range_expression])
            for prop, range_expression in transaction.property_ranges
        )
        for index, count in enumerate(transaction.feature_counts):
            self.feature_counts[index] = _checked_add(
                self.feature_counts[index], count, f"feature count {index}"
            )
        self.compatibility_observations.update(transaction.compatibility_observations)
        return expression_mapping

    def freeze(self, source_fingerprint: bytes) -> CompiledOntology:
        self._ensure_named_expressions()
        entities = _sorted_entities(self.entities)
        entity_ids = {record: index for index, record in enumerate(entities)}
        expressions, final_ids = self._freeze_expressions(entity_ids)
        object_properties = tuple(
            record for record in entities if record.kind is EntityKind.OBJECT_PROPERTY
        )
        for prop in object_properties:
            self.property_chains.add((prop,))
        property_chains = tuple(
            sorted(
                tuple(EntityId(entity_ids[prop]) for prop in chain)
                for chain in self.property_chains
            )
        )
        chain_ids = {
            tuple(int(item) for item in chain): index for index, chain in enumerate(property_chains)
        }
        return CompiledOntology(
            entities=entities,
            expressions=expressions,
            expression_occurrences=tuple(
                ExpressionOccurrence(
                    negative=self.expression_occurrences[temporary][0],
                    positive=self.expression_occurrences[temporary][1],
                )
                for temporary, _final in sorted(final_ids.items(), key=lambda item: item[1])
            ),
            property_occurrences=tuple(
                PropertyOccurrence(
                    negative=self.property_occurrences.get(prop, [0, 0])[0],
                    positive=self.property_occurrences.get(prop, [0, 0])[1],
                )
                for prop in object_properties
            ),
            property_chains=property_chains,
            subclass_axioms=tuple(
                sorted(
                    (ExpressionId(final_ids[first]), ExpressionId(final_ids[second]))
                    for first, second in self.subclass_axioms
                )
            ),
            equivalent_class_axioms=tuple(
                sorted(
                    (ExpressionId(final_ids[first]), ExpressionId(final_ids[second]))
                    for first, second in self.equivalent_class_axioms
                )
            ),
            disjoint_groups=tuple(
                sorted(
                    tuple(ExpressionId(final_ids[item]) for item in group)
                    for group in self.disjoint_groups
                )
            ),
            subproperty_axioms=tuple(
                sorted(
                    (
                        PropertyChainId(chain_ids[tuple(entity_ids[prop] for prop in sub_chain)]),
                        EntityId(entity_ids[super_property]),
                    )
                    for sub_chain, super_property in self.subproperty_axioms
                )
            ),
            property_ranges=tuple(
                sorted(
                    (EntityId(entity_ids[prop]), ExpressionId(final_ids[range_expression]))
                    for prop, range_expression in self.property_ranges
                )
            ),
            feature_counts=tuple(self.feature_counts),
            source_fingerprint=source_fingerprint,
        )

    def freeze_query_ir(
        self,
        *,
        kind: QueryIRKind,
        ontology_ids: dict[EntityRecord, EntityId],
        root_expression: int | None,
        obligations: set[tuple[int, int]],
    ) -> QueryIR:
        self._ensure_named_expressions()
        entities = _sorted_entities(self.entities)
        entity_ids = {record: index for index, record in enumerate(entities)}
        expressions, final_ids = self._freeze_expressions(entity_ids)
        object_properties = tuple(
            record for record in entities if record.kind is EntityKind.OBJECT_PROPERTY
        )
        return QueryIR(
            kind=kind,
            entities=tuple(
                QueryEntityRecord(record, ontology_ids.get(record)) for record in entities
            ),
            expressions=expressions,
            expression_occurrences=tuple(
                ExpressionOccurrence(
                    negative=self.expression_occurrences[temporary][0],
                    positive=self.expression_occurrences[temporary][1],
                )
                for temporary, _final in sorted(final_ids.items(), key=lambda item: item[1])
            ),
            property_occurrences=tuple(
                PropertyOccurrence(
                    negative=self.property_occurrences.get(prop, [0, 0])[0],
                    positive=self.property_occurrences.get(prop, [0, 0])[1],
                )
                for prop in object_properties
            ),
            root_expression=(
                None if root_expression is None else ExpressionId(final_ids[root_expression])
            ),
            subsumption_obligations=tuple(
                sorted(
                    (ExpressionId(final_ids[first]), ExpressionId(final_ids[second]))
                    for first, second in obligations
                )
            ),
        )

    def _ensure_named_expressions(self) -> None:
        for entity in tuple(self.entities):
            if entity.kind is EntityKind.CLASS:
                self.intern_expression(ExpressionTag.CLASS, entities=(entity,))
            elif entity.kind is EntityKind.NAMED_INDIVIDUAL:
                self.intern_expression(ExpressionTag.INDIVIDUAL, entities=(entity,))

    def _freeze_expressions(
        self,
        entity_ids: dict[EntityRecord, int],
    ) -> tuple[tuple[ExpressionRecord, ...], dict[int, int]]:
        dependents: list[list[int]] = [[] for _ in self.expressions]
        remaining: list[int] = []
        final_ids: dict[int, int] = {}
        available: list[tuple[tuple[int, bytes, tuple[int, ...]], int]] = []
        for handle, key in enumerate(self.expressions):
            dependencies = key.dependencies()
            remaining.append(len(dependencies))
            for dependency in dependencies:
                dependents[dependency].append(handle)
            if not dependencies:
                arguments = key.rewritten_arguments(entity_ids, final_ids)
                heappush(available, ((int(key.tag), key.payload, arguments), handle))

        records: list[ExpressionRecord] = []
        while available:
            (_order_key, handle) = heappop(available)
            if handle in final_ids:
                continue
            key = self.expressions[handle]
            arguments = key.rewritten_arguments(entity_ids, final_ids)
            final_ids[handle] = len(records)
            records.append(ExpressionRecord(key.tag, arguments, key.payload))
            for dependent in dependents[handle]:
                remaining[dependent] -= 1
                if remaining[dependent] == 0:
                    dependent_key = self.expressions[dependent]
                    rewritten = dependent_key.rewritten_arguments(entity_ids, final_ids)
                    heappush(
                        available,
                        (
                            (int(dependent_key.tag), dependent_key.payload, rewritten),
                            dependent,
                        ),
                    )
        if len(records) != len(self.expressions):
            raise ValueError("temporary expression graph is cyclic")
        return tuple(records), final_ids


class QueryBuilder(OntologyBuilder):
    """Transaction target for a self-contained query mini-IR.

    Unlike an ontology builder it does not inject all four predefined entities; a query
    records only entities actually present in its structural signature or normalization.
    """

    __slots__ = ()

    def __init__(self) -> None:
        MutableIndex.__init__(self)


def _sorted_entities(values: set[EntityRecord]) -> tuple[EntityRecord, ...]:
    return tuple(sorted(values, key=lambda item: (int(item.kind), item.iri.encode("utf-8"))))


__all__ = [
    "PREDEFINED_ENTITIES",
    "IndexTransaction",
    "MutableIndex",
    "OntologyBuilder",
    "QueryBuilder",
]
