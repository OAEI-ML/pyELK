from __future__ import annotations

import itertools

import pytest

from pyelk.indexing.ir import DisjointGroupId, EntityId, ExpressionId, PropertyChainId
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


def _conclusions() -> tuple[Conclusion, ...]:
    root = ExpressionId(3)
    return (
        ContextInitialization(root),
        SubContextInitialization(root, EntityId(5)),
        SubClassInclusionDecomposed(root, ExpressionId(7)),
        SubClassInclusionComposed(root, ExpressionId(8)),
        ForwardLink(root, PropertyChainId(9), ExpressionId(10)),
        BackwardLink(root, EntityId(11), ExpressionId(12)),
        Propagation(root, EntityId(13), ExpressionId(14)),
        DisjointSubsumer(root, DisjointGroupId(15), 2),
        ClassInconsistency(root),
    )


def test_enqueue_and_insert_suppress_pending_and_stored_duplicates() -> None:
    state = ContextState(ExpressionId(3))
    conclusion = SubClassInclusionComposed(ExpressionId(3), ExpressionId(8))
    assert state.enqueue(conclusion)
    assert not state.enqueue(conclusion)
    assert state.pending_count == 1
    assert state.pop_todo() == conclusion
    assert state.pending_count == 0
    assert state.insert(conclusion)
    assert not state.insert(conclusion)
    assert not state.enqueue(conclusion)
    assert state.conclusion_count == 1
    assert state.composed_subsumers == {ExpressionId(8)}


def test_every_family_updates_its_specialized_index_once() -> None:
    state = ContextState(ExpressionId(3))
    for conclusion in _conclusions():
        assert state.insert(conclusion)
    assert state.initialized and state.inconsistent
    assert state.initialized_subcontexts == {EntityId(5)}
    assert state.decomposed_subsumers == {ExpressionId(7)}
    assert state.composed_subsumers == {ExpressionId(8)}
    assert state.forward_links == {PropertyChainId(9): {ExpressionId(10)}}
    assert state.backward_links == {EntityId(11): {ExpressionId(12)}}
    assert state.propagations == {EntityId(13): {ExpressionId(14)}}
    assert state.disjoint_positions == {DisjointGroupId(15): {2}}
    assert state.conclusion_count == 9


def test_context_rejects_cross_destination_insertion_and_enqueue() -> None:
    state = ContextState(ExpressionId(1))
    foreign = ClassInconsistency(ExpressionId(2))
    with pytest.raises(ValueError, match="belongs to context 2"):
        state.enqueue(foreign)
    with pytest.raises(ValueError, match="belongs to context 2"):
        state.insert(foreign)


def test_queue_state_transitions_and_saturated_empty_invariant() -> None:
    state = ContextState(ExpressionId(3))
    assert state.mark_queued()
    assert not state.mark_queued()
    state.claim()
    assert not state.queued and not state.saturated
    assert state.enqueue(ContextInitialization(ExpressionId(3)))
    with pytest.raises(RuntimeError, match="pending conclusions"):
        state.mark_saturated()
    conclusion = state.pop_todo()
    assert conclusion is not None and state.insert(conclusion)
    state.mark_saturated()
    assert state.freeze().saturated
    assert state.enqueue(SubClassInclusionComposed(ExpressionId(3), ExpressionId(4)))
    assert not state.freeze().saturated


def test_freeze_is_deterministic_under_insertion_and_pending_permutations() -> None:
    values = _conclusions()
    expected = None
    for order in (values, tuple(reversed(values)), values[::2] + values[1::2]):
        state = ContextState(ExpressionId(3))
        for conclusion in order:
            assert state.insert(conclusion)
        state.mark_saturated()
        frozen = state.freeze()
        expected = frozen if expected is None else expected
        assert frozen == expected

    pending_expected = None
    for order in itertools.permutations(values[:3]):
        state = ContextState(ExpressionId(3))
        for conclusion in order:
            assert state.enqueue(conclusion)
        frozen = state.freeze()
        pending_expected = frozen if pending_expected is None else pending_expected
        assert frozen == pending_expected


def test_conclusions_iteration_is_sorted_by_compact_identity() -> None:
    state = ContextState(ExpressionId(3))
    for conclusion in reversed(_conclusions()):
        assert state.insert(conclusion)
    assert state.conclusions() == _conclusions()
