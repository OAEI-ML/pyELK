"""Reusable contract builders, validators, and backend doubles."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from hashlib import blake2b

from pyelk.exceptions import ReasonerClosedError
from pyelk.indexing.codec import SCHEMA_MAJOR, SCHEMA_MINOR
from pyelk.indexing.ir import (
    FEATURE_VECTOR_LENGTH,
    OWL_BOTTOM_OBJECT_PROPERTY_IRI,
    OWL_NOTHING_IRI,
    OWL_THING_IRI,
    OWL_TOP_OBJECT_PROPERTY_IRI,
    CompiledOntology,
    EntityId,
    EntityKind,
    EntityRecord,
    ExpressionId,
    ExpressionOccurrence,
    ExpressionRecord,
    ExpressionTag,
    PropertyOccurrence,
)
from pyelk.reasoning.contracts import (
    BackendInfo,
    DiagnosticScalar,
    QueryKind,
    RawQueryResult,
    RawRealization,
    RawTaxonomy,
)


class TinyCompiledOntologyBuilder:
    """Build small valid compiled ontologies without importing semantic packages."""

    def __init__(self) -> None:
        self._entities: set[tuple[EntityKind, str]] = {
            (EntityKind.CLASS, OWL_NOTHING_IRI),
            (EntityKind.CLASS, OWL_THING_IRI),
            (EntityKind.OBJECT_PROPERTY, OWL_BOTTOM_OBJECT_PROPERTY_IRI),
            (EntityKind.OBJECT_PROPERTY, OWL_TOP_OBJECT_PROPERTY_IRI),
        }
        self._subclasses: set[tuple[str, str]] = set()
        self._feature_counts = [0] * FEATURE_VECTOR_LENGTH

    def add_entity(self, kind: EntityKind, iri: str) -> TinyCompiledOntologyBuilder:
        """Add a declared entity and return this builder."""

        self._entities.add((kind, iri))
        return self

    def add_class(self, iri: str) -> TinyCompiledOntologyBuilder:
        """Add a named class."""

        return self.add_entity(EntityKind.CLASS, iri)

    def add_object_property(self, iri: str) -> TinyCompiledOntologyBuilder:
        """Add a named object property."""

        return self.add_entity(EntityKind.OBJECT_PROPERTY, iri)

    def add_subclass(self, sub_iri: str, super_iri: str) -> TinyCompiledOntologyBuilder:
        """Add one named-class subclass row."""

        self.add_class(sub_iri)
        self.add_class(super_iri)
        self._subclasses.add((sub_iri, super_iri))
        return self

    def set_feature_count(self, index: int, count: int) -> TinyCompiledOntologyBuilder:
        """Set one pinned feature count."""

        if not 0 <= index < FEATURE_VECTOR_LENGTH:
            raise IndexError(index)
        if count < 0:
            raise ValueError("feature count must be nonnegative")
        self._feature_counts[index] = count
        return self

    def build(self) -> CompiledOntology:
        """Freeze the deterministic test ontology."""

        entities = tuple(
            EntityRecord(kind=kind, iri=iri)
            for kind, iri in sorted(
                self._entities, key=lambda item: (int(item[0]), item[1].encode())
            )
        )
        expression_entities = tuple(
            (ExpressionTag.CLASS, index)
            for index, record in enumerate(entities)
            if record.kind is EntityKind.CLASS
        ) + tuple(
            (ExpressionTag.INDIVIDUAL, index)
            for index, record in enumerate(entities)
            if record.kind is EntityKind.NAMED_INDIVIDUAL
        )
        expressions = tuple(
            ExpressionRecord(tag, (entity_id,)) for tag, entity_id in expression_entities
        )
        expression_ids = {
            entities[entity_id].iri: expression_id
            for expression_id, (tag, entity_id) in enumerate(expression_entities)
            if tag is ExpressionTag.CLASS
        }
        object_property_ids = tuple(
            index
            for index, record in enumerate(entities)
            if record.kind is EntityKind.OBJECT_PROPERTY
        )
        property_chains = tuple((EntityId(entity_id),) for entity_id in object_property_ids)
        subclass_axioms = tuple(
            sorted(
                (
                    ExpressionId(expression_ids[sub_iri]),
                    ExpressionId(expression_ids[super_iri]),
                )
                for sub_iri, super_iri in self._subclasses
            )
        )
        fingerprint_input = b"\n".join(
            [
                *(f"{int(kind)}:{iri}".encode() for kind, iri in sorted(self._entities)),
                *(f"sub:{sub}:{sup}".encode() for sub, sup in sorted(self._subclasses)),
            ]
        )
        return CompiledOntology(
            entities=entities,
            expressions=expressions,
            expression_occurrences=tuple(
                ExpressionOccurrence(negative=0, positive=0) for _ in expressions
            ),
            property_occurrences=tuple(
                PropertyOccurrence(negative=0, positive=0) for _ in object_property_ids
            ),
            property_chains=property_chains,
            subclass_axioms=subclass_axioms,
            equivalent_class_axioms=(),
            disjoint_groups=(),
            subproperty_axioms=(),
            property_ranges=(),
            feature_counts=tuple(self._feature_counts),
            source_fingerprint=blake2b(fingerprint_input, digest_size=32).digest(),
        )


def assert_taxonomy_valid(taxonomy: RawTaxonomy) -> None:
    """Assert semantic graph invariants beyond raw-record structural validation."""

    node_count = len(taxonomy.nodes)
    outgoing: list[set[int]] = [set() for _ in range(node_count)]
    incoming: list[set[int]] = [set() for _ in range(node_count)]
    for sub_node, super_node in taxonomy.direct_edges:
        outgoing[sub_node].add(super_node)
        incoming[super_node].add(sub_node)

    assert not outgoing[taxonomy.top], "top node cannot have a strict superclass"
    assert not incoming[taxonomy.bottom], "bottom node cannot have a strict subclass"

    indegrees = [len(incoming[index]) for index in range(node_count)]
    ready = deque(index for index, indegree in enumerate(indegrees) if indegree == 0)
    visited = 0
    while ready:
        node = ready.popleft()
        visited += 1
        for successor in outgoing[node]:
            indegrees[successor] -= 1
            if indegrees[successor] == 0:
                ready.append(successor)
    assert visited == node_count, "taxonomy direct-edge graph must be acyclic"

    assert _reachable(taxonomy.bottom, outgoing) == set(range(node_count))
    reverse = incoming
    assert _reachable(taxonomy.top, reverse) == set(range(node_count))

    for edge in taxonomy.direct_edges:
        sub_node, super_node = edge
        without_edge = [set(values) for values in outgoing]
        without_edge[sub_node].remove(super_node)
        assert super_node not in _reachable(sub_node, without_edge), (
            f"direct edge {edge!r} is transitively redundant"
        )


def _reachable(start: int, edges: list[set[int]]) -> set[int]:
    result = {start}
    todo = [start]
    while todo:
        node = todo.pop()
        for successor in edges[node] - result:
            result.add(successor)
            todo.append(successor)
    return result


def assert_realization_valid(realization: RawRealization) -> None:
    """Assert taxonomy and direct-type invariants for a raw realization."""

    assert_taxonomy_valid(realization.class_taxonomy)
    typed_instances = {instance_index for instance_index, _ in realization.direct_types}
    assert typed_instances == set(range(len(realization.instance_nodes))), (
        "every instance node must have at least one direct type"
    )


class FakeBackendSession:
    """Configurable complete ``BackendSession`` test double."""

    def __init__(
        self,
        *,
        class_taxonomy: RawTaxonomy,
        object_property_taxonomy: RawTaxonomy | None = None,
        realization: RawRealization | None = None,
        inconsistent: bool = False,
        query_results: Mapping[tuple[bytes | None, QueryKind, bool], RawQueryResult] | None = None,
        entailments: Mapping[bytes | None, bool] | None = None,
        diagnostics: Mapping[str, DiagnosticScalar] | None = None,
    ) -> None:
        self._info = BackendInfo(
            name="python",
            implementation_version="test-double",
            ir_major=SCHEMA_MAJOR,
            ir_minor=SCHEMA_MINOR,
            requested_workers=0,
            effective_workers=1,
            native_available=False,
            fallback_reason="test double",
        )
        self._class_taxonomy = class_taxonomy
        self._object_property_taxonomy = object_property_taxonomy or class_taxonomy
        self._realization = realization or RawRealization(class_taxonomy, (), ())
        self._inconsistent = inconsistent
        self._query_results = dict(query_results or {})
        self._entailments = dict(entailments or {})
        self._diagnostics = dict(diagnostics or {})
        self._closed = False

    @property
    def info(self) -> BackendInfo:
        self._ensure_open()
        return self._info

    def close(self) -> None:
        self._closed = True

    def is_inconsistent(self) -> bool:
        self._ensure_open()
        return self._inconsistent

    def class_taxonomy(self) -> RawTaxonomy:
        self._ensure_open()
        return self._class_taxonomy

    def object_property_taxonomy(self) -> RawTaxonomy:
        self._ensure_open()
        return self._object_property_taxonomy

    def realization(self) -> RawRealization:
        self._ensure_open()
        return self._realization

    def query_class_expression(
        self, encoded_expression: bytes | None, kind: QueryKind, direct: bool
    ) -> RawQueryResult:
        self._ensure_open()
        key = (encoded_expression, kind, direct)
        if key in self._query_results:
            return self._query_results[key]
        if kind is QueryKind.SATISFIABLE:
            return RawQueryResult(kind=kind, boolean=True)
        return RawQueryResult(kind=kind)

    def entails(self, encoded_axiom: bytes | None) -> bool:
        self._ensure_open()
        return self._entailments.get(encoded_axiom, False)

    def diagnostics(self) -> Mapping[str, DiagnosticScalar]:
        self._ensure_open()
        return dict(self._diagnostics)

    def _ensure_open(self) -> None:
        if self._closed:
            raise ReasonerClosedError


__all__ = [
    "FakeBackendSession",
    "TinyCompiledOntologyBuilder",
    "assert_realization_valid",
    "assert_taxonomy_valid",
]
