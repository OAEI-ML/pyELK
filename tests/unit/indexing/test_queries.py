from __future__ import annotations

from collections.abc import Callable

import pyowl_core as owl
import pytest

from pyelk.exceptions import UnsupportedQueryError
from pyelk.indexing.compiler import (
    compile_entailment_query,
    compile_ontology,
    compile_query_expression,
    symbol_table,
)
from pyelk.indexing.conversion import FEATURE_INDEX
from pyelk.indexing.ir import (
    CompiledOntology,
    EntityId,
    EntityKind,
    EntityRecord,
    QueryIR,
    QueryIRKind,
)

from ._support import load_functional

_A = owl.Class(owl.IRI("urn:query#A"))
_B = owl.Class(owl.IRI("urn:query#B"))
_C = owl.Class(owl.IRI("urn:query#C"))
_P = owl.ObjectProperty(owl.IRI("urn:query#p"))
_I = owl.NamedIndividual(owl.IRI("urn:query#i"))
_J = owl.NamedIndividual(owl.IRI("urn:query#j"))
_K = owl.NamedIndividual(owl.IRI("urn:query#k"))


def _empty_symbols() -> CompiledOntology:
    return compile_ontology(load_functional("", ontology_iri="urn:query-empty"))


def _feature_counts(compiled: object) -> dict[int, int]:
    values = compiled.feature_counts  # type: ignore[attr-defined]
    return {index: count for index, count in enumerate(values) if count}


def _unsafe_equivalent_classes(
    *members: owl.ClassExpression,
) -> owl.EquivalentClasses:
    value = object.__new__(owl.EquivalentClasses)
    object.__setattr__(value, "expressions", members)
    object.__setattr__(value, "annotations", owl.CanonicalSet())
    return value


def _unsafe_same_individual(*members: owl.Individual) -> owl.SameIndividual:
    value = object.__new__(owl.SameIndividual)
    object.__setattr__(value, "individuals", members)
    object.__setattr__(value, "annotations", owl.CanonicalSet())
    return value


def test_class_expression_query_is_self_contained_and_maps_existing_entities() -> None:
    ontology = compile_ontology(
        load_functional(
            "Declaration(Class(<urn:query#A>)) Declaration(ObjectProperty(<urn:query#p>))"
        )
    )
    expression = owl.ObjectIntersectionOf(owl.CanonicalSet((_A, owl.ObjectSomeValuesFrom(_P, _B))))
    compiled = compile_query_expression(expression, symbol_table(ontology))
    assert compiled.encoded is not None
    query = QueryIR.decode(compiled.encoded)
    assert query.kind is QueryIRKind.CLASS_EXPRESSION
    assert query.root_expression is not None
    assert query.subsumption_obligations == ()
    mapped = {record.entity.iri: record.ontology_id for record in query.entities}
    assert mapped["urn:query#A"] is not None
    assert mapped["urn:query#p"] is not None
    assert mapped["urn:query#B"] is None
    assert compiled.fresh_entities == (EntityRecord(EntityKind.CLASS, "urn:query#B"),)
    assert _feature_counts(compiled) == {}


def test_unindexable_class_query_rolls_back_but_enumerates_every_fresh_entity() -> None:
    expression = owl.ObjectIntersectionOf(
        owl.CanonicalSet(
            (
                _A,
                owl.ObjectAllValuesFrom(
                    owl.ObjectProperty(owl.IRI("urn:query#ghost-property")),
                    owl.Class(owl.IRI("urn:query#Ghost")),
                ),
            )
        )
    )
    compiled = compile_query_expression(expression, _empty_symbols())
    assert compiled.encoded is None
    assert _feature_counts(compiled) == {FEATURE_INDEX["OBJECT_ALL_VALUES_FROM"]: 1}
    assert {record.iri for record in compiled.fresh_entities} == {
        "urn:query#A",
        "urn:query#Ghost",
        "urn:query#ghost-property",
    }
    with pytest.raises(UnsupportedQueryError, match="OBJECT_ALL_VALUES_FROM") as caught:
        compile_query_expression(expression, _empty_symbols(), unsupported="error")
    assert caught.value.query is expression


QueryFactory = Callable[[], owl.StructuralNode]


@pytest.mark.parametrize(
    ("factory", "obligation_count", "features"),
    (
        (lambda: owl.SubClassOf(_A, _B), 1, {}),
        (lambda: owl.ClassAssertion(_A, _I), 1, {}),
        (
            lambda: owl.ObjectPropertyAssertion(_P, _I, _J),
            1,
            {FEATURE_INDEX["OBJECT_ONE_OF"]: 1},
        ),
        (lambda: owl.ObjectPropertyDomain(_P, _A), 1, {}),
        (
            lambda: owl.DisjointClasses(owl.CanonicalSet((_A, _B, _C))),
            3,
            {},
        ),
        (
            lambda: owl.DifferentIndividuals(owl.CanonicalSet((_I, _J, _K))),
            3,
            {FEATURE_INDEX["OBJECT_ONE_OF"]: 6},
        ),
        (
            lambda: owl.EquivalentClasses(owl.CanonicalSet((_A, _B, _C))),
            3,
            {},
        ),
        (
            lambda: owl.SameIndividual(owl.CanonicalSet((_I, _J, _K))),
            3,
            {FEATURE_INDEX["OBJECT_ONE_OF"]: 6},
        ),
    ),
)
def test_all_eight_supported_entailment_families(
    factory: QueryFactory,
    obligation_count: int,
    features: dict[int, int],
) -> None:
    compiled = compile_entailment_query(factory(), _empty_symbols())
    assert compiled.encoded is not None
    query = QueryIR.decode(compiled.encoded)
    assert query.kind is QueryIRKind.ENTAILMENT
    assert query.root_expression is None
    assert len(query.subsumption_obligations) == obligation_count
    assert _feature_counts(compiled) == features
    assert compiled.fresh_entities


def test_zero_member_query_lists_are_rejected_and_singletons_follow_pinned_cycle() -> None:
    with pytest.raises(ValueError, match="EquivalentClasses"):
        compile_entailment_query(_unsafe_equivalent_classes(), _empty_symbols())
    with pytest.raises(ValueError, match="SameIndividual"):
        compile_entailment_query(_unsafe_same_individual(), _empty_symbols())

    equivalent = compile_entailment_query(_unsafe_equivalent_classes(_A), _empty_symbols())
    assert equivalent.encoded is not None
    assert len(QueryIR.decode(equivalent.encoded).subsumption_obligations) == 1

    same = compile_entailment_query(_unsafe_same_individual(_I), _empty_symbols())
    assert same.encoded is not None
    assert len(QueryIR.decode(same.encoded).subsumption_obligations) == 1
    assert _feature_counts(same) == {FEATURE_INDEX["OBJECT_ONE_OF"]: 2}


@pytest.mark.parametrize(
    ("query", "normalized_iri"),
    (
        (owl.ObjectPropertyDomain(_P, _A), owl.OWL_THING.iri.value),
        (owl.DisjointClasses(owl.CanonicalSet((_A, _B))), owl.OWL_NOTHING.iri.value),
        (owl.DifferentIndividuals(owl.CanonicalSet((_I, _J))), owl.OWL_NOTHING.iri.value),
    ),
)
def test_entities_introduced_by_query_normalization_reuse_ontology_predefined_ids(
    query: owl.AxiomNode,
    normalized_iri: str,
) -> None:
    compiled = compile_entailment_query(query, _empty_symbols())
    assert compiled.encoded is not None
    assert normalized_iri not in {record.iri for record in compiled.fresh_entities}
    decoded = QueryIR.decode(compiled.encoded)
    normalized = next(row for row in decoded.entities if row.entity.iri == normalized_iri)
    assert normalized.ontology_id is not None


def test_unsupported_query_family_short_circuits_nested_ontology_features() -> None:
    query = owl.Declaration(owl.DataProperty(owl.IRI("urn:query#data")))
    compiled = compile_entailment_query(query, _empty_symbols())
    assert compiled.encoded is None
    assert _feature_counts(compiled) == {FEATURE_INDEX["QUERY_DECLARATION_AXIOM"]: 1}
    assert compiled.fresh_entities == (EntityRecord(EntityKind.DATA_PROPERTY, "urn:query#data"),)
    with pytest.raises(UnsupportedQueryError) as caught:
        compile_entailment_query(query, _empty_symbols(), unsupported="error")
    assert caught.value.feature == "QUERY_DECLARATION_AXIOM"
    assert caught.value.query is query


def test_supported_entailment_with_unsupported_nested_expression_is_transactional() -> None:
    query = owl.SubClassOf(_A, owl.ObjectAllValuesFrom(_P, _B))
    compiled = compile_entailment_query(query, _empty_symbols())
    assert compiled.encoded is None
    assert _feature_counts(compiled) == {FEATURE_INDEX["OBJECT_ALL_VALUES_FROM"]: 1}
    assert {record.iri for record in compiled.fresh_entities} == {
        "urn:query#A",
        "urn:query#B",
        "urn:query#p",
    }


def test_supported_axiom_annotations_do_not_create_fresh_reasoning_entities() -> None:
    annotation = owl.Annotation(
        owl.AnnotationProperty(owl.IRI("urn:query#annotation")),
        owl.Literal("note", owl.RDF_PLAIN_LITERAL),
    )
    query = owl.SubClassOf(_A, _B, owl.CanonicalSet((annotation,)))
    compiled = compile_entailment_query(query, _empty_symbols())
    assert compiled.encoded is not None
    assert {record.iri for record in compiled.fresh_entities} == {
        "urn:query#A",
        "urn:query#B",
    }


class _InvalidSymbolTable:
    def __init__(self, entity_count: int, value: int | None) -> None:
        self.entity_count = entity_count
        self.value = value

    def lookup_entity(self, entity: EntityRecord) -> EntityId | None:
        return None if self.value is None else EntityId(self.value)


def test_external_symbol_table_ids_are_defensively_validated() -> None:
    with pytest.raises(ValueError, match="out-of-range"):
        compile_query_expression(_A, _InvalidSymbolTable(1, 1))
    with pytest.raises(ValueError, match="distinct entities"):
        compile_query_expression(
            owl.ObjectIntersectionOf(owl.CanonicalSet((_A, _B))),
            _InvalidSymbolTable(1, 0),
        )
    with pytest.raises(ValueError, match="entity_count"):
        compile_query_expression(_A, _InvalidSymbolTable(-1, None))


def test_query_expression_deep_nesting_is_iterative_and_ceiling_is_explicit() -> None:
    expression: owl.ClassExpression = _A
    for _ in range(1_500):
        expression = owl.ObjectSomeValuesFrom(_P, expression)
    compiled = compile_query_expression(expression, _empty_symbols(), max_nodes=1_501)
    assert compiled.encoded is not None
    assert len(QueryIR.decode(compiled.encoded).expressions) == 1_501
    with pytest.raises(ValueError, match="node safety ceiling"):
        compile_query_expression(expression, _empty_symbols(), max_nodes=100)


@pytest.mark.parametrize("unsupported", ("IGNORE", "", "strict"))
def test_invalid_unsupported_mode_is_rejected(unsupported: str) -> None:
    with pytest.raises(ValueError, match="unsupported"):
        compile_query_expression(_A, _empty_symbols(), unsupported=unsupported)  # type: ignore[arg-type]


def test_symbol_table_validates_record_lookup() -> None:
    table = symbol_table(_empty_symbols())
    with pytest.raises(TypeError, match="EntityRecord"):
        table.lookup_entity("urn:not-a-record")  # type: ignore[arg-type]
