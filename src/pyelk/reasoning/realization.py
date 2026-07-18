"""Pure instance quotient and realization selectors.

This module deliberately consumes the backend-neutral ``RawTaxonomy`` contract rather
than the taxonomy implementation.  It can therefore be reused by the Python backend,
the native adapter tests, and tiny semantic oracles without creating a WP8 dependency.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from pyelk.indexing.ir import CompiledOntology, EntityId, EntityKind, ExpressionId, ExpressionTag
from pyelk.reasoning.contexts import FrozenContext
from pyelk.reasoning.contracts import RawRealization, RawTaxonomy
from pyelk.reasoning.properties import saturate_properties
from pyelk.reasoning.saturation import SaturationEngine, SaturationSnapshot
from pyelk.reasoning.session import SaturationSession


def realization(
    session: SaturationSession,
    class_taxonomy: RawTaxonomy,
) -> RawRealization:
    """Realize every committed individual in ``session`` over ``class_taxonomy``."""

    if not isinstance(session, SaturationSession):
        raise TypeError("session must be SaturationSession")
    return build_realization(
        session.compiled,
        session.ensure_realized(),
        class_taxonomy,
    )


def build_realization(
    compiled: CompiledOntology,
    saturation: SaturationSnapshot,
    class_taxonomy: RawTaxonomy,
) -> RawRealization:
    """Build same-individual nodes and their minimal named class types."""

    if not isinstance(compiled, CompiledOntology):
        raise TypeError("compiled must be CompiledOntology")
    if not isinstance(saturation, SaturationSnapshot):
        raise TypeError("saturation must be SaturationSnapshot")
    if not isinstance(class_taxonomy, RawTaxonomy):
        raise TypeError("class_taxonomy must be RawTaxonomy")

    individuals = _entity_ids(compiled, EntityKind.NAMED_INDIVIDUAL)
    if saturation.inconsistent_ontology:
        nodes = (individuals,) if individuals else ()
        direct_types = ((0, class_taxonomy.top),) if individuals else ()
        return RawRealization(class_taxonomy, nodes, direct_types)

    individual_expressions = _named_expressions(compiled, ExpressionTag.INDIVIDUAL)
    class_expressions = _named_expressions(compiled, ExpressionTag.CLASS)
    contexts: Mapping[ExpressionId, FrozenContext] = saturation.contexts
    if compiled.property_ranges:
        # Frozen IR v1 has no private range-filler identity.  A shared engine therefore
        # lets an assertion-root activation write inherited ranges into another named
        # individual's public context.  ELK realization demands every nominal root
        # independently; mirror that observable behaviour until a later IR revision can
        # represent IndexedRangeFiller directly.
        properties = saturate_properties(compiled)
        contexts = {
            root: SaturationEngine(compiled, properties).run((root,)).contexts[root]
            for root in individual_expressions.values()
        }
    expression_to_class_node = {
        expression: node_index
        for node_index, node in enumerate(class_taxonomy.nodes)
        for entity in node
        if (expression := class_expressions.get(entity)) is not None
    }
    missing = [
        individual_expressions[individual]
        for individual in individuals
        if individual_expressions[individual] not in contexts
    ]
    if missing:
        raise ValueError(f"realization snapshot is missing individual contexts: {missing!r}")

    parent = {individual: individual for individual in individuals}

    def find(value: EntityId) -> EntityId:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(first: EntityId, second: EntityId) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root == second_root:
            return
        if int(first_root) > int(second_root):
            first_root, second_root = second_root, first_root
        parent[second_root] = first_root

    for position, first in enumerate(individuals):
        first_expression = individual_expressions[first]
        first_subsumers = _subsumers(contexts, first_expression)
        for second in individuals[position + 1 :]:
            second_expression = individual_expressions[second]
            if second_expression in first_subsumers and first_expression in _subsumers(
                contexts, second_expression
            ):
                union(first, second)

    grouped: dict[EntityId, list[EntityId]] = {}
    for individual in individuals:
        grouped.setdefault(find(individual), []).append(individual)
    instance_nodes = tuple(sorted(tuple(sorted(group)) for group in grouped.values()))

    strict_supers = _strict_super_closure(class_taxonomy)
    direct_rows: list[tuple[int, int]] = []
    for instance_index, node in enumerate(instance_nodes):
        candidate_types = {class_taxonomy.top}
        for individual in node:
            context_subsumers = _subsumers(
                contexts,
                individual_expressions[individual],
            )
            candidate_types.update(
                expression_to_class_node[expression]
                for expression in context_subsumers
                if expression in expression_to_class_node
            )
        candidate_types.discard(class_taxonomy.bottom)
        direct = _minimal_nodes(candidate_types, strict_supers)
        if not direct:  # pragma: no cover - top is always seeded by saturation
            direct = (class_taxonomy.top,)
        direct_rows.extend((instance_index, class_node) for class_node in direct)

    return RawRealization(
        class_taxonomy=class_taxonomy,
        instance_nodes=instance_nodes,
        direct_types=tuple(sorted(direct_rows)),
    )


def equivalent_individuals(
    value: RawRealization,
    individual: int,
    *,
    fresh_id: int | None = None,
) -> tuple[EntityId, ...]:
    """Return one same-individual node, or a singleton allowed-fresh node."""

    _require_realization(value)
    for node in value.instance_nodes:
        if individual in node:
            return node
    if fresh_id is not None and individual == fresh_id:
        return (EntityId(fresh_id),)
    raise KeyError(individual)


def types(
    value: RawRealization,
    individual: int,
    *,
    direct: bool = False,
    fresh_id: int | None = None,
) -> tuple[tuple[EntityId, ...], ...]:
    """Select direct or transitive named types for one individual."""

    _require_realization(value)
    if not isinstance(direct, bool):
        raise TypeError("direct must be a boolean")
    instance_index = _instance_index(value, individual)
    if instance_index is None:
        if fresh_id is not None and individual == fresh_id:
            return (value.class_taxonomy.nodes[value.class_taxonomy.top],)
        raise KeyError(individual)
    node_indices = {
        class_index
        for candidate_instance, class_index in value.direct_types
        if candidate_instance == instance_index
    }
    if not direct:
        strict_supers = _strict_super_closure(value.class_taxonomy)
        node_indices.update(
            super_node
            for class_index in tuple(node_indices)
            for super_node in strict_supers[class_index]
        )
    return _taxonomy_nodes(value.class_taxonomy, node_indices)


def instances(
    value: RawRealization,
    class_entity: int,
    *,
    direct: bool = False,
) -> tuple[tuple[EntityId, ...], ...]:
    """Return direct instances or instances inherited from strict subclasses."""

    _require_realization(value)
    if not isinstance(direct, bool):
        raise TypeError("direct must be a boolean")
    class_index = _taxonomy_node_index(value.class_taxonomy, class_entity)
    if class_index is None:
        raise KeyError(class_entity)
    strict_supers = _strict_super_closure(value.class_taxonomy)
    selected = {
        instance_index
        for instance_index, direct_type in value.direct_types
        if direct_type == class_index or (not direct and class_index in strict_supers[direct_type])
    }
    return tuple(value.instance_nodes[index] for index in sorted(selected))


def direct_type_indices(value: RawRealization, instance_index: int) -> tuple[int, ...]:
    """Expose the minimal class-node indices for internal complex-query selection."""

    _require_realization(value)
    if (
        isinstance(instance_index, bool)
        or not isinstance(instance_index, int)
        or not 0 <= instance_index < len(value.instance_nodes)
    ):
        raise IndexError(instance_index)
    return tuple(
        class_index
        for candidate_instance, class_index in value.direct_types
        if candidate_instance == instance_index
    )


def taxonomy_sub_nodes(
    taxonomy: RawTaxonomy,
    entity: int,
    *,
    direct: bool = False,
) -> tuple[tuple[EntityId, ...], ...]:
    """Backend-neutral named subclass/subproperty selector."""

    return _taxonomy_relatives(taxonomy, entity, direct=direct, supers=False)


def taxonomy_super_nodes(
    taxonomy: RawTaxonomy,
    entity: int,
    *,
    direct: bool = False,
) -> tuple[tuple[EntityId, ...], ...]:
    """Backend-neutral named superclass/superproperty selector."""

    return _taxonomy_relatives(taxonomy, entity, direct=direct, supers=True)


def taxonomy_equivalent_node(taxonomy: RawTaxonomy, entity: int) -> tuple[EntityId, ...]:
    """Return the taxonomy equivalence node for ``entity``."""

    if not isinstance(taxonomy, RawTaxonomy):
        raise TypeError("taxonomy must be RawTaxonomy")
    index = _taxonomy_node_index(taxonomy, entity)
    if index is None:
        raise KeyError(entity)
    return taxonomy.nodes[index]


def fresh_equivalent_node(fresh_id: int) -> tuple[EntityId, ...]:
    """Return the singleton semantic node for one allowed fresh named entity."""

    if isinstance(fresh_id, bool) or not isinstance(fresh_id, int) or fresh_id < 0:
        raise ValueError("fresh_id must be a nonnegative integer")
    return (EntityId(fresh_id),)


def fresh_taxonomy_bounds(
    taxonomy: RawTaxonomy,
    *,
    supers: bool,
) -> tuple[tuple[EntityId, ...], ...]:
    """Return the sole strict top/bottom bound for a fresh class or property."""

    if not isinstance(taxonomy, RawTaxonomy):
        raise TypeError("taxonomy must be RawTaxonomy")
    if not isinstance(supers, bool):
        raise TypeError("supers must be a boolean")
    index = taxonomy.top if supers else taxonomy.bottom
    return (taxonomy.nodes[index],)


def _taxonomy_relatives(
    taxonomy: RawTaxonomy,
    entity: int,
    *,
    direct: bool,
    supers: bool,
) -> tuple[tuple[EntityId, ...], ...]:
    if not isinstance(taxonomy, RawTaxonomy):
        raise TypeError("taxonomy must be RawTaxonomy")
    if not isinstance(direct, bool):
        raise TypeError("direct must be a boolean")
    start = _taxonomy_node_index(taxonomy, entity)
    if start is None:
        raise KeyError(entity)
    edges = (
        ((sub, sup) for sub, sup in taxonomy.direct_edges)
        if supers
        else ((sup, sub) for sub, sup in taxonomy.direct_edges)
    )
    adjacency: list[list[int]] = [[] for _ in taxonomy.nodes]
    for source, target in edges:
        adjacency[source].append(target)
    selected = set(adjacency[start])
    if not direct:
        pending = list(selected)
        while pending:
            node = pending.pop()
            for target in adjacency[node]:
                if target not in selected:
                    selected.add(target)
                    pending.append(target)
    return _taxonomy_nodes(taxonomy, selected)


def _subsumers(
    contexts: Mapping[ExpressionId, FrozenContext],
    root: ExpressionId,
) -> set[ExpressionId]:
    context = contexts.get(root)
    if context is None:
        return {root}
    return {root, *context.composed_subsumers, *context.decomposed_subsumers}


def _minimal_nodes(
    candidates: Iterable[int],
    strict_supers: tuple[frozenset[int], ...],
) -> tuple[int, ...]:
    values = set(candidates)
    return tuple(
        sorted(
            node
            for node in values
            if not any(node in strict_supers[other] for other in values if other != node)
        )
    )


def _strict_super_closure(taxonomy: RawTaxonomy) -> tuple[frozenset[int], ...]:
    outgoing: list[list[int]] = [[] for _ in taxonomy.nodes]
    for sub_node, super_node in taxonomy.direct_edges:
        outgoing[sub_node].append(super_node)
    result: list[frozenset[int]] = []
    for start in range(len(taxonomy.nodes)):
        reached: set[int] = set()
        pending = list(outgoing[start])
        while pending:
            node = pending.pop()
            if node in reached:
                continue
            reached.add(node)
            pending.extend(outgoing[node])
        result.append(frozenset(reached))
    return tuple(result)


def _taxonomy_nodes(
    taxonomy: RawTaxonomy,
    indices: Iterable[int],
) -> tuple[tuple[EntityId, ...], ...]:
    return tuple(sorted(taxonomy.nodes[index] for index in set(indices)))


def _taxonomy_node_index(taxonomy: RawTaxonomy, entity: int) -> int | None:
    return next(
        (index for index, node in enumerate(taxonomy.nodes) if entity in node),
        None,
    )


def _instance_index(value: RawRealization, individual: int) -> int | None:
    return next(
        (index for index, node in enumerate(value.instance_nodes) if individual in node),
        None,
    )


def _entity_ids(compiled: CompiledOntology, kind: EntityKind) -> tuple[EntityId, ...]:
    return tuple(
        EntityId(index) for index, record in enumerate(compiled.entities) if record.kind is kind
    )


def _named_expressions(
    compiled: CompiledOntology,
    tag: ExpressionTag,
) -> dict[EntityId, ExpressionId]:
    return {
        EntityId(record.arguments[0]): ExpressionId(index)
        for index, record in enumerate(compiled.expressions)
        if record.tag is tag
    }


def _require_realization(value: RawRealization) -> None:
    if not isinstance(value, RawRealization):
        raise TypeError("value must be RawRealization")


__all__ = [
    "build_realization",
    "direct_type_indices",
    "equivalent_individuals",
    "fresh_equivalent_node",
    "fresh_taxonomy_bounds",
    "instances",
    "realization",
    "taxonomy_equivalent_node",
    "taxonomy_sub_nodes",
    "taxonomy_super_nodes",
    "types",
]
