from __future__ import annotations

from dataclasses import dataclass

import pyowl_core as owl
import pytest

from pyelk.exceptions import UnsupportedFeatureError
from pyelk.indexing.builder import IndexTransaction, OntologyBuilder
from pyelk.indexing.compiler import compile_ontology
from pyelk.indexing.conversion import (
    FEATURE_INDEX,
    AxiomConverter,
    ExpressionConverter,
    entity_record,
)
from pyelk.indexing.ir import EntityKind, EntityRecord, ExpressionTag
from pyelk.indexing.polarity import IndexPolarity

from ._support import entity_id, load_functional


def _features(compiled: object) -> dict[int, int]:
    counts = compiled.feature_counts  # type: ignore[attr-defined]
    return {index: count for index, count in enumerate(counts) if count}


def test_left_associated_intersection_union_and_polarity_rows() -> None:
    compiled = compile_ontology(
        load_functional("SubClassOf(ObjectIntersectionOf(:A :B :C) ObjectUnionOf(:D :E))")
    )
    assert len(compiled.subclass_axioms) == 1
    sub, sup = compiled.subclass_axioms[0]
    sub_record = compiled.expressions[sub]
    sup_record = compiled.expressions[sup]
    assert sub_record.tag is ExpressionTag.OBJECT_INTERSECTION_OF
    assert compiled.expressions[sub_record.arguments[0]].tag is ExpressionTag.OBJECT_INTERSECTION_OF
    assert sup_record.tag is ExpressionTag.OBJECT_UNION_OF
    assert len(sup_record.arguments) == 2
    assert compiled.expression_occurrences[sub].negative == 1
    assert compiled.expression_occurrences[sup].positive == 1
    assert _features(compiled) == {FEATURE_INDEX["OBJECT_UNION_OF_POSITIVE"]: 1}


def test_complement_switches_nested_polarity_and_tracks_both_outer_polarities() -> None:
    compiled = compile_ontology(
        load_functional(
            "SubClassOf("
            "ObjectComplementOf(ObjectHasSelf(:p)) "
            "ObjectComplementOf(ObjectUnionOf(:A :B)))"
        )
    )
    assert _features(compiled) == {
        FEATURE_INDEX["OBJECT_COMPLEMENT_OF_NEGATIVE"]: 1,
        FEATURE_INDEX["OBJECT_COMPLEMENT_OF_POSITIVE"]: 1,
    }
    complement_records = [
        (index, record)
        for index, record in enumerate(compiled.expressions)
        if record.tag is ExpressionTag.OBJECT_COMPLEMENT_OF
    ]
    assert len(complement_records) == 2
    nested_tags = {
        compiled.expressions[record.arguments[0]].tag: compiled.expression_occurrences[
            record.arguments[0]
        ]
        for _index, record in complement_records
    }
    assert nested_tags[ExpressionTag.OBJECT_HAS_SELF].positive == 1
    assert nested_tags[ExpressionTag.OBJECT_UNION_OF].negative == 1


def test_equivalence_prefers_later_named_class_as_definition() -> None:
    transaction = IndexTransaction()
    converter = AxiomConverter(transaction)
    a = owl.Class(owl.IRI("urn:A"))
    b = owl.Class(owl.IRI("urn:B"))
    defined = owl.Class(owl.IRI("urn:Defined"))
    converter._equivalent_classes((owl.ObjectIntersectionOf(owl.CanonicalSet((a, b))), defined))
    builder = OntologyBuilder()
    transaction.commit_into(builder)
    compiled = builder.freeze(b"x" * 32)
    assert len(compiled.equivalent_class_axioms) == 1
    first, second = compiled.equivalent_class_axioms[0]
    assert compiled.expressions[first].tag is ExpressionTag.CLASS
    assert compiled.entities[compiled.expressions[first].arguments[0]].iri == "urn:Defined"
    assert compiled.expressions[second].tag is ExpressionTag.OBJECT_INTERSECTION_OF


def test_binary_disjointness_matches_asymmetric_upstream_conversion_counts() -> None:
    compiled = compile_ontology(load_functional("DisjointClasses(:A :B)"))
    assert compiled.disjoint_groups == ()
    assert len(compiled.subclass_axioms) == 1
    conjunction, bottom = compiled.subclass_axioms[0]
    assert compiled.expressions[conjunction].tag is ExpressionTag.OBJECT_INTERSECTION_OF
    bottom_entity = compiled.entities[compiled.expressions[bottom].arguments[0]]
    assert bottom_entity.iri == owl.OWL_NOTHING.iri.value
    a_expression, b_expression = compiled.expressions[conjunction].arguments
    assert compiled.expression_occurrences[a_expression].negative == 1
    assert compiled.expression_occurrences[b_expression].negative == 2
    assert _features(compiled) == {
        FEATURE_INDEX["DISJOINT_CLASSES"]: 1,
        FEATURE_INDEX["OWL_NOTHING_POSITIVE"]: 1,
    }


def test_nary_and_duplicate_position_disjointness_are_retained() -> None:
    compiled = compile_ontology(load_functional("DisjointClasses(:A :B :C)"))
    assert len(compiled.disjoint_groups) == 1
    assert len(compiled.disjoint_groups[0]) == 3
    assert compiled.subclass_axioms == ()

    transaction = IndexTransaction()
    converter = AxiomConverter(transaction)
    duplicate = owl.Class(owl.IRI("urn:duplicate"))
    converter._disjoint((duplicate, duplicate))
    builder = OntologyBuilder()
    transaction.commit_into(builder)
    duplicate_compiled = builder.freeze(b"d" * 32)
    conjunction, _bottom = duplicate_compiled.subclass_axioms[0]
    arguments = duplicate_compiled.expressions[conjunction].arguments
    assert arguments[0] == arguments[1]


def test_zero_and_singleton_nary_axiom_internals_follow_pinned_visitation() -> None:
    member = owl.Class(owl.IRI("urn:singleton-axiom"))

    empty_disjoint = IndexTransaction()
    AxiomConverter(empty_disjoint)._disjoint(())
    assert len(empty_disjoint.expressions) == 1
    assert empty_disjoint.expressions[0].entities == (entity_record(owl.OWL_NOTHING),)
    assert empty_disjoint.expression_occurrences[0] == [0, 1]
    assert empty_disjoint.subclass_axioms == set()

    singleton_disjoint = IndexTransaction()
    AxiomConverter(singleton_disjoint)._disjoint((member,))
    assert len(singleton_disjoint.expressions) == 2
    assert singleton_disjoint.subclass_axioms == set()
    member_handle = next(
        index
        for index, record in enumerate(singleton_disjoint.expressions)
        if record.entities == (entity_record(member),)
    )
    assert singleton_disjoint.expression_occurrences[member_handle] == [1, 0]

    singleton_equivalent = IndexTransaction()
    AxiomConverter(singleton_equivalent)._equivalent_classes((member,))
    assert singleton_equivalent.equivalent_class_axioms == set()
    assert singleton_equivalent.expression_occurrences == [[1, 1]]

    singleton_same = IndexTransaction()
    individual = owl.NamedIndividual(owl.IRI("urn:singleton-same"))
    AxiomConverter(singleton_same)._same_individual((individual,))
    assert singleton_same.subclass_axioms == set()
    assert singleton_same.expression_occurrences == [[1, 1]]


def _unsafe_intersection(
    *members: owl.ClassExpression,
) -> owl.ObjectIntersectionOf:
    value = object.__new__(owl.ObjectIntersectionOf)
    object.__setattr__(value, "operands", members)
    return value


def _unsafe_union(*members: owl.ClassExpression) -> owl.ObjectUnionOf:
    value = object.__new__(owl.ObjectUnionOf)
    object.__setattr__(value, "operands", members)
    return value


def _unsafe_one_of(*members: owl.Individual) -> owl.ObjectOneOf:
    value = object.__new__(owl.ObjectOneOf)
    object.__setattr__(value, "individuals", members)
    return value


def test_zero_and_singleton_expression_simplifications_match_pinned_converter() -> None:
    cases = (
        (_unsafe_intersection(), owl.OWL_THING.iri.value, 0),
        (_unsafe_union(), owl.OWL_NOTHING.iri.value, 0),
        (_unsafe_one_of(), owl.OWL_NOTHING.iri.value, 0),
    )
    for expression, expected_iri, one_of_count in cases:
        transaction = IndexTransaction()
        root = ExpressionConverter(transaction).convert(expression, IndexPolarity.POSITIVE)
        record = transaction.expressions[root]
        assert record.tag is ExpressionTag.CLASS
        assert record.entities[0].iri == expected_iri
        assert transaction.feature_counts[FEATURE_INDEX["OBJECT_ONE_OF"]] == one_of_count

    class_member = owl.Class(owl.IRI("urn:singleton-class"))
    for expression in (_unsafe_intersection(class_member), _unsafe_union(class_member)):
        transaction = IndexTransaction()
        root = ExpressionConverter(transaction).convert(expression, IndexPolarity.POSITIVE)
        assert transaction.expressions[root].tag is ExpressionTag.CLASS
        assert transaction.expressions[root].entities == (entity_record(class_member),)

    individual = owl.NamedIndividual(owl.IRI("urn:singleton-individual"))
    transaction = IndexTransaction()
    root = ExpressionConverter(transaction).convert(
        _unsafe_one_of(individual), IndexPolarity.POSITIVE
    )
    assert transaction.expressions[root].tag is ExpressionTag.INDIVIDUAL
    assert transaction.feature_counts[FEATURE_INDEX["OBJECT_ONE_OF"]] == 1


def test_duplicate_expression_positions_survive_conversion() -> None:
    member = owl.Class(owl.IRI("urn:duplicate-expression"))
    for expression, polarity in (
        (_unsafe_intersection(member, member), IndexPolarity.NEGATIVE),
        (_unsafe_union(member, member), IndexPolarity.POSITIVE),
    ):
        transaction = IndexTransaction()
        root = ExpressionConverter(transaction).convert(expression, polarity)
        record = transaction.expressions[root]
        assert record.expressions[0] == record.expressions[1]
        assert transaction.expression_occurrences[record.expressions[0]] == [
            2 * polarity.negative,
            2 * polarity.positive,
        ]


@dataclass(frozen=True, slots=True)
class _UnsafeDisjointUnion:
    defined_class: owl.Class
    expressions: tuple[owl.ClassExpression, ...]


@pytest.mark.parametrize("member_count", (0, 1, 3))
def test_disjoint_union_zero_one_many_conversion_rows(member_count: int) -> None:
    transaction = IndexTransaction()
    converter = AxiomConverter(transaction)
    value = _UnsafeDisjointUnion(
        owl.Class(owl.IRI("urn:Defined")),
        tuple(owl.Class(owl.IRI(f"urn:M{index}")) for index in range(member_count)),
    )
    converter._disjoint_union(value)  # type: ignore[arg-type]
    builder = OntologyBuilder()
    transaction.commit_into(builder)
    compiled = builder.freeze(bytes([member_count + 1]) * 32)
    if member_count < 2:
        assert len(compiled.equivalent_class_axioms) == 1
        assert compiled.disjoint_groups == ()
    else:
        assert compiled.equivalent_class_axioms == ()
        assert len(compiled.disjoint_groups) == 1
        assert len(compiled.subclass_axioms) == member_count
        assert compiled.feature_counts[FEATURE_INDEX["DISJOINT_UNION"]] == 1


def test_property_axiom_conversion_tables_and_chain_counts() -> None:
    compiled = compile_ontology(
        load_functional(
            "EquivalentObjectProperties(:p :q :r) "
            "SubObjectPropertyOf(ObjectPropertyChain(:p :q :r) :s) "
            "ObjectPropertyDomain(:p :A) "
            "ObjectPropertyRange(:p :B) "
            "ReflexiveObjectProperty(:q) "
            "TransitiveObjectProperty(:r)"
        )
    )
    property_ids = {name: entity_id(compiled, f"urn:test#{name}") for name in "pqrs"}
    chains = {tuple(int(item) for item in chain) for chain in compiled.property_chains}
    assert (property_ids["p"], property_ids["q"], property_ids["r"]) in chains
    assert (property_ids["r"], property_ids["r"]) in chains
    assert len(compiled.subproperty_axioms) == 6
    assert compiled.property_ranges == ((property_ids["p"], compiled.property_ranges[0][1]),)
    assert len(compiled.subclass_axioms) == 2
    assert _features(compiled) == {
        FEATURE_INDEX["OBJECT_PROPERTY_CHAIN"]: 3,
        FEATURE_INDEX["OBJECT_PROPERTY_RANGE"]: 1,
        FEATURE_INDEX["REFLEXIVE_OBJECT_PROPERTY"]: 1,
    }


def test_individual_axiom_conversion_rows_and_entities() -> None:
    compiled = compile_ontology(
        load_functional(
            "ClassAssertion(:A :i) "
            "ObjectPropertyAssertion(:p :i :j) "
            "SameIndividual(:i :j :k) "
            "DifferentIndividuals(:i :j :k)"
        )
    )
    row_tags = [
        (compiled.expressions[sub].tag, compiled.expressions[sup].tag)
        for sub, sup in compiled.subclass_axioms
    ]
    assert row_tags.count((ExpressionTag.INDIVIDUAL, ExpressionTag.CLASS)) == 1
    assert row_tags.count((ExpressionTag.INDIVIDUAL, ExpressionTag.OBJECT_SOME_VALUES_FROM)) == 1
    assert row_tags.count((ExpressionTag.INDIVIDUAL, ExpressionTag.INDIVIDUAL)) == 4
    assert len(compiled.disjoint_groups) == 1
    assert len(compiled.disjoint_groups[0]) == 3
    assert all(
        compiled.expressions[member].tag is ExpressionTag.INDIVIDUAL
        for member in compiled.disjoint_groups[0]
    )
    assert {record.iri for record in compiled.entities} >= {
        "urn:test#A",
        "urn:test#i",
        "urn:test#j",
        "urn:test#k",
        "urn:test#p",
    }
    assert _features(compiled) == {
        FEATURE_INDEX["DIFFERENT_INDIVIDUALS"]: 1,
        FEATURE_INDEX["OBJECT_HAS_VALUE_POSITIVE"]: 1,
        FEATURE_INDEX["OBJECT_PROPERTY_ASSERTION"]: 1,
    }


def test_unsupported_nested_construct_rolls_back_every_ghost() -> None:
    view = load_functional("SubClassOf(ObjectIntersectionOf(:Ghost ObjectAllValuesFrom(:p :A)) :B)")
    compiled = compile_ontology(view)
    assert {record.iri for record in compiled.entities} == {
        owl.OWL_THING.iri.value,
        owl.OWL_NOTHING.iri.value,
        owl.OWL_TOP_OBJECT_PROPERTY.iri.value,
        owl.OWL_BOTTOM_OBJECT_PROPERTY.iri.value,
    }
    assert compiled.subclass_axioms == ()
    assert _features(compiled) == {FEATURE_INDEX["OBJECT_ALL_VALUES_FROM"]: 1}
    with pytest.raises(UnsupportedFeatureError, match="OBJECT_ALL_VALUES_FROM") as caught:
        compile_ontology(view, unsupported="error")
    assert caught.value.feature == "OBJECT_ALL_VALUES_FROM"


def test_annotation_axioms_and_axiom_annotations_do_not_change_logical_ir() -> None:
    plain = compile_ontology(
        load_functional(
            'AnnotationAssertion(rdfs:label :A "same-signature") SubClassOf(:A :B)',
            ontology_iri="urn:annotations",
        )
    )
    annotated = compile_ontology(
        load_functional(
            'AnnotationAssertion(rdfs:label :A "different text") '
            'SubClassOf(Annotation(rdfs:label "note") :A :B)',
            ontology_iri="urn:annotations",
        )
    )
    assert plain.entities == annotated.entities
    assert plain.expressions == annotated.expressions
    assert plain.subclass_axioms == annotated.subclass_axioms
    assert plain.feature_counts == annotated.feature_counts
    assert plain.source_fingerprint == annotated.source_fingerprint


def test_declarations_neutral_supported_and_exactly_rejected() -> None:
    compiled = compile_ontology(
        load_functional(
            "Declaration(Class(:A)) Declaration(ObjectProperty(:p)) "
            "Declaration(AnnotationProperty(:note)) Declaration(DataProperty(:data))"
        )
    )
    assert EntityRecord(EntityKind.CLASS, "urn:test#A") in compiled.entities
    assert EntityRecord(EntityKind.OBJECT_PROPERTY, "urn:test#p") in compiled.entities
    assert all(record.iri != "urn:test#note" for record in compiled.entities)
    assert all(record.iri != "urn:test#data" for record in compiled.entities)
    assert _features(compiled) == {FEATURE_INDEX["DATA_PROPERTY"]: 1}


def test_node_ceiling_rejects_deep_work_iteratively_without_partial_commit() -> None:
    expression: owl.ClassExpression = owl.Class(owl.IRI("urn:leaf"))
    prop = owl.ObjectProperty(owl.IRI("urn:p"))
    for _ in range(2_000):
        expression = owl.ObjectSomeValuesFrom(prop, expression)
    transaction = IndexTransaction()
    converter = AxiomConverter(transaction, node_limit=2_001)
    converter.convert(owl.SubClassOf(owl.Class(owl.IRI("urn:A")), expression))
    assert len(transaction.expressions) == 2_002
    with pytest.raises(ValueError, match="node safety ceiling"):
        AxiomConverter(IndexTransaction(), node_limit=50).convert(
            owl.SubClassOf(owl.Class(owl.IRI("urn:A")), expression)
        )


def test_polarity_value_contract() -> None:
    assert IndexPolarity.DUAL.negative == 1
    assert IndexPolarity.DUAL.positive == 1
    assert IndexPolarity.NEGATIVE.complementary() is IndexPolarity.POSITIVE
    assert IndexPolarity.DUAL.complementary() is IndexPolarity.DUAL
