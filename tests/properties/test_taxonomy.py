from __future__ import annotations

import os

from hypothesis import given, settings
from hypothesis import strategies as st

from pyelk.indexing.compiler import compile_ontology
from pyelk.indexing.ir import (
    OWL_NOTHING_IRI,
    OWL_THING_IRI,
    EntityKind,
)
from pyelk.reasoning.reduction import ReducedGraph, quotient_and_reduce
from pyelk.reasoning.session import SaturationSession
from pyelk.reasoning.taxonomy import class_taxonomy
from tests.unit.indexing._support import load_functional

_EXAMPLES = int(os.environ.get("PYELK_TAXONOMY_EXAMPLES", "120"))


def _slow_quotient(
    members: tuple[int, ...],
    edges: frozenset[tuple[int, int]] | set[tuple[int, int]],
) -> ReducedGraph:
    """Cubic test oracle: closure, mutual quotient, then all-intermediate reduction."""

    reach = {member: {member} for member in members}
    for sub, super_ in edges:
        reach[sub].add(super_)
    changed = True
    while changed:
        changed = False
        for sub in members:
            expanded = set(reach[sub])
            for middle in tuple(reach[sub]):
                expanded.update(reach[middle])
            if not expanded <= reach[sub]:
                reach[sub].update(expanded)
                changed = True

    remaining = set(members)
    components: list[tuple[int, ...]] = []
    while remaining:
        representative = min(remaining)
        component = tuple(
            member
            for member in members
            if member in reach[representative] and representative in reach[member]
        )
        components.append(component)
        remaining.difference_update(component)
    nodes = tuple(sorted(components))
    node_of = {member: index for index, node in enumerate(nodes) for member in node}
    relation = {
        (node_of[sub], node_of[super_])
        for sub in members
        for super_ in reach[sub]
        if node_of[sub] != node_of[super_]
    }
    direct = {
        (sub, super_)
        for sub, super_ in relation
        if not any(
            middle not in {sub, super_}
            and (sub, middle) in relation
            and (middle, super_) in relation
            for middle in range(len(nodes))
        )
    }
    return ReducedGraph(nodes, tuple(sorted(direct)))


@st.composite
def _graphs(draw: st.DrawFn) -> tuple[tuple[int, ...], frozenset[tuple[int, int]]]:
    size = draw(st.integers(min_value=1, max_value=10))
    members = tuple(range(size))
    index = st.integers(min_value=0, max_value=size - 1)
    edges = draw(st.frozensets(st.tuples(index, index), max_size=min(45, size * size)))
    return members, edges


@settings(max_examples=_EXAMPLES, deadline=None)
@given(graph=_graphs())
def test_generated_preorders_match_cubic_quotient_and_reduction(
    graph: tuple[tuple[int, ...], frozenset[tuple[int, int]]],
) -> None:
    members, edges = graph
    expected = _slow_quotient(members, edges)
    assert quotient_and_reduce(members, edges) == expected
    assert quotient_and_reduce(reversed(members), reversed(tuple(edges))) == expected


@st.composite
def _taxonomy_cases(
    draw: st.DrawFn,
) -> tuple[
    int,
    frozenset[tuple[int, int]],
    frozenset[int],
    frozenset[int],
]:
    size = draw(st.integers(min_value=1, max_value=8))
    index = st.integers(min_value=0, max_value=size - 1)
    edges = draw(st.frozensets(st.tuples(index, index), max_size=min(30, size * size)))
    unsatisfiable = draw(st.frozensets(index, max_size=size))
    top_equivalent = draw(st.frozensets(index, max_size=size))
    return size, edges, unsatisfiable, top_equivalent


@settings(max_examples=_EXAMPLES, deadline=None)
@given(data=st.data(), case=_taxonomy_cases())
def test_generated_class_taxonomy_matches_slow_semantic_selector(
    data: st.DataObject,
    case: tuple[
        int,
        frozenset[tuple[int, int]],
        frozenset[int],
        frozenset[int],
    ],
) -> None:
    size, told_edges, unsatisfiable, top_equivalent = case
    axioms = (
        *(f"Declaration(Class(:C{index}))" for index in range(size)),
        *(f"SubClassOf(:C{sub} :C{super_})" for sub, super_ in sorted(told_edges) if sub != super_),
        *(f"SubClassOf(:C{index} owl:Nothing)" for index in sorted(unsatisfiable)),
        *(f"SubClassOf(owl:Thing :C{index})" for index in sorted(top_equivalent)),
    )
    permutation = data.draw(st.permutations(axioms), label="axiom-order")
    compiled = compile_ontology(
        load_functional(" ".join(permutation), ontology_iri="urn:taxonomy-generated")
    )
    actual = class_taxonomy(SaturationSession(compiled))
    members = tuple(
        index for index, entity in enumerate(compiled.entities) if entity.kind is EntityKind.CLASS
    )
    generated = {
        int(entity.iri.rsplit("C", 1)[1]): index
        for index, entity in enumerate(compiled.entities)
        if entity.kind is EntityKind.CLASS and entity.iri.startswith("urn:test#C")
    }
    top = next(index for index in members if compiled.entities[index].iri == OWL_THING_IRI)
    bottom = next(index for index in members if compiled.entities[index].iri == OWL_NOTHING_IRI)
    relation: set[tuple[int, int]] = {(bottom, member) for member in members}
    relation.update((member, top) for member in members)
    relation.update((generated[sub], generated[super_]) for sub, super_ in told_edges)
    relation.update((generated[index], bottom) for index in unsatisfiable)
    relation.update((top, generated[index]) for index in top_equivalent)
    expected = _slow_quotient(members, relation)
    assert actual.nodes == tuple(
        tuple(node_member for node_member in node) for node in expected.nodes
    )
    assert actual.direct_edges == expected.direct_edges
    assert actual.top == expected.node_for(top)
    assert actual.bottom == expected.node_for(bottom)
