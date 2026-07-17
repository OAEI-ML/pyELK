"""Pure ELK 0.6.0 class-inference functions and rule dispatch.

Inference functions in this module validate only their structural premises and produce one
structural conclusion. They never insert into a context or call another rule recursively.
The occurrence-aware dispatcher is defined below the inference catalogue.
"""

from __future__ import annotations

from typing import TypeVar

from pyelk.indexing.ir import U32_RESERVED, DisjointGroupId, EntityId, ExpressionId, PropertyChainId
from pyelk.reasoning.conclusions import (
    BackwardLink,
    ClassInconsistency,
    ContextInitialization,
    DisjointSubsumer,
    ForwardLink,
    Propagation,
    SubClassInclusionComposed,
    SubClassInclusionDecomposed,
    SubContextInitialization,
)
from pyelk.reasoning.properties import PropertyRange, SubPropertyChain

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


__all__ = [
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
