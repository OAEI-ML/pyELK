"""Pure ELK 0.6.0 class-inference functions and rule dispatch.

Inference functions in this module validate only their structural premises and produce one
structural conclusion. They never insert into a context or call another rule recursively.
The occurrence-aware dispatcher is defined below the inference catalogue.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Protocol, TypeVar

from pyelk.indexing.ir import (
    OWL_NOTHING_IRI,
    OWL_THING_IRI,
    U32_RESERVED,
    CompiledOntology,
    DisjointGroupId,
    EntityId,
    EntityKind,
    ExpressionId,
    ExpressionTag,
    PropertyChainId,
)
from pyelk.reasoning.conclusions import (
    BackwardLink,
    ClassInconsistency,
    Conclusion,
    ContextInitialization,
    DisjointSubsumer,
    ForwardLink,
    Propagation,
    SubClassInclusionComposed,
    SubClassInclusionDecomposed,
    SubContextInitialization,
)
from pyelk.reasoning.contexts import ContextState
from pyelk.reasoning.properties import PropertyChainRecord, PropertyRange, SubPropertyChain

_Premise = TypeVar("_Premise")


def _premise(
    value: _Premise | None,
    expected: type[_Premise],
    field_name: str,
) -> _Premise | None:
    if value is None:
        return None
    if not isinstance(value, expected):
        raise TypeError(f"{field_name} must be {expected.__name__} or None")
    return value


def _checked_position(position: object) -> int:
    if (
        isinstance(position, bool)
        or not isinstance(position, int)
        or not 0 <= position < U32_RESERVED
    ):
        raise ValueError("position must be a u32 ID excluding 0xffffffff")
    return position


def context_initialization_no_premises(root: ExpressionId) -> ContextInitialization:
    """Implement pinned ``ContextInitializationNoPremises``."""

    return ContextInitialization(root)


def subcontext_initialization_no_premises(
    destination: ExpressionId,
    relation: EntityId,
) -> SubContextInitialization:
    """Implement pinned ``SubContextInitializationNoPremises``."""

    return SubContextInitialization(destination, relation)


def subclass_inclusion_tautology(
    initialization: ContextInitialization | None,
) -> SubClassInclusionDecomposed | None:
    """Derive that an initialized context root subsumes itself."""

    premise = _premise(initialization, ContextInitialization, "initialization")
    return None if premise is None else SubClassInclusionDecomposed(premise.root, premise.root)


def subclass_inclusion_owl_thing(
    initialization: ContextInitialization | None,
    owl_thing: ExpressionId,
) -> SubClassInclusionComposed | None:
    """Derive the indexed ``owl:Thing`` for an initialized context."""

    premise = _premise(initialization, ContextInitialization, "initialization")
    return None if premise is None else SubClassInclusionComposed(premise.root, owl_thing)


def subclass_inclusion_expanded_subclass_of(
    premise: SubClassInclusionComposed | None,
    sub_expression: ExpressionId,
    super_expression: ExpressionId,
) -> SubClassInclusionDecomposed | None:
    """Expand one indexed ``SubClassOf`` row from its composed subclass premise."""

    checked = _premise(premise, SubClassInclusionComposed, "subclass premise")
    if checked is None or checked.subsumer != sub_expression:
        return None
    return SubClassInclusionDecomposed(checked.destination, super_expression)


def subclass_inclusion_expanded_definition(
    premise: SubClassInclusionDecomposed | None,
    defined_class: ExpressionId,
    definition: ExpressionId,
) -> SubClassInclusionDecomposed | None:
    """Expand the decomposition direction of one named definition."""

    checked = _premise(premise, SubClassInclusionDecomposed, "definition premise")
    if checked is None or checked.subsumer != defined_class:
        return None
    return SubClassInclusionDecomposed(checked.destination, definition)


def subclass_inclusion_expanded_first_equivalent_class(
    premise: SubClassInclusionComposed | None,
    first_expression: ExpressionId,
    second_expression: ExpressionId,
) -> SubClassInclusionDecomposed | None:
    """Expand the first member from a composed second equivalent member."""

    checked = _premise(premise, SubClassInclusionComposed, "equivalent-class premise")
    if checked is None or checked.subsumer != second_expression:
        return None
    return SubClassInclusionDecomposed(checked.destination, first_expression)


def subclass_inclusion_expanded_second_equivalent_class(
    premise: SubClassInclusionComposed | None,
    first_expression: ExpressionId,
    second_expression: ExpressionId,
) -> SubClassInclusionDecomposed | None:
    """Expand the second member from a composed first equivalent member."""

    checked = _premise(premise, SubClassInclusionComposed, "equivalent-class premise")
    if checked is None or checked.subsumer != first_expression:
        return None
    return SubClassInclusionDecomposed(checked.destination, second_expression)


def subclass_inclusion_composed_defined_class(
    premise: SubClassInclusionComposed | None,
    definition: ExpressionId,
    defined_class: ExpressionId,
) -> SubClassInclusionComposed | None:
    """Compose a named defined class from its definition."""

    checked = _premise(premise, SubClassInclusionComposed, "definition premise")
    if checked is None or checked.subsumer != definition:
        return None
    return SubClassInclusionComposed(checked.destination, defined_class)


def subclass_inclusion_composed_of_decomposed(
    premise: SubClassInclusionDecomposed | None,
) -> SubClassInclusionComposed | None:
    """Mirror every novel decomposed subsumer into the composed partition."""

    checked = _premise(premise, SubClassInclusionDecomposed, "decomposed premise")
    if checked is None:
        return None
    return SubClassInclusionComposed(checked.destination, checked.subsumer)


def subclass_inclusion_decomposed_first_conjunct(
    premise: SubClassInclusionDecomposed | None,
    conjunction: ExpressionId,
    first_conjunct: ExpressionId,
) -> SubClassInclusionDecomposed | None:
    """Decompose the first operand of an indexed intersection."""

    checked = _premise(premise, SubClassInclusionDecomposed, "intersection premise")
    if checked is None or checked.subsumer != conjunction:
        return None
    return SubClassInclusionDecomposed(checked.destination, first_conjunct)


def subclass_inclusion_decomposed_second_conjunct(
    premise: SubClassInclusionDecomposed | None,
    conjunction: ExpressionId,
    second_conjunct: ExpressionId,
) -> SubClassInclusionDecomposed | None:
    """Decompose the second operand of an indexed intersection."""

    checked = _premise(premise, SubClassInclusionDecomposed, "intersection premise")
    if checked is None or checked.subsumer != conjunction:
        return None
    return SubClassInclusionDecomposed(checked.destination, second_conjunct)


def subclass_inclusion_composed_object_intersection_of(
    first_premise: SubClassInclusionComposed | None,
    second_premise: SubClassInclusionComposed | None,
    first_conjunct: ExpressionId,
    second_conjunct: ExpressionId,
    conjunction: ExpressionId,
) -> SubClassInclusionComposed | None:
    """Compose an intersection when both exact conjunct premises are present."""

    first = _premise(first_premise, SubClassInclusionComposed, "first conjunct premise")
    second = _premise(second_premise, SubClassInclusionComposed, "second conjunct premise")
    if (
        first is None
        or second is None
        or first.destination != second.destination
        or first.subsumer != first_conjunct
        or second.subsumer != second_conjunct
    ):
        return None
    return SubClassInclusionComposed(first.destination, conjunction)


def subclass_inclusion_composed_object_union_of(
    premise: SubClassInclusionComposed | None,
    disjunct: ExpressionId,
    union: ExpressionId,
    position: int,
) -> SubClassInclusionComposed | None:
    """Compose an indexed union from one registered disjunct position."""

    _checked_position(position)
    checked = _premise(premise, SubClassInclusionComposed, "union premise")
    if checked is None or checked.subsumer != disjunct:
        return None
    return SubClassInclusionComposed(checked.destination, union)


def subclass_inclusion_composed_object_some_values_from(
    backward_premise: BackwardLink | None,
    propagation_premise: Propagation | None,
) -> SubClassInclusionComposed | None:
    """Carry an existential back to a compatible backward-link source."""

    backward = _premise(backward_premise, BackwardLink, "backward-link premise")
    propagation = _premise(propagation_premise, Propagation, "propagation premise")
    if (
        backward is None
        or propagation is None
        or backward.destination != propagation.destination
        or backward.relation != propagation.relation
    ):
        return None
    return SubClassInclusionComposed(backward.source, propagation.carry_existential)


def forward_link_of_object_some_values_from(
    premise: SubClassInclusionDecomposed | None,
    existential: ExpressionId,
    relation_chain: PropertyChainId,
    target: ExpressionId,
) -> ForwardLink | None:
    """Create the forward half of a decomposed existential link."""

    checked = _premise(premise, SubClassInclusionDecomposed, "existential premise")
    if checked is None or checked.subsumer != existential:
        return None
    return ForwardLink(checked.destination, relation_chain, target)


def backward_link_of_object_some_values_from(
    premise: SubClassInclusionDecomposed | None,
    existential: ExpressionId,
    relation: EntityId,
    target: ExpressionId,
) -> BackwardLink | None:
    """Create the backward half of a decomposed existential link."""

    checked = _premise(premise, SubClassInclusionDecomposed, "existential premise")
    if checked is None or checked.subsumer != existential:
        return None
    return BackwardLink(target, relation, checked.destination)


def forward_link_of_object_has_self(
    premise: SubClassInclusionDecomposed | None,
    has_self: ExpressionId,
    relation_chain: PropertyChainId,
) -> ForwardLink | None:
    """Create a reflexive forward link from a decomposed has-self expression."""

    checked = _premise(premise, SubClassInclusionDecomposed, "has-self premise")
    if checked is None or checked.subsumer != has_self:
        return None
    return ForwardLink(checked.destination, relation_chain, checked.destination)


def backward_link_of_object_has_self(
    premise: SubClassInclusionDecomposed | None,
    has_self: ExpressionId,
    relation: EntityId,
) -> BackwardLink | None:
    """Create a reflexive backward link from a decomposed has-self expression."""

    checked = _premise(premise, SubClassInclusionDecomposed, "has-self premise")
    if checked is None or checked.subsumer != has_self:
        return None
    return BackwardLink(checked.destination, relation, checked.destination)


def forward_link_composition(
    backward_premise: BackwardLink | None,
    left_subproperty_premise: SubPropertyChain | None,
    forward_premise: ForwardLink | None,
    right_subproperty_premise: SubPropertyChain | None,
    composition: PropertyChainId,
    backward_relation_chain: PropertyChainId,
    composition_first_chain: PropertyChainId,
    composition_suffix_chain: PropertyChainId,
) -> ForwardLink | None:
    """Compose a backward and forward link into a longer forward chain."""

    backward = _premise(backward_premise, BackwardLink, "backward-link premise")
    left = _premise(left_subproperty_premise, SubPropertyChain, "left subproperty premise")
    forward = _premise(forward_premise, ForwardLink, "forward-link premise")
    right = _premise(right_subproperty_premise, SubPropertyChain, "right subproperty premise")
    if (
        backward is None
        or left is None
        or forward is None
        or right is None
        or backward.destination != forward.destination
        or left != SubPropertyChain(backward_relation_chain, composition_first_chain)
        or right != SubPropertyChain(forward.chain, composition_suffix_chain)
    ):
        return None
    return ForwardLink(backward.source, composition, forward.target)


def backward_link_composition(
    backward_premise: BackwardLink | None,
    left_subproperty_premise: SubPropertyChain | None,
    forward_premise: ForwardLink | None,
    right_subproperty_premise: SubPropertyChain | None,
    superproperty_premise: SubPropertyChain | None,
    composition: PropertyChainId,
    super_property: EntityId,
    backward_relation_chain: PropertyChainId,
    composition_first_chain: PropertyChainId,
    composition_suffix_chain: PropertyChainId,
    super_property_chain: PropertyChainId,
) -> BackwardLink | None:
    """Compose links and expand a complex result to one told named super-property."""

    forward_result = forward_link_composition(
        backward_premise,
        left_subproperty_premise,
        forward_premise,
        right_subproperty_premise,
        composition,
        backward_relation_chain,
        composition_first_chain,
        composition_suffix_chain,
    )
    superproperty = _premise(
        superproperty_premise,
        SubPropertyChain,
        "superproperty premise",
    )
    if (
        forward_result is None
        or superproperty is None
        or superproperty != SubPropertyChain(composition, super_property_chain)
    ):
        return None
    return BackwardLink(forward_result.target, super_property, forward_result.destination)


def backward_link_reversed_expanded(
    forward_premise: ForwardLink | None,
    subproperty_premise: SubPropertyChain | None,
    super_property: EntityId,
    super_property_chain: PropertyChainId,
) -> BackwardLink | None:
    """Reverse a forward chain after expansion to one named super-property."""

    forward = _premise(forward_premise, ForwardLink, "forward-link premise")
    subproperty = _premise(subproperty_premise, SubPropertyChain, "subproperty premise")
    if (
        forward is None
        or subproperty is None
        or subproperty != SubPropertyChain(forward.chain, super_property_chain)
    ):
        return None
    return BackwardLink(forward.target, super_property, forward.destination)


def propagation_generated(
    initialization_premise: SubContextInitialization | None,
    filler_premise: SubClassInclusionComposed | None,
    subproperty_premise: SubPropertyChain | None,
    filler: ExpressionId,
    carry_existential: ExpressionId,
    initialized_relation_chain: PropertyChainId,
    carry_property_chain: PropertyChainId,
) -> Propagation | None:
    """Generate an existential carry for an initialized compatible subcontext."""

    initialization = _premise(
        initialization_premise,
        SubContextInitialization,
        "subcontext initialization premise",
    )
    filler_inclusion = _premise(
        filler_premise,
        SubClassInclusionComposed,
        "filler premise",
    )
    subproperty = _premise(subproperty_premise, SubPropertyChain, "subproperty premise")
    if (
        initialization is None
        or filler_inclusion is None
        or subproperty is None
        or initialization.destination != filler_inclusion.destination
        or filler_inclusion.subsumer != filler
        or subproperty != SubPropertyChain(initialized_relation_chain, carry_property_chain)
    ):
        return None
    return Propagation(
        initialization.destination,
        initialization.sub_destination_property,
        carry_existential,
    )


def subclass_inclusion_range(
    initialization_premise: ContextInitialization | None,
    range_premise: PropertyRange | None,
) -> SubClassInclusionDecomposed | None:
    """Attach a property range to an initialized range-filler context."""

    initialization = _premise(
        initialization_premise,
        ContextInitialization,
        "initialization premise",
    )
    property_range = _premise(range_premise, PropertyRange, "property-range premise")
    if initialization is None or property_range is None:
        return None
    return SubClassInclusionDecomposed(initialization.root, property_range.range)


def subclass_inclusion_object_has_self_property_range(
    has_self_premise: SubClassInclusionDecomposed | None,
    range_premise: PropertyRange | None,
    has_self: ExpressionId,
    relation: EntityId,
) -> SubClassInclusionDecomposed | None:
    """Attach an inherited range at a decomposed has-self context."""

    self_inclusion = _premise(
        has_self_premise,
        SubClassInclusionDecomposed,
        "has-self premise",
    )
    property_range = _premise(range_premise, PropertyRange, "property-range premise")
    if (
        self_inclusion is None
        or property_range is None
        or self_inclusion.subsumer != has_self
        or property_range.property != relation
    ):
        return None
    return SubClassInclusionDecomposed(self_inclusion.destination, property_range.range)


def disjoint_subsumer_from_subsumer(
    premise: SubClassInclusionComposed | None,
    member: ExpressionId,
    group: DisjointGroupId,
    position: int,
) -> DisjointSubsumer | None:
    """Record the exact disjoint-group member position reached in a context."""

    checked = _premise(premise, SubClassInclusionComposed, "disjoint member premise")
    if checked is None or checked.subsumer != member:
        return None
    return DisjointSubsumer(checked.destination, group, position)


def class_inconsistency_of_disjoint_subsumers(
    first_premise: DisjointSubsumer | None,
    second_premise: DisjointSubsumer | None,
) -> ClassInconsistency | None:
    """Derive contradiction from two distinct positions in one disjoint group."""

    first = _premise(first_premise, DisjointSubsumer, "first disjoint premise")
    second = _premise(second_premise, DisjointSubsumer, "second disjoint premise")
    if (
        first is None
        or second is None
        or first.destination != second.destination
        or first.disjoint_group != second.disjoint_group
        or first.position == second.position
    ):
        return None
    return ClassInconsistency(first.destination)


def class_inconsistency_of_object_complement_of(
    positive_premise: SubClassInclusionComposed | None,
    complement_premise: SubClassInclusionDecomposed | None,
    negated_expression: ExpressionId,
    complement_expression: ExpressionId,
) -> ClassInconsistency | None:
    """Derive contradiction from an expression and its indexed complement."""

    positive = _premise(positive_premise, SubClassInclusionComposed, "positive premise")
    complement = _premise(
        complement_premise,
        SubClassInclusionDecomposed,
        "complement premise",
    )
    if (
        positive is None
        or complement is None
        or positive.destination != complement.destination
        or positive.subsumer != negated_expression
        or complement.subsumer != complement_expression
    ):
        return None
    return ClassInconsistency(positive.destination)


def class_inconsistency_of_owl_nothing(
    premise: SubClassInclusionDecomposed | None,
    owl_nothing: ExpressionId,
) -> ClassInconsistency | None:
    """Derive contradiction when ``owl:Nothing`` is a decomposed subsumer."""

    checked = _premise(premise, SubClassInclusionDecomposed, "owl:Nothing premise")
    if checked is None or checked.subsumer != owl_nothing:
        return None
    return ClassInconsistency(checked.destination)


def class_inconsistency_propagated(
    backward_premise: BackwardLink | None,
    inconsistency_premise: ClassInconsistency | None,
) -> ClassInconsistency | None:
    """Propagate local contradiction over one backward link to its source context."""

    backward = _premise(backward_premise, BackwardLink, "backward-link premise")
    inconsistency = _premise(
        inconsistency_premise,
        ClassInconsistency,
        "inconsistency premise",
    )
    if (
        backward is None
        or inconsistency is None
        or backward.destination != inconsistency.destination
    ):
        return None
    return ClassInconsistency(backward.source)


class ConclusionProducer(Protocol):
    """Destination-aware output sink supplied by the WP7 scheduler."""

    def produce(self, conclusion: Conclusion, /) -> object:
        """Route one local or cross-context structural conclusion."""


class PropertyView(Protocol):
    """Read-only portion of property saturation consumed by class rules."""

    @property
    def chains(self) -> tuple[PropertyChainRecord, ...]: ...

    def compiled_chain(self, chain: PropertyChainId) -> PropertyChainId: ...

    def singleton_chain(self, property_id: EntityId) -> PropertyChainId: ...

    def sub_properties(self, super_chain: PropertyChainId) -> tuple[EntityId, ...]: ...

    def ranges(self, property_id: EntityId) -> tuple[ExpressionId, ...]: ...

    def compositions_by_right(
        self,
        left_property: EntityId,
        *,
        redundant: bool = False,
    ) -> Mapping[PropertyChainId, tuple[PropertyChainId, ...]]: ...

    def compositions_by_left(
        self,
        right_chain: PropertyChainId,
        *,
        redundant: bool = False,
    ) -> Mapping[EntityId, tuple[PropertyChainId, ...]]: ...


# This table is deliberately explicit: it is the audited non-incremental Java rule surface,
# while the value names the structural conclusion family that triggers the Python handler.
RULE_CLASS_TRIGGERS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "RootContextInitializationRule": "context_initialization",
        "OwlThingContextInitRule": "context_initialization",
        "SuperClassFromSubClassRule": "composed_subsumer",
        "ComposedFromDecomposedSubsumerRule": "decomposed_subsumer",
        "EquivalentClassFirstFromSecondRule": "composed_subsumer",
        "EquivalentClassSecondFromFirstRule": "composed_subsumer",
        "IndexedClassFromDefinitionRule": "composed_subsumer",
        "IndexedClassDecompositionRule": "decomposed_subsumer",
        "IndexedObjectComplementOfDecomposition": "decomposed_subsumer",
        "ObjectIntersectionFromFirstConjunctRule": "composed_subsumer",
        "ObjectIntersectionFromSecondConjunctRule": "composed_subsumer",
        "IndexedObjectIntersectionOfDecomposition": "decomposed_subsumer",
        "ObjectUnionFromDisjunctRule": "composed_subsumer",
        "IndexedObjectSomeValuesFromDecomposition": "decomposed_subsumer",
        "IndexedObjectHasSelfDecomposition": "decomposed_subsumer",
        "PropagationFromExistentialFillerRule": "composed_subsumer",
        "PropagationInitializationRule": "subcontext_initialization",
        "SubsumerPropagationRule": "propagation",
        "SubsumerBackwardLinkRule": "backward_link",
        "BackwardLinkFromForwardLinkRule": "forward_link",
        "BackwardLinkChainFromBackwardLinkRule": "backward_link",
        "NonReflexiveBackwardLinkCompositionRule": "forward_link",
        "ReflexiveBackwardLinkCompositionRule": "forward_link",
        "ContradictionFromNegationRule": "composed_subsumer",
        "ContradictionFromOwlNothingRule": "decomposed_subsumer",
        "ContradictionCompositionRule": "disjoint_subsumer",
        "ContradictionOverBackwardLinkRule": "backward_link",
        "ContradictionPropagationRule": "class_inconsistency",
        "OwlNothingDecompositionRule": "decomposed_subsumer",
        "DisjointSubsumerFromMemberRule": "composed_subsumer",
    }
)


def _append_index(
    index: dict[int, set[tuple[int, ...]]],
    key: int,
    value: tuple[int, ...],
) -> None:
    index.setdefault(key, set()).add(value)


def _freeze_index(
    index: dict[int, set[tuple[int, ...]]],
) -> dict[int, tuple[tuple[int, ...], ...]]:
    return {key: tuple(sorted(values)) for key, values in index.items()}


def _class_expression(compiled: CompiledOntology, iri: str) -> ExpressionId:
    entity = EntityId(
        next(
            index
            for index, record in enumerate(compiled.entities)
            if record.kind is EntityKind.CLASS and record.iri == iri
        )
    )
    return ExpressionId(
        next(
            index
            for index, record in enumerate(compiled.expressions)
            if record.tag is ExpressionTag.CLASS and record.arguments == (entity,)
        )
    )


def _emit(producer: ConclusionProducer, conclusion: Conclusion | None) -> None:
    if conclusion is not None:
        producer.produce(conclusion)


class RuleDispatcher:
    """Occurrence-aware, iterative dispatcher for one already-stored novel premise.

    Construction freezes all linked-rule lookups for a compiled ontology. ``dispatch`` only
    reads the supplied context and property view and writes through ``ConclusionProducer``;
    it never inserts a conclusion or recursively applies another rule.
    """

    def __init__(self, compiled: CompiledOntology, properties: PropertyView) -> None:
        if not isinstance(compiled, CompiledOntology):
            raise TypeError("compiled must be CompiledOntology")
        self.compiled = compiled
        self.properties = properties
        self.owl_thing = _class_expression(compiled, OWL_THING_IRI)
        self.owl_nothing = _class_expression(compiled, OWL_NOTHING_IRI)
        self._introduce_thing = compiled.expression_occurrences[self.owl_thing].negative > 0
        self._decompose_nothing = compiled.expression_occurrences[self.owl_nothing].positive > 0

        subclasses: dict[int, set[tuple[int, ...]]] = {}
        for sub_expression, super_expression in compiled.subclass_axioms:
            _append_index(subclasses, sub_expression, (super_expression,))
        self._subclasses = _freeze_index(subclasses)

        definitions_by_class: dict[int, set[tuple[int, ...]]] = {}
        classes_by_definition: dict[int, set[tuple[int, ...]]] = {}
        equivalent_first: dict[int, set[tuple[int, ...]]] = {}
        equivalent_second: dict[int, set[tuple[int, ...]]] = {}
        for first, second in compiled.equivalent_class_axioms:
            if compiled.expressions[first].tag is ExpressionTag.CLASS:
                _append_index(definitions_by_class, first, (second,))
                _append_index(classes_by_definition, second, (first,))
            else:
                _append_index(equivalent_first, second, (first,))
                _append_index(equivalent_second, first, (second,))
        self._definitions_by_class = _freeze_index(definitions_by_class)
        self._classes_by_definition = _freeze_index(classes_by_definition)
        self._equivalent_first = _freeze_index(equivalent_first)
        self._equivalent_second = _freeze_index(equivalent_second)

        intersections_by_first: dict[int, set[tuple[int, ...]]] = {}
        intersections_by_second: dict[int, set[tuple[int, ...]]] = {}
        unions_by_disjunct: dict[int, set[tuple[int, ...]]] = {}
        existentials_by_filler: dict[int, set[tuple[int, ...]]] = {}
        complements_by_negated: dict[int, set[tuple[int, ...]]] = {}
        positive_complements: dict[int, set[tuple[int, ...]]] = {}
        for expression_index, record in enumerate(compiled.expressions):
            expression = ExpressionId(expression_index)
            occurrence = compiled.expression_occurrences[expression_index]
            if record.tag is ExpressionTag.OBJECT_INTERSECTION_OF and occurrence.negative:
                first = ExpressionId(record.arguments[0])
                second = ExpressionId(record.arguments[1])
                _append_index(intersections_by_first, first, (second, expression))
                _append_index(intersections_by_second, second, (first, expression))
            elif record.tag is ExpressionTag.OBJECT_UNION_OF and occurrence.negative:
                for position, argument in enumerate(record.arguments):
                    _append_index(
                        unions_by_disjunct,
                        ExpressionId(argument),
                        (expression, position),
                    )
            elif record.tag is ExpressionTag.OBJECT_SOME_VALUES_FROM and occurrence.negative:
                relation = EntityId(record.arguments[0])
                filler = ExpressionId(record.arguments[1])
                _append_index(existentials_by_filler, filler, (expression, relation))
            elif record.tag is ExpressionTag.OBJECT_COMPLEMENT_OF and occurrence.positive:
                negated = ExpressionId(record.arguments[0])
                _append_index(complements_by_negated, negated, (expression,))
                _append_index(positive_complements, expression, (negated,))
        self._intersections_by_first = _freeze_index(intersections_by_first)
        self._intersections_by_second = _freeze_index(intersections_by_second)
        self._unions_by_disjunct = _freeze_index(unions_by_disjunct)
        self._existentials_by_filler = _freeze_index(existentials_by_filler)
        self._complements_by_negated = _freeze_index(complements_by_negated)
        self._positive_complements = _freeze_index(positive_complements)

        disjoint_by_member: dict[int, set[tuple[int, ...]]] = {}
        for group_index, members in enumerate(compiled.disjoint_groups):
            for position, member in enumerate(members):
                _append_index(disjoint_by_member, member, (group_index, position))
        self._disjoint_by_member = _freeze_index(disjoint_by_member)

        told_super_properties: dict[int, set[tuple[int, ...]]] = {}
        for compiled_chain, super_property in compiled.subproperty_axioms:
            local_chain = properties.compiled_chain(compiled_chain)
            _append_index(told_super_properties, local_chain, (super_property,))
        self._told_super_properties = _freeze_index(told_super_properties)

    def dispatch(
        self,
        state: ContextState,
        premise: Conclusion,
        producer: ConclusionProducer,
    ) -> None:
        """Apply the trigger family for a novel premise already inserted in ``state``."""

        if not isinstance(state, ContextState):
            raise TypeError("state must be ContextState")
        if not state.contains(premise):
            raise ValueError("the dispatched premise must already be stored in its context")
        if isinstance(premise, ContextInitialization):
            self._on_context_initialization(premise, producer)
        elif isinstance(premise, SubContextInitialization):
            self._on_subcontext_initialization(state, premise, producer)
        elif isinstance(premise, SubClassInclusionDecomposed):
            self._on_decomposed_subsumer(state, premise, producer)
        elif isinstance(premise, SubClassInclusionComposed):
            self._on_composed_subsumer(state, premise, producer)
        elif isinstance(premise, ForwardLink):
            self._on_forward_link(state, premise, producer)
        elif isinstance(premise, BackwardLink):
            self._on_backward_link(state, premise, producer)
        elif isinstance(premise, Propagation):
            self._on_propagation(state, premise, producer)
        elif isinstance(premise, DisjointSubsumer):
            self._on_disjoint_subsumer(state, premise, producer)
        elif isinstance(premise, ClassInconsistency):
            self._on_class_inconsistency(state, premise, producer)
        else:  # pragma: no cover - exhaustive union, retained for hostile runtime callers
            raise TypeError(f"unsupported conclusion type: {type(premise).__name__}")

    def _on_context_initialization(
        self,
        premise: ContextInitialization,
        producer: ConclusionProducer,
    ) -> None:
        _emit(producer, subclass_inclusion_tautology(premise))
        if self._introduce_thing:
            _emit(producer, subclass_inclusion_owl_thing(premise, self.owl_thing))

    def _on_subcontext_initialization(
        self,
        state: ContextState,
        premise: SubContextInitialization,
        producer: ConclusionProducer,
    ) -> None:
        relation_chain = self.properties.singleton_chain(premise.sub_destination_property)
        for filler in sorted(state.composed_subsumers):
            for existential_value, property_value in self._existentials_by_filler.get(filler, ()):
                existential = ExpressionId(existential_value)
                carry_property = EntityId(property_value)
                carry_chain = self.properties.singleton_chain(carry_property)
                if premise.sub_destination_property not in self.properties.sub_properties(
                    carry_chain
                ):
                    continue
                conclusion = propagation_generated(
                    premise,
                    SubClassInclusionComposed(state.root, filler),
                    SubPropertyChain(relation_chain, carry_chain),
                    filler,
                    existential,
                    relation_chain,
                    carry_chain,
                )
                if conclusion is not None:
                    producer.produce(conclusion)

    def _on_decomposed_subsumer(
        self,
        state: ContextState,
        premise: SubClassInclusionDecomposed,
        producer: ConclusionProducer,
    ) -> None:
        conclusion: Conclusion | None
        composed = subclass_inclusion_composed_of_decomposed(premise)
        if composed is not None:
            producer.produce(composed)
        for (definition_value,) in self._definitions_by_class.get(premise.subsumer, ()):
            conclusion = subclass_inclusion_expanded_definition(
                premise,
                premise.subsumer,
                ExpressionId(definition_value),
            )
            if conclusion is not None:
                producer.produce(conclusion)

        if premise.subsumer >= len(self.compiled.expressions):
            return
        record = self.compiled.expressions[premise.subsumer]
        occurrence = self.compiled.expression_occurrences[premise.subsumer]
        if record.tag is ExpressionTag.OBJECT_INTERSECTION_OF and occurrence.positive:
            first = subclass_inclusion_decomposed_first_conjunct(
                premise,
                premise.subsumer,
                ExpressionId(record.arguments[0]),
            )
            second = subclass_inclusion_decomposed_second_conjunct(
                premise,
                premise.subsumer,
                ExpressionId(record.arguments[1]),
            )
            if first is not None:
                producer.produce(first)
            if second is not None:
                producer.produce(second)
        elif record.tag is ExpressionTag.OBJECT_SOME_VALUES_FROM and occurrence.positive:
            relation = EntityId(record.arguments[0])
            target = ExpressionId(record.arguments[1])
            relation_chain = self.properties.singleton_chain(relation)
            backward = backward_link_of_object_some_values_from(
                premise,
                premise.subsumer,
                relation,
                target,
            )
            if backward is not None:
                producer.produce(backward)
            if self.properties.compositions_by_left(relation_chain):
                forward = forward_link_of_object_some_values_from(
                    premise,
                    premise.subsumer,
                    relation_chain,
                    target,
                )
                if forward is not None:
                    producer.produce(forward)
        elif record.tag is ExpressionTag.OBJECT_HAS_SELF and occurrence.positive:
            relation = EntityId(record.arguments[0])
            relation_chain = self.properties.singleton_chain(relation)
            backward = backward_link_of_object_has_self(premise, premise.subsumer, relation)
            if backward is not None:
                producer.produce(backward)
            if self.properties.compositions_by_left(relation_chain):
                forward = forward_link_of_object_has_self(
                    premise,
                    premise.subsumer,
                    relation_chain,
                )
                if forward is not None:
                    producer.produce(forward)
            for range_expression in self.properties.ranges(relation):
                conclusion = subclass_inclusion_object_has_self_property_range(
                    premise,
                    PropertyRange(relation, range_expression),
                    premise.subsumer,
                    relation,
                )
                if conclusion is not None:
                    producer.produce(conclusion)
        elif record.tag is ExpressionTag.OBJECT_COMPLEMENT_OF and occurrence.positive:
            for (negated_value,) in self._positive_complements.get(premise.subsumer, ()):
                negated = ExpressionId(negated_value)
                if negated not in state.composed_subsumers:
                    continue
                conclusion = class_inconsistency_of_object_complement_of(
                    SubClassInclusionComposed(state.root, negated),
                    premise,
                    negated,
                    premise.subsumer,
                )
                if conclusion is not None:
                    producer.produce(conclusion)
        if self._decompose_nothing and premise.subsumer == self.owl_nothing:
            conclusion = class_inconsistency_of_owl_nothing(premise, self.owl_nothing)
            if conclusion is not None:
                producer.produce(conclusion)

    def _on_composed_subsumer(
        self,
        state: ContextState,
        premise: SubClassInclusionComposed,
        producer: ConclusionProducer,
    ) -> None:
        conclusion: Conclusion | None
        for (super_value,) in self._subclasses.get(premise.subsumer, ()):
            conclusion = subclass_inclusion_expanded_subclass_of(
                premise,
                premise.subsumer,
                ExpressionId(super_value),
            )
            if conclusion is not None:
                producer.produce(conclusion)
        for (defined_value,) in self._classes_by_definition.get(premise.subsumer, ()):
            conclusion = subclass_inclusion_composed_defined_class(
                premise,
                premise.subsumer,
                ExpressionId(defined_value),
            )
            if conclusion is not None:
                producer.produce(conclusion)
        for (first_value,) in self._equivalent_first.get(premise.subsumer, ()):
            conclusion = subclass_inclusion_expanded_first_equivalent_class(
                premise,
                ExpressionId(first_value),
                premise.subsumer,
            )
            if conclusion is not None:
                producer.produce(conclusion)
        for (second_value,) in self._equivalent_second.get(premise.subsumer, ()):
            conclusion = subclass_inclusion_expanded_second_equivalent_class(
                premise,
                premise.subsumer,
                ExpressionId(second_value),
            )
            if conclusion is not None:
                producer.produce(conclusion)

        for second_value, conjunction_value in self._intersections_by_first.get(
            premise.subsumer, ()
        ):
            second = ExpressionId(second_value)
            if second in state.composed_subsumers:
                self._produce_intersection(
                    premise,
                    SubClassInclusionComposed(state.root, second),
                    premise.subsumer,
                    second,
                    ExpressionId(conjunction_value),
                    producer,
                )
        for first_value, conjunction_value in self._intersections_by_second.get(
            premise.subsumer, ()
        ):
            first = ExpressionId(first_value)
            if first in state.composed_subsumers:
                self._produce_intersection(
                    SubClassInclusionComposed(state.root, first),
                    premise,
                    first,
                    premise.subsumer,
                    ExpressionId(conjunction_value),
                    producer,
                )
        for union_value, position in self._unions_by_disjunct.get(premise.subsumer, ()):
            conclusion = subclass_inclusion_composed_object_union_of(
                premise,
                premise.subsumer,
                ExpressionId(union_value),
                position,
            )
            if conclusion is not None:
                producer.produce(conclusion)

        for existential_value, property_value in self._existentials_by_filler.get(
            premise.subsumer, ()
        ):
            carry_property = EntityId(property_value)
            carry_chain = self.properties.singleton_chain(carry_property)
            compatible = self.properties.sub_properties(carry_chain)
            for relation in sorted(state.initialized_subcontexts):
                if relation not in compatible:
                    continue
                relation_chain = self.properties.singleton_chain(relation)
                conclusion = propagation_generated(
                    SubContextInitialization(state.root, relation),
                    premise,
                    SubPropertyChain(relation_chain, carry_chain),
                    premise.subsumer,
                    ExpressionId(existential_value),
                    relation_chain,
                    carry_chain,
                )
                if conclusion is not None:
                    producer.produce(conclusion)

        for (complement_value,) in self._complements_by_negated.get(premise.subsumer, ()):
            complement = ExpressionId(complement_value)
            if complement not in state.decomposed_subsumers:
                continue
            conclusion = class_inconsistency_of_object_complement_of(
                premise,
                SubClassInclusionDecomposed(state.root, complement),
                premise.subsumer,
                complement,
            )
            if conclusion is not None:
                producer.produce(conclusion)
        for group_value, position in self._disjoint_by_member.get(premise.subsumer, ()):
            conclusion = disjoint_subsumer_from_subsumer(
                premise,
                premise.subsumer,
                DisjointGroupId(group_value),
                position,
            )
            if conclusion is not None:
                producer.produce(conclusion)

    @staticmethod
    def _produce_intersection(
        first_premise: SubClassInclusionComposed,
        second_premise: SubClassInclusionComposed,
        first: ExpressionId,
        second: ExpressionId,
        conjunction: ExpressionId,
        producer: ConclusionProducer,
    ) -> None:
        conclusion = subclass_inclusion_composed_object_intersection_of(
            first_premise,
            second_premise,
            first,
            second,
            conjunction,
        )
        if conclusion is not None:
            producer.produce(conclusion)

    def _on_forward_link(
        self,
        state: ContextState,
        premise: ForwardLink,
        producer: ConclusionProducer,
    ) -> None:
        record = self.properties.chains[premise.chain]
        if not record.is_singleton:
            for (super_value,) in self._told_super_properties.get(premise.chain, ()):
                super_property = EntityId(super_value)
                super_chain = self.properties.singleton_chain(super_property)
                conclusion = backward_link_reversed_expanded(
                    premise,
                    SubPropertyChain(premise.chain, super_chain),
                    super_property,
                    super_chain,
                )
                if conclusion is not None:
                    producer.produce(conclusion)
        compositions = self.properties.compositions_by_left(premise.chain)
        for relation, result_chains in sorted(compositions.items()):
            for source in sorted(state.backward_links.get(relation, ())):
                backward = BackwardLink(state.root, relation, source)
                for result_chain in result_chains:
                    self._produce_link_composition(backward, premise, result_chain, producer)

    def _on_backward_link(
        self,
        state: ContextState,
        premise: BackwardLink,
        producer: ConclusionProducer,
    ) -> None:
        conclusion: Conclusion | None
        producer.produce(subcontext_initialization_no_premises(state.root, premise.relation))
        for carry in sorted(state.propagations.get(premise.relation, ())):
            conclusion = subclass_inclusion_composed_object_some_values_from(
                premise,
                Propagation(state.root, premise.relation, carry),
            )
            if conclusion is not None:
                producer.produce(conclusion)
        if state.inconsistent:
            conclusion = class_inconsistency_propagated(
                premise,
                ClassInconsistency(state.root),
            )
            if conclusion is not None:
                producer.produce(conclusion)
        # Frozen IR v1 represents context roots only by ExpressionId and therefore has no
        # separate IndexedRangeFiller identity.  Attach inherited ranges when the named
        # backward edge reaches its target; this preserves the specified v1 destination.
        for range_expression in self.properties.ranges(premise.relation):
            producer.produce(SubClassInclusionDecomposed(state.root, range_expression))
        compositions = self.properties.compositions_by_right(premise.relation)
        for right_chain, result_chains in sorted(compositions.items()):
            for target in sorted(state.forward_links.get(right_chain, ())):
                forward = ForwardLink(state.root, right_chain, target)
                for result_chain in result_chains:
                    self._produce_link_composition(premise, forward, result_chain, producer)

    def _produce_link_composition(
        self,
        backward: BackwardLink,
        forward: ForwardLink,
        result_chain: PropertyChainId,
        producer: ConclusionProducer,
    ) -> None:
        conclusion: Conclusion | None
        result = self.properties.chains[result_chain]
        if result.suffix_chain is None:
            raise AssertionError("property composition result must be a complex chain")
        backward_chain = self.properties.singleton_chain(backward.relation)
        first_chain = self.properties.singleton_chain(result.first_property)
        left_premise = SubPropertyChain(backward_chain, first_chain)
        right_premise = SubPropertyChain(forward.chain, result.suffix_chain)
        if self._chain_is_extendable(result_chain):
            conclusion = forward_link_composition(
                backward,
                left_premise,
                forward,
                right_premise,
                result_chain,
                backward_chain,
                first_chain,
                result.suffix_chain,
            )
            if conclusion is not None:
                producer.produce(conclusion)
            return
        for (super_value,) in self._told_super_properties.get(result_chain, ()):
            super_property = EntityId(super_value)
            super_chain = self.properties.singleton_chain(super_property)
            conclusion = backward_link_composition(
                backward,
                left_premise,
                forward,
                right_premise,
                SubPropertyChain(result_chain, super_chain),
                result_chain,
                super_property,
                backward_chain,
                first_chain,
                result.suffix_chain,
                super_chain,
            )
            if conclusion is not None:
                producer.produce(conclusion)

    def _chain_is_extendable(self, chain: PropertyChainId) -> bool:
        return bool(
            self.properties.compositions_by_left(chain)
            or self.properties.compositions_by_left(chain, redundant=True)
        )

    @staticmethod
    def _on_propagation(
        state: ContextState,
        premise: Propagation,
        producer: ConclusionProducer,
    ) -> None:
        for source in sorted(state.backward_links.get(premise.relation, ())):
            conclusion = subclass_inclusion_composed_object_some_values_from(
                BackwardLink(state.root, premise.relation, source),
                premise,
            )
            if conclusion is not None:
                producer.produce(conclusion)

    @staticmethod
    def _on_disjoint_subsumer(
        state: ContextState,
        premise: DisjointSubsumer,
        producer: ConclusionProducer,
    ) -> None:
        for position in sorted(state.disjoint_positions.get(premise.disjoint_group, ())):
            if position == premise.position:
                continue
            conclusion = class_inconsistency_of_disjoint_subsumers(
                premise,
                DisjointSubsumer(state.root, premise.disjoint_group, position),
            )
            if conclusion is not None:
                producer.produce(conclusion)

    @staticmethod
    def _on_class_inconsistency(
        state: ContextState,
        premise: ClassInconsistency,
        producer: ConclusionProducer,
    ) -> None:
        for relation, sources in sorted(state.backward_links.items()):
            for source in sorted(sources):
                conclusion = class_inconsistency_propagated(
                    BackwardLink(state.root, relation, source),
                    premise,
                )
                if conclusion is not None:
                    producer.produce(conclusion)


__all__ = [
    "RULE_CLASS_TRIGGERS",
    "ConclusionProducer",
    "PropertyView",
    "RuleDispatcher",
    "backward_link_composition",
    "backward_link_of_object_has_self",
    "backward_link_of_object_some_values_from",
    "backward_link_reversed_expanded",
    "class_inconsistency_of_disjoint_subsumers",
    "class_inconsistency_of_object_complement_of",
    "class_inconsistency_of_owl_nothing",
    "class_inconsistency_propagated",
    "context_initialization_no_premises",
    "disjoint_subsumer_from_subsumer",
    "forward_link_composition",
    "forward_link_of_object_has_self",
    "forward_link_of_object_some_values_from",
    "propagation_generated",
    "subclass_inclusion_composed_defined_class",
    "subclass_inclusion_composed_object_intersection_of",
    "subclass_inclusion_composed_object_some_values_from",
    "subclass_inclusion_composed_object_union_of",
    "subclass_inclusion_composed_of_decomposed",
    "subclass_inclusion_decomposed_first_conjunct",
    "subclass_inclusion_decomposed_second_conjunct",
    "subclass_inclusion_expanded_definition",
    "subclass_inclusion_expanded_first_equivalent_class",
    "subclass_inclusion_expanded_second_equivalent_class",
    "subclass_inclusion_expanded_subclass_of",
    "subclass_inclusion_object_has_self_property_range",
    "subclass_inclusion_owl_thing",
    "subclass_inclusion_range",
    "subclass_inclusion_tautology",
    "subcontext_initialization_no_premises",
]
