"""Canonical class and object-property taxonomies from pure saturation."""

from __future__ import annotations

from pyelk.indexing.ir import (
    OWL_BOTTOM_OBJECT_PROPERTY_IRI,
    OWL_NOTHING_IRI,
    OWL_THING_IRI,
    OWL_TOP_OBJECT_PROPERTY_IRI,
    CompiledOntology,
    EntityId,
    EntityKind,
    EntityRecord,
    ExpressionId,
    ExpressionTag,
    PropertyChainId,
)
from pyelk.reasoning.contexts import FrozenContext
from pyelk.reasoning.contracts import RawTaxonomy
from pyelk.reasoning.properties import PropertySaturation, saturate_properties
from pyelk.reasoning.reduction import quotient_and_reduce, transitive_reduction
from pyelk.reasoning.saturation import SaturationEngine, SaturationSnapshot
from pyelk.reasoning.session import SaturationSession


def class_taxonomy(session: SaturationSession) -> RawTaxonomy:
    """Classify one pure saturation session into a canonical raw taxonomy."""

    if not isinstance(session, SaturationSession):
        raise TypeError("session must be SaturationSession")
    return build_class_taxonomy(
        session.compiled,
        session.ensure_classified(),
        properties=session.properties,
    )


def object_property_taxonomy(session: SaturationSession) -> RawTaxonomy:
    """Classify singleton object properties from one pure saturation session."""

    if not isinstance(session, SaturationSession):
        raise TypeError("session must be SaturationSession")
    consistency = session.ensure_consistency()
    return build_object_property_taxonomy(
        session.compiled,
        session.properties,
        inconsistent_ontology=consistency.inconsistent,
    )


def build_class_taxonomy(
    compiled: CompiledOntology,
    saturation: SaturationSnapshot,
    *,
    properties: PropertySaturation | None = None,
) -> RawTaxonomy:
    """Build the mutual-subsumption quotient of every committed named class."""

    if not isinstance(compiled, CompiledOntology):
        raise TypeError("compiled must be CompiledOntology")
    if not isinstance(saturation, SaturationSnapshot):
        raise TypeError("saturation must be SaturationSnapshot")
    if properties is not None and not isinstance(properties, PropertySaturation):
        raise TypeError("properties must be PropertySaturation or None")
    members = _entity_ids(compiled, EntityKind.CLASS)
    top = _entity(compiled, EntityKind.CLASS, OWL_THING_IRI)
    bottom = _entity(compiled, EntityKind.CLASS, OWL_NOTHING_IRI)
    if saturation.inconsistent_ontology:
        return _collapsed_taxonomy(compiled, members, EntityKind.CLASS, top, bottom)

    expressions = _named_expressions(compiled, ExpressionTag.CLASS)
    expression_entities = {expression: entity for entity, expression in expressions.items()}
    contexts: dict[ExpressionId, FrozenContext] | None = None
    if compiled.property_ranges:
        # Frozen IR v1 has no separate IndexedRangeFiller identity.  WP6 must therefore use
        # named filler IDs for transitively demanded range contexts.  Sharing those contexts
        # between independently demanded taxonomy roots would leak one root's range facts
        # into another.  Isolated engines retain the correct root consequence while keeping
        # the ordinary no-range classification path fully shared and linear.
        property_view = saturate_properties(compiled) if properties is None else properties
        contexts = {
            root: SaturationEngine(compiled, property_view).run((root,)).contexts[root]
            for root in expressions.values()
        }
    edges: set[tuple[int, int]] = {(int(bottom), int(member)) for member in members}
    for member in members:
        edges.add((int(member), int(top)))
        root = expressions[member]
        context = saturation.contexts.get(root) if contexts is None else contexts.get(root)
        if context is None:
            raise ValueError(f"classification snapshot is missing class context {root}")
        for subsumer in {*context.composed_subsumers, *context.decomposed_subsumers}:
            super_entity = expression_entities.get(ExpressionId(subsumer))
            if super_entity is not None:
                edges.add((int(member), int(super_entity)))
        if context.inconsistent or member == bottom:
            edges.add((int(member), int(bottom)))
    return _from_relation(compiled, members, edges, EntityKind.CLASS, top, bottom)


def build_object_property_taxonomy(
    compiled: CompiledOntology,
    properties: PropertySaturation,
    *,
    inconsistent_ontology: bool = False,
) -> RawTaxonomy:
    """Build the singleton-chain property quotient and direct hierarchy."""

    if not isinstance(compiled, CompiledOntology):
        raise TypeError("compiled must be CompiledOntology")
    if not isinstance(properties, PropertySaturation):
        raise TypeError("properties must be PropertySaturation")
    if not isinstance(inconsistent_ontology, bool):
        raise TypeError("inconsistent_ontology must be a boolean")
    members = _entity_ids(compiled, EntityKind.OBJECT_PROPERTY)
    top = _entity(
        compiled,
        EntityKind.OBJECT_PROPERTY,
        OWL_TOP_OBJECT_PROPERTY_IRI,
    )
    bottom = _entity(
        compiled,
        EntityKind.OBJECT_PROPERTY,
        OWL_BOTTOM_OBJECT_PROPERTY_IRI,
    )
    if inconsistent_ontology:
        return _collapsed_taxonomy(
            compiled,
            members,
            EntityKind.OBJECT_PROPERTY,
            top,
            bottom,
        )

    singleton_entities = {
        PropertyChainId(chain): record.first_property
        for chain, record in enumerate(properties.chains)
        if record.is_singleton
    }
    edges: set[tuple[int, int]] = {(int(bottom), int(member)) for member in members}
    for member in members:
        edges.add((int(member), int(top)))
        singleton = properties.singleton_chain(member)
        for super_chain in properties.super_chains(singleton):
            super_property = singleton_entities.get(super_chain)
            if super_property is not None:
                edges.add((int(member), int(super_property)))
    return _from_relation(
        compiled,
        members,
        edges,
        EntityKind.OBJECT_PROPERTY,
        top,
        bottom,
    )


def validate_taxonomy(
    compiled: CompiledOntology,
    taxonomy: RawTaxonomy,
    kind: EntityKind,
) -> RawTaxonomy:
    """Validate kind, coverage, special nodes, and direct-edge minimality."""

    if not isinstance(compiled, CompiledOntology):
        raise TypeError("compiled must be CompiledOntology")
    return validate_taxonomy_entities(compiled.entities, taxonomy, kind)


def validate_taxonomy_entities(
    entities: tuple[EntityRecord, ...],
    taxonomy: RawTaxonomy,
    kind: EntityKind,
) -> RawTaxonomy:
    """Validate a backend taxonomy against bounded facade entity metadata."""

    if not isinstance(entities, tuple) or any(
        not isinstance(record, EntityRecord) for record in entities
    ):
        raise TypeError("entities must be a tuple of EntityRecord values")
    if not isinstance(taxonomy, RawTaxonomy):
        raise TypeError("taxonomy must be RawTaxonomy")
    if kind not in {EntityKind.CLASS, EntityKind.OBJECT_PROPERTY}:
        raise ValueError("taxonomy kind must be CLASS or OBJECT_PROPERTY")
    expected = tuple(
        EntityId(index) for index, record in enumerate(entities) if record.kind is kind
    )
    actual = tuple(sorted(member for node in taxonomy.nodes for member in node))
    if actual != expected:
        raise ValueError("taxonomy member coverage does not match facade entities")
    if any(entities[member].kind is not kind for member in actual):
        raise ValueError("taxonomy contains an entity of the wrong kind")
    top_iri, bottom_iri = (
        (OWL_THING_IRI, OWL_NOTHING_IRI)
        if kind is EntityKind.CLASS
        else (OWL_TOP_OBJECT_PROPERTY_IRI, OWL_BOTTOM_OBJECT_PROPERTY_IRI)
    )
    top = _metadata_entity(entities, kind, top_iri)
    bottom = _metadata_entity(entities, kind, bottom_iri)
    if top not in taxonomy.nodes[taxonomy.top]:
        raise ValueError("taxonomy top node does not contain the predefined top entity")
    if bottom not in taxonomy.nodes[taxonomy.bottom]:
        raise ValueError("taxonomy bottom node does not contain the predefined bottom entity")
    if transitive_reduction(len(taxonomy.nodes), taxonomy.direct_edges) != taxonomy.direct_edges:
        raise ValueError("taxonomy direct edges contain a transitive redundancy")
    return taxonomy


def _metadata_entity(
    entities: tuple[EntityRecord, ...],
    kind: EntityKind,
    iri: str,
) -> EntityId:
    for index, record in enumerate(entities):
        if record.kind is kind and record.iri == iri:
            return EntityId(index)
    raise ValueError(f"facade entities are missing predefined {kind.name} {iri!r}")


def _from_relation(
    compiled: CompiledOntology,
    members: tuple[EntityId, ...],
    edges: set[tuple[int, int]],
    kind: EntityKind,
    top: EntityId,
    bottom: EntityId,
) -> RawTaxonomy:
    reduced = quotient_and_reduce((int(member) for member in members), edges)
    nodes = tuple(tuple(EntityId(member) for member in node) for node in reduced.nodes)
    top_node = reduced.node_for(int(top))
    bottom_node = reduced.node_for(int(bottom))
    if top_node is None or bottom_node is None:  # pragma: no cover - members validated by IR
        raise AssertionError("taxonomy reduction lost a predefined entity")
    taxonomy = RawTaxonomy(
        nodes=nodes,
        direct_edges=reduced.direct_edges,
        top=top_node,
        bottom=bottom_node,
    )
    return validate_taxonomy(compiled, taxonomy, kind)


def _collapsed_taxonomy(
    compiled: CompiledOntology,
    members: tuple[EntityId, ...],
    kind: EntityKind,
    top: EntityId,
    bottom: EntityId,
) -> RawTaxonomy:
    taxonomy = RawTaxonomy(nodes=(members,), direct_edges=(), top=0, bottom=0)
    if top not in members or bottom not in members:  # pragma: no cover - frozen IR invariant
        raise AssertionError("collapsed taxonomy is missing its predefined bounds")
    return validate_taxonomy(compiled, taxonomy, kind)


def _entity_ids(compiled: CompiledOntology, kind: EntityKind) -> tuple[EntityId, ...]:
    return tuple(
        EntityId(index) for index, record in enumerate(compiled.entities) if record.kind is kind
    )


def _entity(compiled: CompiledOntology, kind: EntityKind, iri: str) -> EntityId:
    return EntityId(
        next(
            index
            for index, record in enumerate(compiled.entities)
            if record.kind is kind and record.iri == iri
        )
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


__all__ = [
    "build_class_taxonomy",
    "build_object_property_taxonomy",
    "class_taxonomy",
    "object_property_taxonomy",
    "validate_taxonomy",
    "validate_taxonomy_entities",
]
