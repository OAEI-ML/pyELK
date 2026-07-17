from __future__ import annotations

import pytest

from pyelk.indexing.ir import EntityId, ExpressionId, PropertyChainId
from pyelk.reasoning.conclusions import (
    BackwardLink,
    ForwardLink,
    Propagation,
    SubClassInclusionComposed,
    SubClassInclusionDecomposed,
)
from pyelk.reasoning.rules import (
    backward_link_of_object_has_self,
    backward_link_of_object_some_values_from,
    forward_link_of_object_has_self,
    forward_link_of_object_some_values_from,
    subclass_inclusion_composed_object_intersection_of,
    subclass_inclusion_composed_object_some_values_from,
    subclass_inclusion_composed_object_union_of,
    subclass_inclusion_decomposed_first_conjunct,
    subclass_inclusion_decomposed_second_conjunct,
)

_ROOT = ExpressionId(1)
_FIRST = ExpressionId(2)
_SECOND = ExpressionId(3)
_CONSTRUCTED = ExpressionId(4)
_TARGET = ExpressionId(5)
_RELATION = EntityId(6)
_CHAIN = PropertyChainId(7)


def test_subclass_inclusion_decomposed_first_conjunct_inference() -> None:
    premise = SubClassInclusionDecomposed(_ROOT, _CONSTRUCTED)
    assert subclass_inclusion_decomposed_first_conjunct(
        premise, _CONSTRUCTED, _FIRST
    ) == SubClassInclusionDecomposed(_ROOT, _FIRST)
    assert subclass_inclusion_decomposed_first_conjunct(None, _CONSTRUCTED, _FIRST) is None


def test_subclass_inclusion_decomposed_second_conjunct_inference() -> None:
    premise = SubClassInclusionDecomposed(_ROOT, _CONSTRUCTED)
    assert subclass_inclusion_decomposed_second_conjunct(
        premise, _CONSTRUCTED, _SECOND
    ) == SubClassInclusionDecomposed(_ROOT, _SECOND)
    assert subclass_inclusion_decomposed_second_conjunct(None, _CONSTRUCTED, _SECOND) is None


def test_subclass_inclusion_composed_object_intersection_of_inference() -> None:
    first = SubClassInclusionComposed(_ROOT, _FIRST)
    second = SubClassInclusionComposed(_ROOT, _SECOND)
    expected = SubClassInclusionComposed(_ROOT, _CONSTRUCTED)
    assert (
        subclass_inclusion_composed_object_intersection_of(
            first, second, _FIRST, _SECOND, _CONSTRUCTED
        )
        == expected
    )
    assert (
        subclass_inclusion_composed_object_intersection_of(
            None, second, _FIRST, _SECOND, _CONSTRUCTED
        )
        is None
    )
    assert (
        subclass_inclusion_composed_object_intersection_of(
            first, None, _FIRST, _SECOND, _CONSTRUCTED
        )
        is None
    )
    duplicate = SubClassInclusionComposed(_ROOT, _FIRST)
    assert (
        subclass_inclusion_composed_object_intersection_of(
            duplicate, duplicate, _FIRST, _FIRST, _CONSTRUCTED
        )
        == expected
    )


def test_subclass_inclusion_composed_object_union_of_inference() -> None:
    premise = SubClassInclusionComposed(_ROOT, _FIRST)
    assert subclass_inclusion_composed_object_union_of(
        premise, _FIRST, _CONSTRUCTED, 2
    ) == SubClassInclusionComposed(_ROOT, _CONSTRUCTED)
    assert subclass_inclusion_composed_object_union_of(None, _FIRST, _CONSTRUCTED, 2) is None
    with pytest.raises(ValueError):
        subclass_inclusion_composed_object_union_of(premise, _FIRST, _CONSTRUCTED, -1)


def test_subclass_inclusion_composed_object_some_values_from_inference() -> None:
    backward = BackwardLink(_ROOT, _RELATION, _TARGET)
    propagation = Propagation(_ROOT, _RELATION, _CONSTRUCTED)
    assert subclass_inclusion_composed_object_some_values_from(
        backward, propagation
    ) == SubClassInclusionComposed(_TARGET, _CONSTRUCTED)
    assert subclass_inclusion_composed_object_some_values_from(None, propagation) is None
    assert subclass_inclusion_composed_object_some_values_from(backward, None) is None
    assert (
        subclass_inclusion_composed_object_some_values_from(
            backward, Propagation(_ROOT, EntityId(8), _CONSTRUCTED)
        )
        is None
    )


def test_forward_link_of_object_some_values_from_inference() -> None:
    premise = SubClassInclusionDecomposed(_ROOT, _CONSTRUCTED)
    assert forward_link_of_object_some_values_from(
        premise, _CONSTRUCTED, _CHAIN, _TARGET
    ) == ForwardLink(_ROOT, _CHAIN, _TARGET)
    assert forward_link_of_object_some_values_from(None, _CONSTRUCTED, _CHAIN, _TARGET) is None


def test_backward_link_of_object_some_values_from_inference() -> None:
    premise = SubClassInclusionDecomposed(_ROOT, _CONSTRUCTED)
    assert backward_link_of_object_some_values_from(
        premise, _CONSTRUCTED, _RELATION, _TARGET
    ) == BackwardLink(_TARGET, _RELATION, _ROOT)
    assert backward_link_of_object_some_values_from(None, _CONSTRUCTED, _RELATION, _TARGET) is None


def test_forward_link_of_object_has_self_inference() -> None:
    premise = SubClassInclusionDecomposed(_ROOT, _CONSTRUCTED)
    assert forward_link_of_object_has_self(premise, _CONSTRUCTED, _CHAIN) == ForwardLink(
        _ROOT, _CHAIN, _ROOT
    )
    assert forward_link_of_object_has_self(None, _CONSTRUCTED, _CHAIN) is None


def test_backward_link_of_object_has_self_inference() -> None:
    premise = SubClassInclusionDecomposed(_ROOT, _CONSTRUCTED)
    assert backward_link_of_object_has_self(premise, _CONSTRUCTED, _RELATION) == BackwardLink(
        _ROOT, _RELATION, _ROOT
    )
    assert backward_link_of_object_has_self(None, _CONSTRUCTED, _RELATION) is None
