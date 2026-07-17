"""Backend-neutral named and complex class-expression query algorithms.

Query mini-IR is installed into a private immutable overlay.  The overlay is never used for
public enumeration: selections are projected back through the caller-supplied raw taxonomy
and realization views.  This preserves one canonical parser/compiler contract while keeping
query-only expressions out of ontology state.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from heapq import heappop, heappush
from typing import Final

from pyelk.indexing.builder import OntologyBuilder
from pyelk.indexing.ir import (
    U32_RESERVED,
    CompiledOntology,
    EntityId,
    EntityKind,
    EntityRecord,
    ExpressionId,
    ExpressionOccurrence,
    ExpressionRecord,
    ExpressionTag,
    PropertyOccurrence,
    QueryIR,
    QueryIRKind,
)
from pyelk.indexing.polarity import IndexPolarity
from pyelk.reasoning.contexts import FrozenContext
from pyelk.reasoning.contracts import (
    QueryKind,
    QueryResultEntityId,
    RawQueryResult,
    RawRealization,
    RawTaxonomy,
)
from pyelk.reasoning.properties import PropertySaturation, saturate_properties
from pyelk.reasoning.realization import (
    direct_type_indices,
    fresh_taxonomy_bounds,
    taxonomy_equivalent_node,
    taxonomy_sub_nodes,
    taxonomy_super_nodes,
)
from pyelk.reasoning.saturation import SaturationEngine
from pyelk.reasoning.session import SaturationSession

_U64_MAX: Final = 0xFFFFFFFFFFFFFFFF


@dataclass(frozen=True, slots=True)
class QueryFeatureMetadata:
    """Sparse feature-count hook consumed by WP10 completeness attachment."""

    counts: tuple[tuple[int, int], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.counts, tuple):
            raise ValueError("query feature metadata counts must be a tuple")
        if any(
            not isinstance(row, tuple)
            or len(row) != 2
            or isinstance(row[0], bool)
            or not isinstance(row[0], int)
            or row[0] < 0
            or isinstance(row[1], bool)
            or not isinstance(row[1], int)
            or row[1] <= 0
            for row in self.counts
        ):
            raise ValueError("query feature metadata contains an invalid count")
        if any(
            self.counts[index - 1][0] >= self.counts[index][0]
            for index in range(1, len(self.counts))
        ):
            raise ValueError("query feature metadata must be sorted and unique")


def query_feature_metadata(feature_counts: tuple[int, ...]) -> QueryFeatureMetadata:
    """Retain exact nonzero query feature positions without importing completeness code."""

    if not isinstance(feature_counts, tuple):
        raise TypeError("feature_counts must be a tuple")
    if any(
        isinstance(count, bool) or not isinstance(count, int) or count < 0
        for count in feature_counts
    ):
        raise ValueError("feature_counts must contain nonnegative integers")
    return QueryFeatureMetadata(
        tuple((index, count) for index, count in enumerate(feature_counts) if count)
    )


class ClassQueryEngine:
    """Cache deterministic private overlays and class-query answers for one session."""

    __slots__ = (
        "_evaluation_cache",
        "_result_cache",
        "class_taxonomy",
        "realized",
        "session",
    )

    def __init__(
        self,
        session: SaturationSession,
        class_taxonomy: RawTaxonomy,
        realized: RawRealization,
    ) -> None:
        if not isinstance(session, SaturationSession):
            raise TypeError("session must be SaturationSession")
        if not isinstance(class_taxonomy, RawTaxonomy):
            raise TypeError("class_taxonomy must be RawTaxonomy")
        if not isinstance(realized, RawRealization):
            raise TypeError("realized must be RawRealization")
        if realized.class_taxonomy != class_taxonomy:
            raise ValueError("realization and class taxonomy views must agree")
        self.session = session
        self.class_taxonomy = class_taxonomy
        self.realized = realized
        self._evaluation_cache: dict[bytes, _QueryEvaluation] = {}
        self._result_cache: dict[tuple[bytes | None, QueryKind, bool], RawQueryResult] = {}

    @property
    def cached_query_count(self) -> int:
        """Number of successfully installed canonical mini-IR payloads."""

        return len(self._evaluation_cache)

    def query(
        self,
        encoded_expression: bytes | None,
        kind: QueryKind,
        direct: bool = False,
    ) -> RawQueryResult:
        """Evaluate one class query with pinned inconsistent/unindexed fallbacks."""

        if encoded_expression is not None and not isinstance(encoded_expression, bytes):
            raise TypeError("encoded_expression must be bytes or None")
        if not isinstance(kind, QueryKind):
            raise TypeError("kind must be QueryKind")
        if not isinstance(direct, bool):
            raise TypeError("direct must be a boolean")
        key = (encoded_expression, kind, direct)
        cached = self._result_cache.get(key)
        if cached is not None:
            return cached

        query: QueryIR | None = None
        if encoded_expression is not None:
            query = QueryIR.decode(encoded_expression)
            if query.kind is not QueryIRKind.CLASS_EXPRESSION:
                raise ValueError("class query requires CLASS_EXPRESSION mini-IR")
        inconsistent = self.session.ensure_consistency().inconsistent
        if inconsistent:
            result = _inconsistent_result(kind, self.class_taxonomy, self.realized)
        elif query is None:
            result = _unindexed_result(kind, direct, self.class_taxonomy)
        else:
            if encoded_expression is None:  # pragma: no cover - narrowed by query decode
                raise AssertionError("decoded query has no canonical payload")
            evaluation = self._evaluation_cache.get(encoded_expression)
            if evaluation is None:
                evaluation = _evaluate_query(self.session.compiled, query)
                self._evaluation_cache[encoded_expression] = evaluation
            result = _select_query(
                evaluation,
                self.session.compiled,
                self.class_taxonomy,
                self.realized,
                kind,
                direct,
            )
        self._result_cache[key] = result
        return result


def query_class_expression(
    session: SaturationSession,
    class_taxonomy: RawTaxonomy,
    realized: RawRealization,
    encoded_expression: bytes | None,
    kind: QueryKind,
    direct: bool = False,
    *,
    engine: ClassQueryEngine | None = None,
) -> RawQueryResult:
    """Functional class-query entry point; pass ``engine`` to retain query caches."""

    evaluator = engine or ClassQueryEngine(session, class_taxonomy, realized)
    if evaluator.session is not session:
        raise ValueError("query engine belongs to another saturation session")
    return evaluator.query(encoded_expression, kind, direct)


def named_taxonomy_query(
    taxonomy: RawTaxonomy,
    entity: int,
    kind: QueryKind,
    *,
    direct: bool = False,
    fresh_id: int | None = None,
) -> RawQueryResult:
    """Select an existing or allowed-fresh named class/property raw node view."""

    if kind not in {
        QueryKind.EQUIVALENT_CLASSES,
        QueryKind.SUBCLASSES,
        QueryKind.SUPERCLASSES,
    }:
        raise ValueError("named taxonomy queries support equivalence, subs, and supers")
    nodes: tuple[tuple[EntityId, ...], ...]
    try:
        if kind is QueryKind.EQUIVALENT_CLASSES:
            nodes = (taxonomy_equivalent_node(taxonomy, entity),)
        elif kind is QueryKind.SUBCLASSES:
            nodes = taxonomy_sub_nodes(taxonomy, entity, direct=direct)
        else:
            nodes = taxonomy_super_nodes(taxonomy, entity, direct=direct)
    except KeyError:
        if fresh_id is None or entity != fresh_id:
            raise
        if kind is QueryKind.EQUIVALENT_CLASSES:
            nodes = ((EntityId(fresh_id),),)
        else:
            nodes = fresh_taxonomy_bounds(
                taxonomy,
                supers=kind is QueryKind.SUPERCLASSES,
            )
    return RawQueryResult(kind=kind, nodes=_query_nodes(nodes))


def named_class_query(
    taxonomy: RawTaxonomy,
    entity: int,
    kind: QueryKind,
    *,
    direct: bool = False,
    fresh_id: int | None = None,
) -> RawQueryResult:
    """Named-class spelling of :func:`named_taxonomy_query`."""

    return named_taxonomy_query(
        taxonomy,
        entity,
        kind,
        direct=direct,
        fresh_id=fresh_id,
    )


def named_object_property_query(
    taxonomy: RawTaxonomy,
    entity: int,
    kind: QueryKind,
    *,
    direct: bool = False,
    fresh_id: int | None = None,
) -> RawQueryResult:
    """Named-object-property spelling of :func:`named_taxonomy_query`."""

    return named_taxonomy_query(
        taxonomy,
        entity,
        kind,
        direct=direct,
        fresh_id=fresh_id,
    )


@dataclass(slots=True)
class _QueryEvaluation:
    query: QueryIR
    overlay: CompiledOntology
    root: ExpressionId
    ontology_expression_ids: tuple[ExpressionId, ...]
    query_expression_ids: tuple[ExpressionId, ...]
    contexts: dict[ExpressionId, FrozenContext]
    fresh_result_ids: tuple[tuple[int, QueryResultEntityId], ...]
    properties: PropertySaturation
    engine: SaturationEngine


@dataclass(frozen=True, slots=True)
class _NodeCandidate:
    members: tuple[QueryResultEntityId, ...]
    expression: ExpressionId
    taxonomy_index: int | None


def _evaluate_query(compiled: CompiledOntology, query: QueryIR) -> _QueryEvaluation:
    overlay, ontology_ids, query_ids = _install_query(compiled, query)
    if query.root_expression is None:  # pragma: no cover - QueryIR validates kind shape
        raise AssertionError("class query lost its root")
    root = query_ids[query.root_expression]
    properties = saturate_properties(overlay)
    fresh_rows: list[tuple[int, QueryResultEntityId]] = []
    fresh_rank = 0
    for query_entity_id, record in enumerate(query.entities):
        if record.ontology_id is None:
            result_id = len(compiled.entities) + fresh_rank
            if result_id >= U32_RESERVED:
                raise OverflowError("query fresh-entity result namespace is exhausted")
            fresh_rows.append((query_entity_id, QueryResultEntityId(result_id)))
            fresh_rank += 1
    return _QueryEvaluation(
        query=query,
        overlay=overlay,
        root=root,
        ontology_expression_ids=ontology_ids,
        query_expression_ids=query_ids,
        contexts={},
        fresh_result_ids=tuple(fresh_rows),
        properties=properties,
        engine=SaturationEngine(overlay, properties),
    )


def _ensure_contexts(
    evaluation: _QueryEvaluation,
    roots: Iterable[ExpressionId],
) -> None:
    requested = tuple(
        sorted(
            {
                root
                for root_value in roots
                if (root := ExpressionId(root_value)) not in evaluation.contexts
            }
        )
    )
    if not requested:
        return
    if evaluation.overlay.property_ranges:
        for root in requested:
            snapshot = SaturationEngine(evaluation.overlay, evaluation.properties).run((root,))
            evaluation.contexts[root] = snapshot.contexts[root]
        return
    snapshot = evaluation.engine.run(requested)
    evaluation.contexts.update(snapshot.contexts)


def _select_query(
    evaluation: _QueryEvaluation,
    compiled: CompiledOntology,
    taxonomy: RawTaxonomy,
    realized: RawRealization,
    kind: QueryKind,
    direct: bool,
) -> RawQueryResult:
    _ensure_contexts(evaluation, (evaluation.root,))
    root_context = evaluation.contexts[evaluation.root]
    if kind is QueryKind.SATISFIABLE:
        return RawQueryResult(kind=kind, boolean=not root_context.inconsistent)

    class_expressions = {
        EntityId(record.arguments[0]): evaluation.ontology_expression_ids[index]
        for index, record in enumerate(compiled.expressions)
        if record.tag is ExpressionTag.CLASS
    }
    expression_to_node = {
        expression: node_index
        for node_index, node in enumerate(taxonomy.nodes)
        for entity in node
        if (expression := class_expressions.get(entity)) is not None
    }
    root_subsumers = _context_subsumers(root_context)
    strict_supers = _strict_super_closure(taxonomy)
    fresh_classes = _fresh_class_candidates(evaluation)

    possible_equivalents = {
        class_expressions[node[0]]
        for node in taxonomy.nodes
        if class_expressions[node[0]] in root_subsumers
    }
    possible_equivalents.update(
        candidate.expression
        for candidate in fresh_classes
        if candidate.expression in root_subsumers
    )
    _ensure_contexts(evaluation, possible_equivalents)

    equivalent_index: int | None = taxonomy.bottom if root_context.inconsistent else None
    if equivalent_index is None:
        for node_index, node in enumerate(taxonomy.nodes):
            representative = class_expressions[node[0]]
            if (
                representative in root_subsumers
                and evaluation.root
                in _context_subsumers(evaluation.contexts[representative])
            ):
                equivalent_index = node_index
                break

    fresh_equivalent = next(
        (
            candidate
            for candidate in fresh_classes
            if candidate.expression in root_subsumers
            and evaluation.root
            in _context_subsumers(evaluation.contexts[candidate.expression])
        ),
        None,
    )
    if kind is QueryKind.EQUIVALENT_CLASSES:
        nodes: tuple[tuple[EntityId, ...], ...]
        if equivalent_index is not None:
            nodes = (taxonomy.nodes[equivalent_index],)
        elif fresh_equivalent is not None:
            nodes = ((EntityId(int(fresh_equivalent.members[0])),),)
        else:
            nodes = ()
        return RawQueryResult(kind=kind, nodes=_query_nodes(nodes))

    if kind in {QueryKind.SUBCLASSES, QueryKind.SUPERCLASSES}:
        supers = kind is QueryKind.SUPERCLASSES
        if not supers:
            _ensure_contexts(
                evaluation,
                (
                    *class_expressions.values(),
                    *(candidate.expression for candidate in fresh_classes),
                ),
            )
        if equivalent_index is not None:
            ontology_candidates = set(
                _relative_indices(
                    taxonomy,
                    equivalent_index,
                    supers=supers,
                    direct=False,
                )
            )
        elif supers:
            ontology_candidates = {taxonomy.top}
            ontology_candidates.update(
                expression_to_node[expression]
                for expression in root_subsumers
                if expression in expression_to_node
            )
        else:
            ontology_candidates = {taxonomy.bottom}
            ontology_candidates.update(
                node_index
                for node_index, node in enumerate(taxonomy.nodes)
                if evaluation.root
                in _context_subsumers(evaluation.contexts[class_expressions[node[0]]])
            )
        node_candidates = [
            _NodeCandidate(
                members=tuple(
                    QueryResultEntityId(int(member)) for member in taxonomy.nodes[index]
                ),
                expression=class_expressions[taxonomy.nodes[index][0]],
                taxonomy_index=index,
            )
            for index in sorted(ontology_candidates)
        ]
        node_candidates.extend(
            candidate
            for candidate in fresh_classes
            if candidate.expression != evaluation.root
            and (
                candidate.expression in root_subsumers
                if supers
                else evaluation.root
                in _context_subsumers(evaluation.contexts[candidate.expression])
            )
        )
        if direct:
            if fresh_classes:
                _ensure_contexts(
                    evaluation,
                    (candidate.expression for candidate in node_candidates),
                )
            node_candidates = _direct_candidates(
                node_candidates,
                supers=supers,
                contexts=evaluation.contexts,
                taxonomy=taxonomy,
                strict_supers=strict_supers,
            )
        return RawQueryResult(
            kind=kind,
            nodes=tuple(sorted(candidate.members for candidate in node_candidates)),
        )

    if kind is not QueryKind.INSTANCES:  # pragma: no cover - exhaustive QueryKind
        raise AssertionError(kind)
    if equivalent_index is not None:
        matching_class_nodes = {equivalent_index}
        matching_class_nodes.update(
            node
            for node in range(len(taxonomy.nodes))
            if equivalent_index in strict_supers[node]
        )
        selected = {
            instance_index
            for instance_index in range(len(realized.instance_nodes))
            if any(
                direct_type == equivalent_index
                if direct
                else direct_type in matching_class_nodes
                for direct_type in direct_type_indices(realized, instance_index)
            )
        }
    else:
        individual_expressions = {
            EntityId(record.arguments[0]): evaluation.ontology_expression_ids[index]
            for index, record in enumerate(compiled.expressions)
            if record.tag is ExpressionTag.INDIVIDUAL
        }
        _ensure_contexts(evaluation, individual_expressions.values())
        selected = set()
        for instance_index, instance_node in enumerate(realized.instance_nodes):
            representative = individual_expressions[instance_node[0]]
            if evaluation.root not in _context_subsumers(evaluation.contexts[representative]):
                continue
            selected.add(instance_index)
        if direct and selected:
            selected_direct_types = {
                direct_type
                for instance_index in selected
                for direct_type in direct_type_indices(realized, instance_index)
            }
            _ensure_contexts(
                evaluation,
                (
                    class_expressions[taxonomy.nodes[class_node][0]]
                    for class_node in selected_direct_types
                ),
            )
            strict_subclasses = {taxonomy.bottom}
            strict_subclasses.update(
                class_node
                for class_node in selected_direct_types
                if evaluation.root
                in _context_subsumers(
                    evaluation.contexts[
                        class_expressions[taxonomy.nodes[class_node][0]]
                    ]
                )
            )
            selected = {
                instance_index
                for instance_index in selected
                if not any(
                    direct_type in strict_subclasses
                    for direct_type in direct_type_indices(realized, instance_index)
                )
            }
    nodes = tuple(realized.instance_nodes[index] for index in sorted(selected))
    return RawQueryResult(kind=kind, nodes=_query_nodes(nodes))


def _install_query(
    compiled: CompiledOntology,
    query: QueryIR,
) -> tuple[CompiledOntology, tuple[ExpressionId, ...], tuple[ExpressionId, ...]]:
    ontology_lookup = {record: EntityId(index) for index, record in enumerate(compiled.entities)}
    for record in query.entities:
        actual = ontology_lookup.get(record.entity)
        if record.ontology_id is None:
            if actual is not None:
                raise ValueError("query marks an existing ontology entity as fresh")
        elif actual != record.ontology_id:
            raise ValueError("query ontology entity reference does not match the session table")
    builder = OntologyBuilder()
    ontology_handles = _load_ontology(builder, compiled)
    _load_query(builder, query)
    overlay = builder.freeze(compiled.source_fingerprint)
    ontology_ids = _remap_expressions(compiled.entities, compiled.expressions, overlay)
    query_ids = _remap_expressions(
        tuple(record.entity for record in query.entities),
        query.expressions,
        overlay,
    )
    if len(ontology_handles) != len(compiled.expressions):  # pragma: no cover - loader invariant
        raise AssertionError("ontology overlay loader lost an expression")
    return overlay, ontology_ids, query_ids


def _load_ontology(builder: OntologyBuilder, compiled: CompiledOntology) -> tuple[int, ...]:
    builder.entities.update(compiled.entities)
    handles = _load_expressions(
        builder,
        compiled.entities,
        compiled.expressions,
        compiled.expression_occurrences,
    )
    object_properties = tuple(
        entity for entity in compiled.entities if entity.kind is EntityKind.OBJECT_PROPERTY
    )
    _add_property_occurrences(builder, object_properties, compiled.property_occurrences)
    for chain in compiled.property_chains:
        builder.add_property_chain(tuple(compiled.entities[entity] for entity in chain))
    for first, second in compiled.subclass_axioms:
        builder.add_subclass(handles[first], handles[second])
    for first, second in compiled.equivalent_class_axioms:
        builder.add_equivalent_class(handles[first], handles[second])
    for group in compiled.disjoint_groups:
        builder.add_disjoint_group(tuple(handles[item] for item in group))
    for chain_id, super_property in compiled.subproperty_axioms:
        builder.add_subproperty(
            tuple(compiled.entities[item] for item in compiled.property_chains[chain_id]),
            compiled.entities[super_property],
        )
    for prop, range_expression in compiled.property_ranges:
        builder.add_property_range(compiled.entities[prop], handles[range_expression])
    builder.feature_counts[:] = list(compiled.feature_counts)
    return handles


def _load_query(builder: OntologyBuilder, query: QueryIR) -> tuple[int, ...]:
    entities = tuple(record.entity for record in query.entities)
    builder.entities.update(entities)
    handles = _load_expressions(
        builder,
        entities,
        query.expressions,
        query.expression_occurrences,
    )
    object_properties = tuple(
        entity for entity in entities if entity.kind is EntityKind.OBJECT_PROPERTY
    )
    _add_property_occurrences(builder, object_properties, query.property_occurrences)
    return handles


def _load_expressions(
    builder: OntologyBuilder,
    entities: tuple[EntityRecord, ...],
    expressions: tuple[ExpressionRecord, ...],
    occurrences: tuple[ExpressionOccurrence, ...],
) -> tuple[int, ...]:
    handles: list[int] = []
    for expression_index, record in enumerate(expressions):
        expression_dependencies: tuple[int, ...] = ()
        expression_entities: tuple[EntityRecord, ...] = ()
        if record.tag is ExpressionTag.OBJECT_SOME_VALUES_FROM:
            expression_entities = (entities[record.arguments[0]],)
            expression_dependencies = (handles[record.arguments[1]],)
        elif record.tag in {
            ExpressionTag.OBJECT_INTERSECTION_OF,
            ExpressionTag.OBJECT_COMPLEMENT_OF,
            ExpressionTag.OBJECT_UNION_OF,
        }:
            expression_dependencies = tuple(handles[item] for item in record.arguments)
        else:
            expression_entities = tuple(entities[item] for item in record.arguments)
        handle = builder.intern_expression(
            record.tag,
            entities=expression_entities,
            expressions=expression_dependencies,
            payload=record.payload,
            polarity=IndexPolarity.NEUTRAL,
        )
        occurrence = occurrences[expression_index]
        _add_occurrence(
            builder.expression_occurrences[handle],
            occurrence.negative,
            occurrence.positive,
            "expression occurrence",
        )
        handles.append(handle)
    return tuple(handles)


def _add_property_occurrences(
    builder: OntologyBuilder,
    properties: tuple[EntityRecord, ...],
    occurrences: tuple[PropertyOccurrence, ...],
) -> None:
    for prop, occurrence in zip(properties, occurrences, strict=True):
        row = builder.property_occurrences.setdefault(prop, [0, 0])
        _add_occurrence(row, occurrence.negative, occurrence.positive, "property occurrence")


def _add_occurrence(row: list[int], negative: int, positive: int, field: str) -> None:
    next_negative = row[0] + negative
    next_positive = row[1] + positive
    if next_negative > _U64_MAX or next_positive > _U64_MAX:
        raise OverflowError(f"{field} exceeds the frozen unsigned 64-bit range")
    row[:] = [next_negative, next_positive]


def _remap_expressions(
    source_entities: tuple[EntityRecord, ...],
    source_expressions: tuple[ExpressionRecord, ...],
    overlay: CompiledOntology,
) -> tuple[ExpressionId, ...]:
    entity_ids = {record: index for index, record in enumerate(overlay.entities)}
    expression_ids = {
        record: ExpressionId(index) for index, record in enumerate(overlay.expressions)
    }
    mapped: list[ExpressionId] = []
    for record in source_expressions:
        arguments: tuple[int, ...]
        if record.tag is ExpressionTag.OBJECT_SOME_VALUES_FROM:
            arguments = (
                entity_ids[source_entities[record.arguments[0]]],
                int(mapped[record.arguments[1]]),
            )
        elif record.tag in {
            ExpressionTag.OBJECT_INTERSECTION_OF,
            ExpressionTag.OBJECT_COMPLEMENT_OF,
            ExpressionTag.OBJECT_UNION_OF,
        }:
            arguments = tuple(int(mapped[item]) for item in record.arguments)
        else:
            arguments = tuple(entity_ids[source_entities[item]] for item in record.arguments)
        mapped_record = ExpressionRecord(
            record.tag,
            tuple(int(item) for item in arguments),
            record.payload,
        )
        mapped.append(expression_ids[mapped_record])
    return tuple(mapped)


def _fresh_class_candidates(evaluation: _QueryEvaluation) -> tuple[_NodeCandidate, ...]:
    result_ids = dict(evaluation.fresh_result_ids)
    candidates: list[_NodeCandidate] = []
    for index, record in enumerate(evaluation.query.expressions):
        if record.tag is not ExpressionTag.CLASS:
            continue
        result_id = result_ids.get(record.arguments[0])
        if result_id is not None:
            candidates.append(
                _NodeCandidate(
                    members=(result_id,),
                    expression=evaluation.query_expression_ids[index],
                    taxonomy_index=None,
                )
            )
    return tuple(candidates)


def _context_subsumers(context: FrozenContext) -> frozenset[ExpressionId]:
    return frozenset(
        {
            context.root,
            *context.composed_subsumers,
            *context.decomposed_subsumers,
        }
    )


def _relative_indices(
    taxonomy: RawTaxonomy,
    start: int,
    *,
    supers: bool,
    direct: bool,
) -> tuple[int, ...]:
    adjacency: list[list[int]] = [[] for _ in taxonomy.nodes]
    for sub, sup in taxonomy.direct_edges:
        source, target = (sub, sup) if supers else (sup, sub)
        adjacency[source].append(target)
    reached = set(adjacency[start])
    if not direct:
        pending = list(reached)
        while pending:
            node = pending.pop()
            for target in adjacency[node]:
                if target not in reached:
                    reached.add(target)
                    pending.append(target)
    return tuple(sorted(reached))


def _strict_super_closure(taxonomy: RawTaxonomy) -> tuple[frozenset[int], ...]:
    outgoing: list[list[int]] = [[] for _ in taxonomy.nodes]
    incoming: list[list[int]] = [[] for _ in taxonomy.nodes]
    remaining = [0] * len(taxonomy.nodes)
    for sub_node, super_node in taxonomy.direct_edges:
        outgoing[sub_node].append(super_node)
        incoming[super_node].append(sub_node)
        remaining[sub_node] += 1
    ready: list[int] = []
    for node, count in enumerate(remaining):
        if count == 0:
            heappush(ready, node)
    closures: list[frozenset[int]] = [frozenset() for _ in taxonomy.nodes]
    visited = 0
    while ready:
        node = heappop(ready)
        visited += 1
        reached: set[int] = set()
        for super_node in outgoing[node]:
            reached.add(super_node)
            reached.update(closures[super_node])
        closures[node] = frozenset(reached)
        for sub_node in incoming[node]:
            remaining[sub_node] -= 1
            if remaining[sub_node] == 0:
                heappush(ready, sub_node)
    if visited != len(taxonomy.nodes):  # pragma: no cover - RawTaxonomy validates acyclicity
        raise ValueError("taxonomy relation is cyclic")
    return tuple(closures)


def _direct_candidates(
    candidates: list[_NodeCandidate],
    *,
    supers: bool,
    contexts: dict[ExpressionId, FrozenContext],
    taxonomy: RawTaxonomy,
    strict_supers: tuple[frozenset[int], ...],
) -> list[_NodeCandidate]:
    return [
        candidate
        for candidate in candidates
        if not any(
            _candidate_subsumes(
                other if supers else candidate,
                candidate if supers else other,
                contexts=contexts,
                taxonomy=taxonomy,
                strict_supers=strict_supers,
            )
            for other in candidates
            if other != candidate
        )
    ]


def _candidate_subsumes(
    sub: _NodeCandidate,
    sup: _NodeCandidate,
    *,
    contexts: dict[ExpressionId, FrozenContext],
    taxonomy: RawTaxonomy,
    strict_supers: tuple[frozenset[int], ...],
) -> bool:
    if sub.expression == sup.expression:
        return False
    if sub.taxonomy_index is not None and sup.taxonomy_index is not None:
        return sup.taxonomy_index in strict_supers[sub.taxonomy_index]
    if sub.taxonomy_index == taxonomy.bottom or sup.taxonomy_index == taxonomy.top:
        return True
    context = contexts[sub.expression]
    return context.inconsistent or sup.expression in _context_subsumers(context)


def _query_nodes(
    nodes: tuple[tuple[EntityId, ...], ...],
) -> tuple[tuple[QueryResultEntityId, ...], ...]:
    return tuple(
        sorted(
            tuple(QueryResultEntityId(int(member)) for member in node)
            for node in nodes
        )
    )


def _unindexed_result(
    kind: QueryKind,
    direct: bool,
    taxonomy: RawTaxonomy,
) -> RawQueryResult:
    if kind is QueryKind.SATISFIABLE:
        return RawQueryResult(kind=kind, boolean=True)
    if kind is QueryKind.SUBCLASSES and direct:
        return RawQueryResult(kind=kind, nodes=_query_nodes((taxonomy.nodes[taxonomy.bottom],)))
    if kind is QueryKind.SUPERCLASSES and direct:
        return RawQueryResult(kind=kind, nodes=_query_nodes((taxonomy.nodes[taxonomy.top],)))
    return RawQueryResult(kind=kind)


def _inconsistent_result(
    kind: QueryKind,
    taxonomy: RawTaxonomy,
    realized: RawRealization,
) -> RawQueryResult:
    if kind is QueryKind.SATISFIABLE:
        return RawQueryResult(kind=kind, boolean=False)
    if kind is QueryKind.EQUIVALENT_CLASSES:
        return RawQueryResult(kind=kind, nodes=_query_nodes((taxonomy.nodes[taxonomy.top],)))
    if kind is QueryKind.INSTANCES:
        return RawQueryResult(kind=kind, nodes=_query_nodes(realized.instance_nodes))
    return RawQueryResult(kind=kind)


__all__ = [
    "ClassQueryEngine",
    "QueryFeatureMetadata",
    "named_class_query",
    "named_object_property_query",
    "named_taxonomy_query",
    "query_class_expression",
    "query_feature_metadata",
]
