from __future__ import annotations

from pyelk.indexing.ir import EntityId, ExpressionId, PropertyChainId
from pyelk.reasoning.conclusions import (
    BackwardLink,
    ContextInitialization,
    ForwardLink,
    Propagation,
    SubClassInclusionComposed,
    SubClassInclusionDecomposed,
    SubContextInitialization,
)
from pyelk.reasoning.properties import PropertyRange, SubPropertyChain
from pyelk.reasoning.rules import (
    backward_link_composition,
    backward_link_reversed_expanded,
    forward_link_composition,
    propagation_generated,
    subclass_inclusion_object_has_self_property_range,
    subclass_inclusion_range,
)

_MIDDLE = ExpressionId(1)
_SOURCE = ExpressionId(2)
_TARGET = ExpressionId(3)
_FILLER = ExpressionId(4)
_CARRY = ExpressionId(5)
_LEFT_PROPERTY = EntityId(6)
_SUPER_PROPERTY = EntityId(7)
_LEFT_CHAIN = PropertyChainId(8)
_RIGHT_CHAIN = PropertyChainId(9)
_FIRST_CHAIN = PropertyChainId(10)
_SUFFIX_CHAIN = PropertyChainId(11)
_COMPOSITION = PropertyChainId(12)
_SUPER_CHAIN = PropertyChainId(13)


def _composition_premises() -> tuple[
    BackwardLink,
    SubPropertyChain,
    ForwardLink,
    SubPropertyChain,
]:
    return (
        BackwardLink(_MIDDLE, _LEFT_PROPERTY, _SOURCE),
        SubPropertyChain(_LEFT_CHAIN, _FIRST_CHAIN),
        ForwardLink(_MIDDLE, _RIGHT_CHAIN, _TARGET),
        SubPropertyChain(_RIGHT_CHAIN, _SUFFIX_CHAIN),
    )


def test_forward_link_composition_inference() -> None:
    backward, left, forward, right = _composition_premises()
    arguments = (_COMPOSITION, _LEFT_CHAIN, _FIRST_CHAIN, _SUFFIX_CHAIN)
    assert forward_link_composition(backward, left, forward, right, *arguments) == ForwardLink(
        _SOURCE, _COMPOSITION, _TARGET
    )
    assert forward_link_composition(None, left, forward, right, *arguments) is None
    assert forward_link_composition(backward, None, forward, right, *arguments) is None
    assert forward_link_composition(backward, left, None, right, *arguments) is None
    assert forward_link_composition(backward, left, forward, None, *arguments) is None
    assert (
        forward_link_composition(
            backward,
            SubPropertyChain(_LEFT_CHAIN, _SUFFIX_CHAIN),
            forward,
            right,
            *arguments,
        )
        is None
    )


def test_backward_link_composition_inference() -> None:
    backward, left, forward, right = _composition_premises()
    superproperty = SubPropertyChain(_COMPOSITION, _SUPER_CHAIN)
    assert backward_link_composition(
        backward,
        left,
        forward,
        right,
        superproperty,
        _COMPOSITION,
        _SUPER_PROPERTY,
        _LEFT_CHAIN,
        _FIRST_CHAIN,
        _SUFFIX_CHAIN,
        _SUPER_CHAIN,
    ) == BackwardLink(_TARGET, _SUPER_PROPERTY, _SOURCE)
    assert (
        backward_link_composition(
            backward,
            left,
            forward,
            right,
            None,
            _COMPOSITION,
            _SUPER_PROPERTY,
            _LEFT_CHAIN,
            _FIRST_CHAIN,
            _SUFFIX_CHAIN,
            _SUPER_CHAIN,
        )
        is None
    )


def test_backward_link_reversed_expanded_inference() -> None:
    forward = ForwardLink(_SOURCE, _RIGHT_CHAIN, _TARGET)
    subproperty = SubPropertyChain(_RIGHT_CHAIN, _SUPER_CHAIN)
    assert backward_link_reversed_expanded(
        forward, subproperty, _SUPER_PROPERTY, _SUPER_CHAIN
    ) == BackwardLink(_TARGET, _SUPER_PROPERTY, _SOURCE)
    assert backward_link_reversed_expanded(None, subproperty, _SUPER_PROPERTY, _SUPER_CHAIN) is None
    assert backward_link_reversed_expanded(forward, None, _SUPER_PROPERTY, _SUPER_CHAIN) is None


def test_propagation_generated_inference() -> None:
    initialization = SubContextInitialization(_MIDDLE, _LEFT_PROPERTY)
    filler = SubClassInclusionComposed(_MIDDLE, _FILLER)
    subproperty = SubPropertyChain(_LEFT_CHAIN, _SUPER_CHAIN)
    arguments = (_FILLER, _CARRY, _LEFT_CHAIN, _SUPER_CHAIN)
    assert propagation_generated(initialization, filler, subproperty, *arguments) == Propagation(
        _MIDDLE, _LEFT_PROPERTY, _CARRY
    )
    assert propagation_generated(None, filler, subproperty, *arguments) is None
    assert propagation_generated(initialization, None, subproperty, *arguments) is None
    assert propagation_generated(initialization, filler, None, *arguments) is None


def test_subclass_inclusion_range_inference() -> None:
    initialization = ContextInitialization(_TARGET)
    property_range = PropertyRange(_LEFT_PROPERTY, _FILLER)
    assert subclass_inclusion_range(initialization, property_range) == SubClassInclusionDecomposed(
        _TARGET, _FILLER
    )
    assert subclass_inclusion_range(None, property_range) is None
    assert subclass_inclusion_range(initialization, None) is None


def test_subclass_inclusion_object_has_self_property_range_inference() -> None:
    premise = SubClassInclusionDecomposed(_SOURCE, _CARRY)
    property_range = PropertyRange(_LEFT_PROPERTY, _FILLER)
    assert subclass_inclusion_object_has_self_property_range(
        premise, property_range, _CARRY, _LEFT_PROPERTY
    ) == SubClassInclusionDecomposed(_SOURCE, _FILLER)
    assert (
        subclass_inclusion_object_has_self_property_range(
            None, property_range, _CARRY, _LEFT_PROPERTY
        )
        is None
    )
    assert (
        subclass_inclusion_object_has_self_property_range(premise, None, _CARRY, _LEFT_PROPERTY)
        is None
    )
