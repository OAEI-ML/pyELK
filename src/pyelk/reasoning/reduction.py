"""Deterministic SCC quotient and transitive reduction for taxonomy graphs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from heapq import heappop, heappush

_BITSET_NODE_LIMIT = 8_192


@dataclass(frozen=True, slots=True)
class ReducedGraph:
    """Canonical quotient nodes and direct ``sub -> super`` component edges."""

    nodes: tuple[tuple[int, ...], ...]
    direct_edges: tuple[tuple[int, int], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.nodes, tuple) or any(
            not isinstance(node, tuple) or not node for node in self.nodes
        ):
            raise ValueError("reduced graph nodes must be nonempty tuples")
        if any(
            node[index - 1] >= node[index] for node in self.nodes for index in range(1, len(node))
        ):
            raise ValueError("reduced graph node members must be strictly sorted")
        if any(self.nodes[index - 1] >= self.nodes[index] for index in range(1, len(self.nodes))):
            raise ValueError("reduced graph nodes must be strictly sorted")
        members = [member for node in self.nodes for member in node]
        if any(
            isinstance(member, bool) or not isinstance(member, int) or member < 0
            for member in members
        ):
            raise ValueError("reduced graph members must be nonnegative integer IDs")
        if len(members) != len(set(members)):
            raise ValueError("a reduced graph member may occur in only one node")
        _validated_edges(len(self.nodes), self.direct_edges)

    def node_for(self, member: int) -> int | None:
        """Return the canonical component index containing ``member``."""

        for index, node in enumerate(self.nodes):
            if member in node:
                return index
        return None


def quotient_and_reduce(
    members: Iterable[int],
    edges: Iterable[tuple[int, int]],
) -> ReducedGraph:
    """Collapse mutual reachability and reduce the resulting DAG.

    The implementation is iterative throughout.  Sparse chains therefore require linear
    stack space and time rather than Python recursion or an all-triples relation scan.
    """

    ordered_members = tuple(sorted(_validated_members(members)))
    if not ordered_members:
        edge_values = tuple(edges)
        if edge_values:
            raise ValueError("an empty graph cannot contain edges")
        return ReducedGraph((), ())
    dense_id = {member: index for index, member in enumerate(ordered_members)}
    adjacency_sets: list[set[int]] = [set() for _ in ordered_members]
    reverse_sets: list[set[int]] = [set() for _ in ordered_members]
    for edge in edges:
        sub, super_ = _validated_member_edge(edge, dense_id)
        if sub == super_:
            continue
        adjacency_sets[sub].add(super_)
        reverse_sets[super_].add(sub)
    adjacency = tuple(tuple(sorted(values)) for values in adjacency_sets)
    reverse = tuple(tuple(sorted(values)) for values in reverse_sets)
    components = _strong_components(adjacency, reverse)

    member_components = tuple(
        tuple(sorted(ordered_members[dense] for dense in component)) for component in components
    )
    canonical_nodes = tuple(sorted(member_components))
    node_by_members = {node: index for index, node in enumerate(canonical_nodes)}
    dense_component: list[int] = [-1] * len(ordered_members)
    for component, members_in_component in zip(components, member_components, strict=True):
        node = node_by_members[members_in_component]
        for dense in component:
            dense_component[dense] = node

    component_edges: set[tuple[int, int]] = set()
    for sub, successors in enumerate(adjacency):
        sub_node = dense_component[sub]
        for super_ in successors:
            super_node = dense_component[super_]
            if sub_node != super_node:
                component_edges.add((sub_node, super_node))
    del adjacency_sets, reverse_sets, adjacency, reverse
    return ReducedGraph(
        nodes=canonical_nodes,
        direct_edges=transitive_reduction(len(canonical_nodes), component_edges),
    )


def transitive_reduction(
    node_count: int,
    edges: Iterable[tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
    """Return the unique transitive reduction of a finite DAG.

    Nodes with zero or one successor are handled in constant local work.  For branching
    nodes, one iterative reachability marking pass removes later reachable successors in
    topological order.  This keeps the required 100k-node sparse chain path linear.
    """

    if isinstance(node_count, bool) or not isinstance(node_count, int) or node_count < 0:
        raise ValueError("node_count must be a nonnegative integer")
    unique_edges = edges if isinstance(edges, (set, frozenset)) else set(edges)
    edge_values = _validated_edges(node_count, tuple(sorted(unique_edges)))
    adjacency_sets: list[set[int]] = [set() for _ in range(node_count)]
    indegrees = [0] * node_count
    for sub, super_ in edge_values:
        adjacency_sets[sub].add(super_)
        indegrees[super_] += 1
    adjacency = tuple(tuple(sorted(values)) for values in adjacency_sets)
    topological = _topological_order(adjacency, indegrees)
    rank = [0] * node_count
    for position, node in enumerate(topological):
        rank[node] = position

    if node_count <= _BITSET_NODE_LIMIT and len(edge_values) > node_count * 2:
        return _bitset_reduction(adjacency, topological, rank)

    direct: list[tuple[int, int]] = []
    for sub, successors in enumerate(adjacency):
        if len(successors) <= 1:
            direct.extend((sub, super_) for super_ in successors)
            continue
        covered: set[int] = set()
        for super_ in sorted(successors, key=rank.__getitem__):
            if super_ in covered:
                continue
            direct.append((sub, super_))
            pending = [super_]
            while pending:
                reached = pending.pop()
                if reached in covered:
                    continue
                covered.add(reached)
                pending.extend(adjacency[reached])
    return tuple(sorted(direct))


def _bitset_reduction(
    adjacency: tuple[tuple[int, ...], ...],
    topological: tuple[int, ...],
    rank: list[int],
) -> tuple[tuple[int, int], ...]:
    """Reduce a small/dense DAG with compact Python-integer reachability rows."""

    reachable = [0] * len(adjacency)
    for node in reversed(topological):
        row = 0
        for successor in adjacency[node]:
            row |= (1 << successor) | reachable[successor]
        reachable[node] = row
    direct: list[tuple[int, int]] = []
    for sub, successors in enumerate(adjacency):
        covered = 0
        for super_ in sorted(successors, key=rank.__getitem__):
            bit = 1 << super_
            if covered & bit:
                continue
            direct.append((sub, super_))
            covered |= bit | reachable[super_]
    return tuple(sorted(direct))


def _validated_members(members: Iterable[int]) -> set[int]:
    values: set[int] = set()
    count = 0
    for member in members:
        count += 1
        if isinstance(member, bool) or not isinstance(member, int) or member < 0:
            raise ValueError("graph members must be nonnegative integer IDs")
        values.add(member)
    if len(values) != count:
        raise ValueError("graph members must be unique")
    return values


def _validated_member_edge(
    edge: object,
    dense_id: dict[int, int],
) -> tuple[int, int]:
    if not isinstance(edge, tuple) or len(edge) != 2:
        raise ValueError("graph edges must be pairs")
    sub, super_ = edge
    if (
        isinstance(sub, bool)
        or not isinstance(sub, int)
        or isinstance(super_, bool)
        or not isinstance(super_, int)
        or sub not in dense_id
        or super_ not in dense_id
    ):
        raise ValueError("graph edge references an unknown member")
    return dense_id[sub], dense_id[super_]


def _validated_edges(
    node_count: int,
    edges: tuple[tuple[int, int], ...],
) -> tuple[tuple[int, int], ...]:
    previous: tuple[int, int] | None = None
    for edge in edges:
        if not isinstance(edge, tuple) or len(edge) != 2:
            raise ValueError("graph edges must be pairs")
        sub, super_ = edge
        if (
            isinstance(sub, bool)
            or not isinstance(sub, int)
            or isinstance(super_, bool)
            or not isinstance(super_, int)
            or not 0 <= sub < node_count
            or not 0 <= super_ < node_count
        ):
            raise ValueError("graph edge references an unknown node")
        if sub == super_:
            raise ValueError("a DAG cannot contain self edges")
        if previous is not None and previous >= edge:
            raise ValueError("graph edges must be strictly sorted and unique")
        previous = edge
    return edges


def _finishing_order(adjacency: tuple[tuple[int, ...], ...]) -> tuple[int, ...]:
    visited = bytearray(len(adjacency))
    finished: list[int] = []
    for start in range(len(adjacency)):
        if visited[start]:
            continue
        visited[start] = 1
        stack: list[tuple[int, int]] = [(start, 0)]
        while stack:
            node, position = stack[-1]
            if position == len(adjacency[node]):
                stack.pop()
                finished.append(node)
                continue
            successor = adjacency[node][position]
            stack[-1] = (node, position + 1)
            if visited[successor]:
                continue
            visited[successor] = 1
            stack.append((successor, 0))
    return tuple(finished)


def _strong_components(
    adjacency: tuple[tuple[int, ...], ...],
    reverse: tuple[tuple[int, ...], ...],
) -> tuple[tuple[int, ...], ...]:
    finished = _finishing_order(adjacency)
    assigned = bytearray(len(adjacency))
    components: list[tuple[int, ...]] = []
    for start in reversed(finished):
        if assigned[start]:
            continue
        assigned[start] = 1
        component: list[int] = []
        pending = [start]
        while pending:
            node = pending.pop()
            component.append(node)
            for predecessor in reversed(reverse[node]):
                if assigned[predecessor]:
                    continue
                assigned[predecessor] = 1
                pending.append(predecessor)
        components.append(tuple(sorted(component)))
    return tuple(components)


def _topological_order(
    adjacency: tuple[tuple[int, ...], ...],
    indegrees: list[int],
) -> tuple[int, ...]:
    ready: list[int] = []
    for node, indegree in enumerate(indegrees):
        if indegree == 0:
            heappush(ready, node)
    ordered: list[int] = []
    while ready:
        node = heappop(ready)
        ordered.append(node)
        for successor in adjacency[node]:
            indegrees[successor] -= 1
            if indegrees[successor] == 0:
                heappush(ready, successor)
    if len(ordered) != len(adjacency):
        raise ValueError("transitive reduction requires an acyclic graph")
    return tuple(ordered)


__all__ = ["ReducedGraph", "quotient_and_reduce", "transitive_reduction"]
