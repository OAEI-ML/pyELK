from __future__ import annotations

import pytest

from pyelk.reasoning.reduction import quotient_and_reduce, transitive_reduction


def test_chain_and_diamond_remove_only_transitive_edges() -> None:
    chain = quotient_and_reduce(
        (0, 1, 2, 3),
        ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)),
    )
    assert chain.nodes == ((0,), (1,), (2,), (3,))
    assert chain.direct_edges == ((0, 1), (1, 2), (2, 3))

    diamond = transitive_reduction(
        4,
        ((0, 1), (0, 2), (0, 3), (1, 3), (2, 3)),
    )
    assert diamond == ((0, 1), (0, 2), (1, 3), (2, 3))


def test_mutual_reachability_cycles_are_collapsed_before_reduction() -> None:
    graph = quotient_and_reduce(
        (10, 20, 30, 40, 50),
        (
            (10, 20),
            (20, 30),
            (30, 10),
            (20, 40),
            (30, 40),
            (40, 50),
        ),
    )
    assert graph.nodes == ((10, 20, 30), (40,), (50,))
    assert graph.direct_edges == ((0, 1), (1, 2))
    assert graph.node_for(20) == 0
    assert graph.node_for(99) is None


def test_order_duplicates_and_reflexive_input_do_not_change_the_quotient() -> None:
    edges = ((7, 3), (3, 9), (7, 9), (3, 3), (7, 3))
    expected = quotient_and_reduce((9, 7, 3), edges)
    assert quotient_and_reduce(reversed((9, 7, 3)), reversed(edges)) == expected
    assert expected.nodes == ((3,), (7,), (9,))
    assert expected.direct_edges == ((0, 2), (1, 0))


def test_disconnected_members_remain_canonical_singletons() -> None:
    graph = quotient_and_reduce((4, 1, 9), ())
    assert graph.nodes == ((1,), (4,), (9,))
    assert graph.direct_edges == ()


def test_sparse_hundred_thousand_node_chain_is_iterative_and_linear_shape() -> None:
    size = 100_000
    graph = quotient_and_reduce(
        range(size),
        ((node, node + 1) for node in range(size - 1)),
    )
    assert len(graph.nodes) == size
    assert len(graph.direct_edges) == size - 1
    assert graph.direct_edges[:2] == ((0, 1), (1, 2))
    assert graph.direct_edges[-1] == (size - 2, size - 1)


def test_small_dense_closure_uses_the_same_exact_reduction() -> None:
    size = 300
    reduced = transitive_reduction(
        size,
        ((sub, super_) for sub in range(size) for super_ in range(sub + 1, size)),
    )
    assert reduced == tuple((node, node + 1) for node in range(size - 1))


@pytest.mark.parametrize(
    ("members", "edges", "match"),
    (
        ((0, 0), (), "unique"),
        ((0,), ((0, 1),), "unknown member"),
        ((0,), ((0,),), "pairs"),
        ((True,), (), "nonnegative"),
    ),
)
def test_quotient_rejects_malformed_graphs(
    members: tuple[int, ...],
    edges: tuple[tuple[int, ...], ...],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        quotient_and_reduce(members, edges)  # type: ignore[arg-type]


def test_dag_reduction_rejects_cycles_self_edges_and_bad_counts() -> None:
    with pytest.raises(ValueError, match="acyclic"):
        transitive_reduction(2, ((0, 1), (1, 0)))
    with pytest.raises(ValueError, match="self"):
        transitive_reduction(1, ((0, 0),))
    with pytest.raises(ValueError, match="node_count"):
        transitive_reduction(-1, ())
