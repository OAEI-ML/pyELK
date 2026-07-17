from __future__ import annotations

from pyelk.indexing.ir import DisjointGroupId, EntityId, ExpressionId
from pyelk.reasoning.conclusions import (
    BackwardLink,
    ClassInconsistency,
    DisjointSubsumer,
    SubClassInclusionComposed,
    SubClassInclusionDecomposed,
)
from pyelk.reasoning.rules import (
    class_inconsistency_of_disjoint_subsumers,
    class_inconsistency_of_object_complement_of,
    class_inconsistency_of_owl_nothing,
    class_inconsistency_propagated,
    disjoint_subsumer_from_subsumer,
)

_ROOT = ExpressionId(1)
_SOURCE = ExpressionId(2)
_MEMBER = ExpressionId(3)
_COMPLEMENT = ExpressionId(4)
_GROUP = DisjointGroupId(5)


def test_disjoint_subsumer_from_subsumer_inference() -> None:
    premise = SubClassInclusionComposed(_ROOT, _MEMBER)
    assert disjoint_subsumer_from_subsumer(premise, _MEMBER, _GROUP, 1) == DisjointSubsumer(
        _ROOT, _GROUP, 1
    )
    assert disjoint_subsumer_from_subsumer(None, _MEMBER, _GROUP, 1) is None


def test_class_inconsistency_of_disjoint_subsumers_inference() -> None:
    first = DisjointSubsumer(_ROOT, _GROUP, 0)
    second = DisjointSubsumer(_ROOT, _GROUP, 1)
    assert class_inconsistency_of_disjoint_subsumers(first, second) == ClassInconsistency(_ROOT)
    assert class_inconsistency_of_disjoint_subsumers(None, second) is None
    assert class_inconsistency_of_disjoint_subsumers(first, None) is None
    assert class_inconsistency_of_disjoint_subsumers(first, first) is None


def test_class_inconsistency_of_object_complement_of_inference() -> None:
    positive = SubClassInclusionComposed(_ROOT, _MEMBER)
    complement = SubClassInclusionDecomposed(_ROOT, _COMPLEMENT)
    assert class_inconsistency_of_object_complement_of(
        positive, complement, _MEMBER, _COMPLEMENT
    ) == ClassInconsistency(_ROOT)
    assert (
        class_inconsistency_of_object_complement_of(None, complement, _MEMBER, _COMPLEMENT) is None
    )
    assert class_inconsistency_of_object_complement_of(positive, None, _MEMBER, _COMPLEMENT) is None


def test_class_inconsistency_of_owl_nothing_inference() -> None:
    premise = SubClassInclusionDecomposed(_ROOT, _MEMBER)
    assert class_inconsistency_of_owl_nothing(premise, _MEMBER) == ClassInconsistency(_ROOT)
    assert class_inconsistency_of_owl_nothing(None, _MEMBER) is None


def test_class_inconsistency_propagated_inference() -> None:
    backward = BackwardLink(_ROOT, EntityId(6), _SOURCE)
    inconsistency = ClassInconsistency(_ROOT)
    assert class_inconsistency_propagated(backward, inconsistency) == ClassInconsistency(_SOURCE)
    assert class_inconsistency_propagated(None, inconsistency) is None
    assert class_inconsistency_propagated(backward, None) is None
