"""Ontology-level consistency over saturated ELK contexts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pyelk.indexing.ir import (
    OWL_BOTTOM_OBJECT_PROPERTY_IRI,
    OWL_THING_IRI,
    OWL_TOP_OBJECT_PROPERTY_IRI,
    CompiledOntology,
    EntityId,
    EntityKind,
    ExpressionId,
    ExpressionTag,
)
from pyelk.reasoning.contexts import ContextState, FrozenContext
from pyelk.reasoning.properties import PropertySaturation


@dataclass(frozen=True, slots=True)
class ConsistencyState:
    """Deterministic explanation of the global consistency decision."""

    owl_thing_root: ExpressionId
    individual_roots: tuple[ExpressionId, ...]
    owl_thing_inconsistent: bool
    inconsistent_individuals: tuple[ExpressionId, ...]
    top_object_property_in_bottom: bool

    @property
    def inconsistent(self) -> bool:
        """Whether any pinned global inconsistency condition holds."""

        return bool(
            self.owl_thing_inconsistent
            or self.inconsistent_individuals
            or self.top_object_property_in_bottom
        )


def owl_thing_root(compiled: CompiledOntology) -> ExpressionId:
    """Return the unique compiled expression for ``owl:Thing``."""

    return _named_expression(compiled, EntityKind.CLASS, OWL_THING_IRI)


def occurring_individual_roots(compiled: CompiledOntology) -> tuple[ExpressionId, ...]:
    """Return committed individuals whose positive or negative occurrence asserts existence."""

    roots = (
        ExpressionId(index)
        for index, record in enumerate(compiled.expressions)
        if record.tag is ExpressionTag.INDIVIDUAL
        and (
            compiled.expression_occurrences[index].negative > 0
            or compiled.expression_occurrences[index].positive > 0
        )
    )
    return tuple(roots)


def consistency_roots(compiled: CompiledOntology) -> tuple[ExpressionId, ...]:
    """Roots demanded by the lazy consistency stage."""

    return tuple(sorted({owl_thing_root(compiled), *occurring_individual_roots(compiled)}))


def evaluate_consistency(
    compiled: CompiledOntology,
    properties: PropertySaturation,
    contexts: Mapping[ExpressionId, ContextState | FrozenContext],
) -> ConsistencyState:
    """Evaluate the three pinned ELK ontology-inconsistency conditions."""

    if not isinstance(compiled, CompiledOntology):
        raise TypeError("compiled must be CompiledOntology")
    if not isinstance(properties, PropertySaturation):
        raise TypeError("properties must be PropertySaturation")
    if not isinstance(contexts, Mapping):
        raise TypeError("contexts must be a mapping")
    thing = owl_thing_root(compiled)
    individuals = occurring_individual_roots(compiled)
    thing_context = contexts.get(thing)
    inconsistent_individuals = tuple(
        root for root in individuals if _context_inconsistent(contexts.get(root))
    )
    top_property = _named_entity(
        compiled,
        EntityKind.OBJECT_PROPERTY,
        OWL_TOP_OBJECT_PROPERTY_IRI,
    )
    bottom_property = _named_entity(
        compiled,
        EntityKind.OBJECT_PROPERTY,
        OWL_BOTTOM_OBJECT_PROPERTY_IRI,
    )
    top_chain = properties.singleton_chain(top_property)
    bottom_chain = properties.singleton_chain(bottom_property)
    return ConsistencyState(
        owl_thing_root=thing,
        individual_roots=individuals,
        owl_thing_inconsistent=_context_inconsistent(thing_context),
        inconsistent_individuals=inconsistent_individuals,
        top_object_property_in_bottom=bottom_chain in properties.super_chains(top_chain),
    )


def _context_inconsistent(context: ContextState | FrozenContext | None) -> bool:
    if context is None:
        return False
    if not isinstance(context, (ContextState, FrozenContext)):
        raise TypeError("context mapping contains an unsupported value")
    return bool(context.inconsistent)


def _named_expression(
    compiled: CompiledOntology,
    kind: EntityKind,
    iri: str,
) -> ExpressionId:
    entity = _named_entity(compiled, kind, iri)
    expected_tag = ExpressionTag.CLASS if kind is EntityKind.CLASS else ExpressionTag.INDIVIDUAL
    return ExpressionId(
        next(
            index
            for index, record in enumerate(compiled.expressions)
            if record.tag is expected_tag and record.arguments == (entity,)
        )
    )


def _named_entity(compiled: CompiledOntology, kind: EntityKind, iri: str) -> EntityId:
    return EntityId(
        next(
            index
            for index, record in enumerate(compiled.entities)
            if record.kind is kind and record.iri == iri
        )
    )


__all__ = [
    "ConsistencyState",
    "consistency_roots",
    "evaluate_consistency",
    "occurring_individual_roots",
    "owl_thing_root",
]
