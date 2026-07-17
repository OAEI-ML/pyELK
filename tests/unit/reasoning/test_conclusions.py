from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from pyelk.indexing.ir import DisjointGroupId, EntityId, ExpressionId, PropertyChainId
from pyelk.reasoning.conclusions import (
    BackwardLink,
    ClassInconsistency,
    Conclusion,
    ConclusionKind,
    ContextInitialization,
    DisjointSubsumer,
    ForwardLink,
    Propagation,
    SubClassInclusionComposed,
    SubClassInclusionDecomposed,
    SubContextInitialization,
    conclusion_destination,
    conclusion_key,
)


def _values() -> tuple[Conclusion, ...]:
    return (
        ContextInitialization(ExpressionId(1)),
        SubContextInitialization(ExpressionId(1), EntityId(2)),
        SubClassInclusionDecomposed(ExpressionId(1), ExpressionId(3)),
        SubClassInclusionComposed(ExpressionId(1), ExpressionId(3)),
        ForwardLink(ExpressionId(1), PropertyChainId(4), ExpressionId(5)),
        BackwardLink(ExpressionId(1), EntityId(2), ExpressionId(5)),
        Propagation(ExpressionId(1), EntityId(2), ExpressionId(6)),
        DisjointSubsumer(ExpressionId(1), DisjointGroupId(7), 8),
        ClassInconsistency(ExpressionId(1)),
    )


def test_nine_conclusion_families_have_distinct_compact_keys() -> None:
    values = _values()
    keys = tuple(conclusion_key(value) for value in values)
    assert tuple(key[0] for key in keys) == tuple(ConclusionKind)
    assert len(set(keys)) == len(values) == 9
    assert all(conclusion_destination(value) == 1 for value in values)
    assert len(set(values)) == len(values)


def test_composed_and_decomposed_identity_partitions_are_not_equal() -> None:
    decomposed = SubClassInclusionDecomposed(ExpressionId(2), ExpressionId(9))
    composed = SubClassInclusionComposed(ExpressionId(2), ExpressionId(9))
    assert conclusion_key(decomposed) != conclusion_key(composed)
    decomposed_value: object = decomposed
    assert decomposed_value != composed


def test_conclusions_are_frozen() -> None:
    conclusion = ForwardLink(ExpressionId(1), PropertyChainId(2), ExpressionId(3))
    with pytest.raises(FrozenInstanceError):
        conclusion.target = ExpressionId(4)  # type: ignore[misc]


@pytest.mark.parametrize("bad", (-1, 0xFFFFFFFF, True))
def test_all_numeric_fields_reject_invalid_u32_ids(bad: int) -> None:
    constructors = (
        lambda: ContextInitialization(ExpressionId(bad)),
        lambda: SubContextInitialization(ExpressionId(0), EntityId(bad)),
        lambda: SubClassInclusionDecomposed(ExpressionId(0), ExpressionId(bad)),
        lambda: SubClassInclusionComposed(ExpressionId(0), ExpressionId(bad)),
        lambda: ForwardLink(ExpressionId(0), PropertyChainId(bad), ExpressionId(0)),
        lambda: BackwardLink(ExpressionId(0), EntityId(bad), ExpressionId(0)),
        lambda: Propagation(ExpressionId(0), EntityId(0), ExpressionId(bad)),
        lambda: DisjointSubsumer(ExpressionId(0), DisjointGroupId(0), bad),
        lambda: ClassInconsistency(ExpressionId(bad)),
    )
    for construct in constructors:
        with pytest.raises(ValueError):
            construct()


def test_unknown_runtime_value_is_rejected() -> None:
    with pytest.raises(TypeError, match="unsupported conclusion"):
        conclusion_key(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="unsupported conclusion"):
        conclusion_destination(object())  # type: ignore[arg-type]
