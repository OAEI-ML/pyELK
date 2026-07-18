from __future__ import annotations

from dataclasses import replace
from itertools import permutations

import pytest

from pyelk.indexing.compiler import compile_ontology
from pyelk.indexing.ir import (
    CompiledOntology,
    DisjointGroupId,
    EntityId,
    ExpressionId,
    ExpressionTag,
)
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
    conclusion_destination,
)
from pyelk.reasoning.contexts import ContextState
from pyelk.reasoning.properties import PropertySaturation, saturate_properties
from pyelk.reasoning.rules import RULE_CLASS_TRIGGERS, RuleDispatcher
from tests.unit.indexing._support import entity_id, load_functional


class _Collector:
    def __init__(self) -> None:
        self.values: list[Conclusion] = []

    def produce(self, conclusion: Conclusion) -> None:
        self.values.append(conclusion)


def _compiled(body: str) -> tuple[CompiledOntology, PropertySaturation, RuleDispatcher]:
    compiled = compile_ontology(load_functional(body, ontology_iri="urn:dispatcher"))
    properties = saturate_properties(compiled)
    return compiled, properties, RuleDispatcher(compiled, properties)


def _entity(compiled: CompiledOntology, name: str) -> EntityId:
    return EntityId(entity_id(compiled, f"urn:test#{name}"))


def _class(compiled: CompiledOntology, name: str) -> ExpressionId:
    class_entity = _entity(compiled, name)
    return ExpressionId(
        next(
            index
            for index, record in enumerate(compiled.expressions)
            if record.tag is ExpressionTag.CLASS and record.arguments == (class_entity,)
        )
    )


def _expression(
    compiled: CompiledOntology,
    tag: ExpressionTag,
    arguments: tuple[int, ...],
) -> ExpressionId:
    return ExpressionId(
        next(
            index
            for index, record in enumerate(compiled.expressions)
            if record.tag is tag and record.arguments == arguments
        )
    )


def _dispatch(
    dispatcher: RuleDispatcher,
    state: ContextState,
    premise: Conclusion,
) -> list[Conclusion]:
    assert state.insert(premise)
    collector = _Collector()
    dispatcher.dispatch(state, premise, collector)
    return collector.values


def _last_arrival_outputs(
    dispatcher: RuleDispatcher,
    premises: tuple[Conclusion, Conclusion],
) -> tuple[set[Conclusion], set[Conclusion]]:
    outputs: list[set[Conclusion]] = []
    for order in permutations(premises):
        state = ContextState(conclusion_destination(order[0]))
        collector = _Collector()
        for premise in order:
            assert state.insert(premise)
            dispatcher.dispatch(state, premise, collector)
        outputs.append(set(collector.values))
    return outputs[0], outputs[1]


def test_dispatch_surface_classifies_all_pinned_non_incremental_rules() -> None:
    assert len(RULE_CLASS_TRIGGERS) == 30
    assert set(RULE_CLASS_TRIGGERS.values()) == {
        "backward_link",
        "class_inconsistency",
        "composed_subsumer",
        "context_initialization",
        "decomposed_subsumer",
        "disjoint_subsumer",
        "forward_link",
        "propagation",
        "subcontext_initialization",
    }


def test_dispatch_requires_an_already_stored_premise_and_skips_duplicates() -> None:
    compiled, _properties, dispatcher = _compiled("Declaration(Class(:A))")
    root = _class(compiled, "A")
    premise = ContextInitialization(root)
    state = ContextState(root)
    with pytest.raises(ValueError, match="already be stored"):
        dispatcher.dispatch(state, premise, _Collector())
    assert state.insert(premise)
    assert not state.insert(premise)


def test_initialization_subclass_definitions_and_occurrence_linked_decomposition() -> None:
    compiled, _properties, dispatcher = _compiled(
        "SubClassOf(:A :B) "
        "EquivalentClasses(:Defined ObjectIntersectionOf(:B :C)) "
        "SubClassOf(:A ObjectIntersectionOf(:B :C)) "
        "ReflexiveObjectProperty(:p)"
    )
    root = _class(compiled, "A")
    class_b = _class(compiled, "B")
    class_c = _class(compiled, "C")
    defined = _class(compiled, "Defined")
    conjunction = _expression(
        compiled,
        ExpressionTag.OBJECT_INTERSECTION_OF,
        (class_b, class_c),
    )
    state = ContextState(root)
    initialized = set(_dispatch(dispatcher, state, ContextInitialization(root)))
    assert SubClassInclusionDecomposed(root, root) in initialized
    assert SubClassInclusionComposed(root, dispatcher.owl_thing) in initialized

    subclass_products = set(_dispatch(dispatcher, state, SubClassInclusionComposed(root, root)))
    assert SubClassInclusionDecomposed(root, class_b) in subclass_products
    definition_products = set(
        _dispatch(dispatcher, state, SubClassInclusionDecomposed(root, defined))
    )
    assert SubClassInclusionDecomposed(root, conjunction) in definition_products
    conjunction_products = set(
        _dispatch(dispatcher, state, SubClassInclusionDecomposed(root, conjunction))
    )
    assert {
        SubClassInclusionDecomposed(root, class_b),
        SubClassInclusionDecomposed(root, class_c),
    } <= conjunction_products


def test_intersection_complement_and_disjoint_joins_cover_both_arrival_orders() -> None:
    compiled, properties, _dispatcher = _compiled(
        "SubClassOf(ObjectIntersectionOf(:A :B) :C) SubClassOf(:C ObjectComplementOf(:A))"
    )
    root = _class(compiled, "C")
    class_a = _class(compiled, "A")
    class_b = _class(compiled, "B")
    compiled = replace(compiled, disjoint_groups=((class_a, class_b, class_a),))
    dispatcher = RuleDispatcher(compiled, properties)
    intersection = _expression(
        compiled,
        ExpressionTag.OBJECT_INTERSECTION_OF,
        (class_a, class_b),
    )
    complement = _expression(
        compiled,
        ExpressionTag.OBJECT_COMPLEMENT_OF,
        (class_a,),
    )

    first, second = _last_arrival_outputs(
        dispatcher,
        (
            SubClassInclusionComposed(root, class_a),
            SubClassInclusionComposed(root, class_b),
        ),
    )
    expected_intersection = SubClassInclusionComposed(root, intersection)
    assert expected_intersection in first
    assert expected_intersection in second

    first, second = _last_arrival_outputs(
        dispatcher,
        (
            SubClassInclusionComposed(root, class_a),
            SubClassInclusionDecomposed(root, complement),
        ),
    )
    assert ClassInconsistency(root) in first
    assert ClassInconsistency(root) in second

    group = DisjointGroupId(0)
    first, second = _last_arrival_outputs(
        dispatcher,
        (DisjointSubsumer(root, group, 0), DisjointSubsumer(root, group, 2)),
    )
    assert ClassInconsistency(root) in first
    assert ClassInconsistency(root) in second


def test_union_and_duplicate_disjoint_member_positions_are_not_lost() -> None:
    compiled, properties, _dispatcher = _compiled("SubClassOf(ObjectUnionOf(:A :B) :C)")
    root = _class(compiled, "C")
    class_a = _class(compiled, "A")
    class_b = _class(compiled, "B")
    union = _expression(
        compiled,
        ExpressionTag.OBJECT_UNION_OF,
        (class_a, class_b),
    )
    compiled = replace(compiled, disjoint_groups=((class_a, class_b, class_a),))
    dispatcher = RuleDispatcher(compiled, properties)
    state = ContextState(root)
    products = set(_dispatch(dispatcher, state, SubClassInclusionComposed(root, class_a)))
    assert SubClassInclusionComposed(root, union) in products
    positions = {value.position for value in products if isinstance(value, DisjointSubsumer)}
    assert positions == {0, 2}


def test_owl_nothing_rule_is_occurrence_linked() -> None:
    compiled, _properties, dispatcher = _compiled("SubClassOf(:A owl:Nothing)")
    root = _class(compiled, "A")
    state = ContextState(root)
    products = set(
        _dispatch(
            dispatcher,
            state,
            SubClassInclusionDecomposed(root, dispatcher.owl_nothing),
        )
    )
    assert ClassInconsistency(root) in products

    empty, _empty_properties, empty_dispatcher = _compiled("Declaration(Class(:A))")
    empty_root = _class(empty, "A")
    state = ContextState(empty_root)
    products = set(
        _dispatch(
            empty_dispatcher,
            state,
            SubClassInclusionDecomposed(empty_root, empty_dispatcher.owl_nothing),
        )
    )
    assert ClassInconsistency(empty_root) not in products


def test_propagation_joins_are_complete_for_filler_subcontext_and_backward_orders() -> None:
    compiled, _properties, dispatcher = _compiled(
        "SubClassOf(ObjectSomeValuesFrom(:p :B) :C) "
        "SubObjectPropertyOf(:q :p) Declaration(Class(:Root))"
    )
    root = _class(compiled, "Root")
    filler = _class(compiled, "B")
    relation = _entity(compiled, "q")
    carry = _expression(
        compiled,
        ExpressionTag.OBJECT_SOME_VALUES_FROM,
        (_entity(compiled, "p"), filler),
    )
    first, second = _last_arrival_outputs(
        dispatcher,
        (
            SubClassInclusionComposed(root, filler),
            SubContextInitialization(root, relation),
        ),
    )
    expected_propagation = Propagation(root, relation, carry)
    assert expected_propagation in first
    assert expected_propagation in second

    source = _class(compiled, "C")
    first, second = _last_arrival_outputs(
        dispatcher,
        (BackwardLink(root, relation, source), expected_propagation),
    )
    expected_carry = SubClassInclusionComposed(source, carry)
    assert expected_carry in first
    assert expected_carry in second


def test_existential_self_ranges_and_inconsistency_propagation() -> None:
    compiled, _properties, dispatcher = _compiled(
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B)) "
        "SubClassOf(:A ObjectHasSelf(:p)) ObjectPropertyRange(:p :C)"
    )
    root = _class(compiled, "A")
    target = _class(compiled, "B")
    range_expression = _class(compiled, "C")
    relation = _entity(compiled, "p")
    existential = _expression(
        compiled,
        ExpressionTag.OBJECT_SOME_VALUES_FROM,
        (relation, target),
    )
    has_self = _expression(
        compiled,
        ExpressionTag.OBJECT_HAS_SELF,
        (relation,),
    )
    state = ContextState(root)
    existential_products = set(
        _dispatch(dispatcher, state, SubClassInclusionDecomposed(root, existential))
    )
    assert BackwardLink(target, relation, root) in existential_products

    self_products = set(_dispatch(dispatcher, state, SubClassInclusionDecomposed(root, has_self)))
    assert BackwardLink(root, relation, root) in self_products
    assert SubClassInclusionDecomposed(root, range_expression) in self_products

    target_state = ContextState(target)
    backward = BackwardLink(target, relation, root)
    backward_products = set(_dispatch(dispatcher, target_state, backward))
    assert SubContextInitialization(target, relation) in backward_products
    assert SubClassInclusionDecomposed(target, range_expression) in backward_products
    first, second = _last_arrival_outputs(
        dispatcher,
        (backward, ClassInconsistency(target)),
    )
    assert ClassInconsistency(root) in first
    assert ClassInconsistency(root) in second


def test_three_property_composition_uses_wp5_local_suffix_ids_in_both_orders() -> None:
    compiled, properties, dispatcher = _compiled(
        "SubObjectPropertyOf(ObjectPropertyChain(:p :q :r) :s) "
        "Declaration(Class(:Source)) Declaration(Class(:Middle)) Declaration(Class(:Target))"
    )
    source = _class(compiled, "Source")
    middle = _class(compiled, "Middle")
    target = _class(compiled, "Target")
    property_p = _entity(compiled, "p")
    property_q = _entity(compiled, "q")
    property_r = _entity(compiled, "r")
    property_s = _entity(compiled, "s")
    suffix = properties.lookup_chain((property_q, property_r))
    full = properties.lookup_chain((property_p, property_q, property_r))
    assert suffix is not None and full is not None
    assert suffix not in properties.compiled_chain_ids

    first, second = _last_arrival_outputs(
        dispatcher,
        (
            BackwardLink(middle, property_p, source),
            ForwardLink(middle, suffix, target),
        ),
    )
    expected = BackwardLink(target, property_s, source)
    assert expected in first
    assert expected in second

    state = ContextState(source)
    products = set(_dispatch(dispatcher, state, ForwardLink(source, full, target)))
    assert BackwardLink(target, property_s, source) in products


def test_composition_produces_an_extendable_forward_link() -> None:
    compiled, properties, dispatcher = _compiled(
        "SubObjectPropertyOf(ObjectPropertyChain(:p :q :r) :s) "
        "SubClassOf(:Source ObjectSomeValuesFrom(:r :Target)) "
        "SubClassOf(:Source ObjectSomeValuesFrom(:p :Target)) "
        "Declaration(Class(:Source)) Declaration(Class(:Middle)) Declaration(Class(:Target))"
    )
    source = _class(compiled, "Source")
    middle = _class(compiled, "Middle")
    target = _class(compiled, "Target")
    property_q = _entity(compiled, "q")
    property_r = _entity(compiled, "r")
    suffix = properties.lookup_chain((property_q, property_r))
    assert suffix is not None
    singleton_r = properties.singleton_chain(property_r)
    first, second = _last_arrival_outputs(
        dispatcher,
        (
            BackwardLink(middle, property_q, source),
            ForwardLink(middle, singleton_r, target),
        ),
    )
    expected = ForwardLink(source, suffix, target)
    assert expected in first
    assert expected in second

    existential = _expression(
        compiled,
        ExpressionTag.OBJECT_SOME_VALUES_FROM,
        (property_r, target),
    )
    state = ContextState(source)
    products = set(_dispatch(dispatcher, state, SubClassInclusionDecomposed(source, existential)))
    assert ForwardLink(source, singleton_r, target) in products

    property_p = _entity(compiled, "p")
    left_only_existential = _expression(
        compiled,
        ExpressionTag.OBJECT_SOME_VALUES_FROM,
        (property_p, target),
    )
    state = ContextState(source)
    products = set(
        _dispatch(
            dispatcher,
            state,
            SubClassInclusionDecomposed(source, left_only_existential),
        )
    )
    assert not any(isinstance(value, ForwardLink) for value in products)
