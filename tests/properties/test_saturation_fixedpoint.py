from __future__ import annotations

import os
from dataclasses import dataclass

from hypothesis import given, settings
from hypothesis import strategies as st

from pyelk.indexing.compiler import compile_ontology
from pyelk.indexing.ir import (
    CompiledOntology,
    EntityId,
    ExpressionId,
    ExpressionTag,
    PropertyChainId,
)
from pyelk.reasoning.conclusions import (
    Conclusion,
    ContextInitialization,
    conclusion_destination,
    conclusion_key,
)
from pyelk.reasoning.contexts import ContextState
from pyelk.reasoning.properties import PropertySaturation, saturate_properties
from pyelk.reasoning.rules import RuleDispatcher
from pyelk.reasoning.saturation import SaturationEngine, SaturationSnapshot
from tests.unit.indexing._support import load_functional

_EXAMPLES = int(os.environ.get("PYELK_SATURATION_EXAMPLES", "80"))


@dataclass(frozen=True, slots=True)
class _Case:
    class_count: int
    subclass_edges: frozenset[tuple[int, int]]
    intersection_supers: frozenset[tuple[int, int, int]]
    intersection_subs: frozenset[tuple[int, int, int]]
    existentials: frozenset[tuple[int, int]]
    complements: frozenset[tuple[int, int]]
    disjoint_pairs: frozenset[tuple[int, int]]
    ranges: frozenset[int]

    def axioms(self) -> tuple[str, ...]:
        return (
            *(f"Declaration(Class(:C{index}))" for index in range(self.class_count)),
            "Declaration(ObjectProperty(:p))",
            *(f"SubClassOf(:C{sub} :C{super_})" for sub, super_ in sorted(self.subclass_edges)),
            *(
                f"SubClassOf(:C{sub} ObjectIntersectionOf(:C{first} :C{second}))"
                for sub, first, second in sorted(self.intersection_supers)
            ),
            *(
                f"SubClassOf(ObjectIntersectionOf(:C{first} :C{second}) :C{super_})"
                for first, second, super_ in sorted(self.intersection_subs)
            ),
            *(
                f"SubClassOf(:C{source} ObjectSomeValuesFrom(:p :C{target}))"
                for source, target in sorted(self.existentials)
            ),
            *(
                f"SubClassOf(:C{source} ObjectComplementOf(:C{target}))"
                for source, target in sorted(self.complements)
            ),
            *(
                f"DisjointClasses(:C{first} :C{second})"
                for first, second in sorted(self.disjoint_pairs)
            ),
            *(f"ObjectPropertyRange(:p :C{target})" for target in sorted(self.ranges)),
        )


@st.composite
def _cases(draw: st.DrawFn) -> _Case:
    class_count = draw(st.integers(min_value=1, max_value=5))
    index = st.integers(min_value=0, max_value=class_count - 1)
    pairs = st.tuples(index, index)
    intersection_supers = st.tuples(index, index, index).filter(lambda row: row[1] != row[2])
    intersection_subs = st.tuples(index, index, index).filter(lambda row: row[0] != row[1])
    disjoint = (
        frozenset()
        if class_count == 1
        else draw(st.frozensets(pairs.filter(lambda pair: pair[0] < pair[1]), max_size=3))
    )
    positive_intersections = (
        frozenset() if class_count == 1 else draw(st.frozensets(intersection_supers, max_size=4))
    )
    negative_intersections = (
        frozenset() if class_count == 1 else draw(st.frozensets(intersection_subs, max_size=4))
    )
    return _Case(
        class_count=class_count,
        subclass_edges=draw(st.frozensets(pairs, max_size=7)),
        intersection_supers=positive_intersections,
        intersection_subs=negative_intersections,
        existentials=draw(st.frozensets(pairs, max_size=4)),
        complements=draw(st.frozensets(pairs, max_size=3)),
        disjoint_pairs=disjoint,
        ranges=draw(st.frozensets(index, max_size=3)),
    )


class _Collector:
    def __init__(self) -> None:
        self.values: set[Conclusion] = set()

    def produce(self, conclusion: Conclusion, /) -> None:
        self.values.add(conclusion)


def _exhaustive_snapshot(
    compiled: CompiledOntology,
    properties: PropertySaturation,
    roots: tuple[ExpressionId, ...],
    *,
    reverse_contexts: bool,
    reverse_conclusions: bool,
    reverse_products: bool,
) -> SaturationSnapshot:
    """Deliberately rescan every stored premise until no insertion changes any context."""

    contexts: dict[ExpressionId, ContextState] = {}

    def ensure(root: ExpressionId) -> ContextState:
        state = contexts.get(root)
        if state is None:
            state = ContextState(root)
            contexts[root] = state
            assert state.insert(ContextInitialization(root))
        return state

    for root in roots:
        ensure(root)
    dispatcher = RuleDispatcher(compiled, properties)
    changed = True
    while changed:
        changed = False
        collector = _Collector()
        context_order = sorted(contexts, reverse=reverse_contexts)
        for root in context_order:
            premises = sorted(
                contexts[root].conclusions(),
                key=conclusion_key,
                reverse=reverse_conclusions,
            )
            for premise in premises:
                dispatcher.dispatch(contexts[root], premise, collector)
        products = sorted(
            collector.values,
            key=conclusion_key,
            reverse=reverse_products,
        )
        for product in products:
            destination = conclusion_destination(product)
            if destination not in contexts:
                ensure(destination)
                changed = True
            if contexts[destination].insert(product):
                changed = True

    for state in contexts.values():
        state.mark_saturated()
    return SaturationSnapshot(
        property_subsumers=tuple(
            properties.super_chains(PropertyChainId(chain))
            for chain in range(properties.chain_count)
        ),
        property_ranges=tuple(
            properties.ranges(EntityId(entity)) for entity in range(len(compiled.entities))
        ),
        contexts={root: contexts[root].freeze() for root in sorted(contexts)},
        inconsistent_ontology=False,
    )


@settings(max_examples=_EXAMPLES, deadline=None)
@given(data=st.data(), case=_cases())
def test_semi_naive_saturation_equals_exhaustive_under_agenda_permutations(
    data: st.DataObject,
    case: _Case,
) -> None:
    axioms = case.axioms()
    compiled = compile_ontology(
        load_functional(" ".join(axioms), ontology_iri="urn:saturation-generated")
    )
    properties = saturate_properties(compiled)
    roots = tuple(
        ExpressionId(index)
        for index, record in enumerate(compiled.expressions)
        if record.tag is ExpressionTag.CLASS
    )
    seed_permutation = data.draw(st.permutations(roots), label="seed-order")
    expected = _exhaustive_snapshot(
        compiled,
        properties,
        roots,
        reverse_contexts=False,
        reverse_conclusions=False,
        reverse_products=False,
    )
    for context_reverse, conclusion_reverse, product_reverse in (
        (True, False, False),
        (False, True, False),
        (False, False, True),
        (True, True, True),
    ):
        assert (
            _exhaustive_snapshot(
                compiled,
                properties,
                tuple(reversed(roots)),
                reverse_contexts=context_reverse,
                reverse_conclusions=conclusion_reverse,
                reverse_products=product_reverse,
            )
            == expected
        )

    engine = SaturationEngine(compiled, properties)
    actual = engine.run(seed_permutation)
    assert actual == expected
    diagnostics = engine.diagnostics()
    assert diagnostics.conclusions_inserted == sum(
        len(context.conclusions) for context in actual.contexts.values()
    )
    assert diagnostics.rule_dispatches == diagnostics.conclusions_inserted
    assert diagnostics.duplicate_insertions == 0

    reversed_axioms = compile_ontology(
        load_functional(
            " ".join(reversed(axioms)),
            ontology_iri="urn:saturation-generated",
        )
    )
    assert reversed_axioms == compiled
    assert SaturationEngine(reversed_axioms).run(reversed(roots)) == expected
