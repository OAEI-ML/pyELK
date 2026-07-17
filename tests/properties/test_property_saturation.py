from __future__ import annotations

import os
from dataclasses import dataclass

from hypothesis import given, settings
from hypothesis import strategies as st

from pyelk.indexing.compiler import compile_ontology
from pyelk.indexing.ir import EntityId, EntityKind, PropertyChainId
from pyelk.reasoning.properties import (
    PropertyComposition,
    PropertyRange,
    PropertySaturation,
    SubPropertyChain,
    saturate_properties,
)
from tests.unit.indexing._support import load_functional

_EXAMPLES = int(os.environ.get("PYELK_PROPERTY_EXAMPLES", "250"))


@dataclass(frozen=True, slots=True)
class _Case:
    property_count: int
    edges: frozenset[tuple[int, int]]
    chain_axioms: frozenset[tuple[tuple[int, ...], int]]
    ranges: frozenset[tuple[int, int]]
    reflexive: frozenset[int]

    def axioms(self) -> tuple[str, ...]:
        values = [
            *(f"Declaration(ObjectProperty(:p{index}))" for index in range(self.property_count)),
            *(f"SubObjectPropertyOf(:p{sub} :p{super_})" for sub, super_ in sorted(self.edges)),
            *(
                "SubObjectPropertyOf(ObjectPropertyChain("
                + " ".join(f":p{item}" for item in chain)
                + f") :p{super_})"
                for chain, super_ in sorted(self.chain_axioms)
            ),
            *(
                f"ObjectPropertyRange(:p{property_index} :C{class_index})"
                for property_index, class_index in sorted(self.ranges)
            ),
            *(f"ReflexiveObjectProperty(:p{index})" for index in sorted(self.reflexive)),
        ]
        return tuple(values)


@st.composite
def _cases(draw: st.DrawFn) -> _Case:
    property_count = draw(st.integers(min_value=1, max_value=5))
    property_index = st.integers(min_value=0, max_value=property_count - 1)
    edges = draw(st.frozensets(st.tuples(property_index, property_index), max_size=8))
    chain = st.lists(property_index, min_size=2, max_size=3).map(tuple)
    chain_axioms = draw(st.frozensets(st.tuples(chain, property_index), max_size=5))
    ranges = draw(
        st.frozensets(
            st.tuples(property_index, st.integers(min_value=0, max_value=2)),
            max_size=5,
        )
    )
    reflexive = draw(st.frozensets(property_index, max_size=property_count))
    return _Case(property_count, edges, chain_axioms, ranges, reflexive)


def _entity_ids(compiled: object) -> dict[str, EntityId]:
    return {
        record.iri.rsplit("#", 1)[-1]: EntityId(index)
        for index, record in enumerate(compiled.entities)  # type: ignore[attr-defined]
        if record.kind is EntityKind.OBJECT_PROPERTY and record.iri.startswith("urn:test#p")
    }


def _exhaustive(
    compiled: object,
    saturated: PropertySaturation,
) -> tuple[
    set[SubPropertyChain],
    set[PropertyRange],
    set[PropertyComposition],
    set[PropertyComposition],
]:
    relation = {
        SubPropertyChain(PropertyChainId(index), PropertyChainId(index))
        for index in range(saturated.chain_count)
    }
    told: set[tuple[PropertyChainId, PropertyChainId]] = set()
    for compiled_sub, super_property in compiled.subproperty_axioms:  # type: ignore[attr-defined]
        pair = (
            saturated.compiled_chain(compiled_sub),
            saturated.singleton_chain(super_property),
        )
        told.add(pair)
        relation.add(SubPropertyChain(*pair))
    changed = True
    while changed:
        changed = False
        additions = {
            SubPropertyChain(told_sub, known.super_chain)
            for told_sub, told_super in told
            for known in relation
            if known.sub_chain == told_super
        } - relation
        if additions:
            relation.update(additions)
            changed = True

    ranges: set[PropertyRange] = set()
    singleton_to_property = {
        saturated.singleton_chain(EntityId(entity_index)): EntityId(entity_index)
        for entity_index, entity in enumerate(compiled.entities)  # type: ignore[attr-defined]
        if entity.kind is EntityKind.OBJECT_PROPERTY
    }
    for super_property, range_expression in compiled.property_ranges:  # type: ignore[attr-defined]
        super_chain = saturated.singleton_chain(super_property)
        for premise in relation:
            if premise.super_chain != super_chain:
                continue
            sub_property = singleton_to_property.get(premise.sub_chain)
            if sub_property is not None:
                ranges.add(PropertyRange(sub_property, range_expression))

    records = saturated.chains

    def subchains(super_chain: PropertyChainId) -> set[PropertyChainId]:
        return {
            conclusion.sub_chain for conclusion in relation if conclusion.super_chain == super_chain
        }

    def named_subproperties(chain: PropertyChainId) -> set[EntityId]:
        return {
            records[sub_chain].first_property
            for sub_chain in subchains(chain)
            if records[sub_chain].is_singleton
        }

    def left_subcomposable(property_id: EntityId) -> dict[EntityId, set[EntityId]]:
        property_chain = saturated.singleton_chain(property_id)
        property_subs = named_subproperties(property_chain)
        result: dict[EntityId, set[EntityId]] = {}
        for complex_sub in subchains(property_chain):
            record = records[complex_sub]
            if record.suffix_chain is None:
                continue
            shared = property_subs & named_subproperties(
                saturated.singleton_chain(record.first_property)
            )
            for right_property in named_subproperties(record.suffix_chain):
                result.setdefault(right_property, set()).update(shared)
        return result

    non_redundant: set[PropertyComposition] = set()
    redundant: set[PropertyComposition] = set()
    for result_id, result_record in enumerate(records):
        suffix = result_record.suffix_chain
        if suffix is None:
            continue
        first_chain = saturated.singleton_chain(result_record.first_property)
        left_candidates = named_subproperties(first_chain)
        right_candidates = subchains(suffix)
        for right_chain in right_candidates:
            if first_chain == suffix and right_chain == result_id:
                continue
            redundant_left: set[EntityId] = set()
            right_record = records[right_chain]
            if (
                right_record.suffix_chain is not None
                and right_record.suffix_chain in right_candidates
            ):
                redundant_left = left_subcomposable(result_record.first_property).get(
                    right_record.first_property,
                    set(),
                )
            for left_property in left_candidates:
                conclusion = PropertyComposition(
                    left_property,
                    right_chain,
                    PropertyChainId(result_id),
                )
                if left_property in redundant_left:
                    redundant.add(conclusion)
                else:
                    non_redundant.add(conclusion)
    non_redundant.difference_update(redundant)
    return relation, ranges, non_redundant, redundant


@settings(max_examples=_EXAMPLES, deadline=None)
@given(case=_cases())
def test_semi_naive_property_saturation_equals_exhaustive_fixed_point(case: _Case) -> None:
    axioms = case.axioms()
    compiled = compile_ontology(
        load_functional(" ".join(axioms), ontology_iri="urn:property-generated")
    )
    saturated = saturate_properties(compiled)
    expected_relation, expected_ranges, expected_non_redundant, expected_redundant = _exhaustive(
        compiled,
        saturated,
    )
    assert set(saturated.subproperty_chains) == expected_relation
    assert set(saturated.property_ranges) == expected_ranges
    assert set(saturated.non_redundant_compositions) == expected_non_redundant
    assert set(saturated.redundant_compositions) == expected_redundant

    permuted = compile_ontology(
        load_functional(" ".join(reversed(axioms)), ontology_iri="urn:property-generated")
    )
    assert saturate_properties(permuted) == saturated
    assert saturate_properties(compiled) == saturated

    properties = _entity_ids(compiled)
    assert saturated.reflexive_properties == tuple(
        sorted(properties[f"p{index}"] for index in case.reflexive)
    )
