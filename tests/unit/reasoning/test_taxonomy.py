from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pyowl_core as owl
import pytest

from pyelk.indexing.compiler import compile_ontology
from pyelk.indexing.ir import (
    CompiledOntology,
    EntityId,
    EntityKind,
    ExpressionId,
    ExpressionTag,
)
from pyelk.reasoning.contracts import RawTaxonomy
from pyelk.reasoning.session import SaturationSession, Stage
from pyelk.reasoning.taxonomy import (
    class_taxonomy,
    object_property_taxonomy,
    validate_taxonomy,
)
from tests.unit.indexing._support import entity_id, load_functional

_TESTS = Path(__file__).resolve().parents[2]
_DATA = _TESTS / "data" / "elk-v0.6.0"
_CLASS_EXPECTED = _DATA / "expected" / "classification"
_PROPERTY_EXPECTED = _CLASS_EXPECTED / "object_property"
_LOAD_OPTIONS = owl.LoadOptions(
    format=owl.DocumentFormat.FUNCTIONAL,
    imports=owl.ImportPolicy.IGNORE,
    backend=owl.BackendPreference.PYTHON,
)


def _compiled(body: str) -> CompiledOntology:
    return compile_ontology(load_functional(body, ontology_iri="urn:taxonomy"))


def _entity(compiled: CompiledOntology, name: str) -> EntityId:
    return EntityId(entity_id(compiled, f"urn:test#{name}"))


def _class_expression(compiled: CompiledOntology, name: str) -> ExpressionId:
    entity = _entity(compiled, name)
    return ExpressionId(
        next(
            index
            for index, record in enumerate(compiled.expressions)
            if record.tag is ExpressionTag.CLASS and record.arguments == (entity,)
        )
    )


def _node_iris(compiled: CompiledOntology, taxonomy: RawTaxonomy) -> tuple[tuple[str, ...], ...]:
    return tuple(
        tuple(compiled.entities[member].iri for member in node) for node in taxonomy.nodes
    )


def _edge_iris(
    compiled: CompiledOntology,
    taxonomy: RawTaxonomy,
) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    nodes = _node_iris(compiled, taxonomy)
    return {(nodes[sub], nodes[super_]) for sub, super_ in taxonomy.direct_edges}


def test_empty_and_declared_class_taxonomies_have_conventional_bottom_top_paths() -> None:
    empty = _compiled("")
    empty_taxonomy = class_taxonomy(SaturationSession(empty))
    empty_nodes = _node_iris(empty, empty_taxonomy)
    assert len(empty_nodes) == 2
    assert empty_taxonomy.direct_edges == ((empty_taxonomy.bottom, empty_taxonomy.top),)

    compiled = _compiled("Declaration(Class(:A)) Declaration(Class(:B))")
    taxonomy = class_taxonomy(SaturationSession(compiled))
    nodes = _node_iris(compiled, taxonomy)
    edge_values = _edge_iris(compiled, taxonomy)
    for name in ("A", "B"):
        node = (f"urn:test#{name}",)
        assert (nodes[taxonomy.bottom], node) in edge_values
        assert (node, nodes[taxonomy.top]) in edge_values


def test_class_cycles_diamonds_top_equivalence_and_unsatisfiability() -> None:
    compiled = _compiled(
        "EquivalentClasses(:A :B) SubClassOf(:A :C) SubClassOf(:A :D) "
        "SubClassOf(:C :E) SubClassOf(:D :E) EquivalentClasses(:E owl:Thing) "
        "SubClassOf(:Dead owl:Nothing)"
    )
    taxonomy = class_taxonomy(SaturationSession(compiled))
    nodes = _node_iris(compiled, taxonomy)
    class_a_node = next(
        index
        for index, node in enumerate(taxonomy.nodes)
        if _entity(compiled, "A") in node
    )
    assert {"urn:test#A", "urn:test#B"} <= set(
        nodes[class_a_node]
    )
    assert {"urn:test#E", "http://www.w3.org/2002/07/owl#Thing"} <= set(
        nodes[taxonomy.top]
    )
    assert {"urn:test#Dead", "http://www.w3.org/2002/07/owl#Nothing"} <= set(
        nodes[taxonomy.bottom]
    )
    assert len(taxonomy.direct_edges) == 5


def test_inconsistent_ontology_collapses_every_class_and_property_once() -> None:
    compiled = _compiled(
        "SubClassOf(owl:Thing owl:Nothing) Declaration(Class(:A)) "
        "Declaration(ObjectProperty(:p))"
    )
    session = SaturationSession(compiled)
    classes = class_taxonomy(session)
    properties = object_property_taxonomy(session)
    assert classes.top == classes.bottom == 0 and classes.direct_edges == ()
    assert properties.top == properties.bottom == 0 and properties.direct_edges == ()
    assert len(classes.nodes[0]) == sum(
        entity.kind is EntityKind.CLASS for entity in compiled.entities
    )
    assert len(properties.nodes[0]) == sum(
        entity.kind is EntityKind.OBJECT_PROPERTY for entity in compiled.entities
    )


def test_property_cycles_bottom_and_top_equivalences_exclude_complex_chains() -> None:
    compiled = _compiled(
        "EquivalentObjectProperties(:p :q) "
        "SubObjectPropertyOf(:p owl:bottomObjectProperty) "
        "SubObjectPropertyOf(owl:topObjectProperty :topAlias) "
        "SubObjectPropertyOf(ObjectPropertyChain(:left :right) :p)"
    )
    taxonomy = object_property_taxonomy(SaturationSession(compiled))
    nodes = _node_iris(compiled, taxonomy)
    expected_bottom = {
        "urn:test#p",
        "urn:test#q",
        "http://www.w3.org/2002/07/owl#bottomObjectProperty",
    }
    assert expected_bottom <= set(nodes[taxonomy.bottom])
    assert {"urn:test#topAlias", "http://www.w3.org/2002/07/owl#topObjectProperty"} <= set(
        nodes[taxonomy.top]
    )
    actual = {member for node in nodes for member in node}
    expected = {
        record.iri
        for record in compiled.entities
        if record.kind is EntityKind.OBJECT_PROPERTY
    }
    assert actual == expected


def test_range_filler_isolation_preserves_source_inference_without_filler_leakage() -> None:
    compiled = _compiled(
        "SubClassOf(:A ObjectSomeValuesFrom(:R :B)) "
        "SubClassOf(ObjectSomeValuesFrom(:R ObjectIntersectionOf(:B :C :D)) :E) "
        "ObjectPropertyRange(:R :C) ObjectPropertyRange(:R :D)"
    )
    session = SaturationSession(compiled)
    shared = session.ensure_classified()
    class_b = _class_expression(compiled, "B")
    class_c = _class_expression(compiled, "C")
    assert class_c in shared.contexts[class_b].decomposed_subsumers

    taxonomy = class_taxonomy(session)
    nodes = _node_iris(compiled, taxonomy)
    edges = _edge_iris(compiled, taxonomy)
    assert (("urn:test#A",), ("urn:test#E",)) in edges
    assert (("urn:test#B",), ("urn:test#C",)) not in edges
    assert (("urn:test#B",), nodes[taxonomy.top]) in edges


def test_ignored_only_entities_never_enter_taxonomy_coverage() -> None:
    compiled = _compiled(
        "Declaration(Class(:Visible)) "
        "SubClassOf(ObjectAllValuesFrom(:p :Ghost) :IgnoredTarget)"
    )
    taxonomy = class_taxonomy(SaturationSession(compiled))
    iris = {iri for node in _node_iris(compiled, taxonomy) for iri in node}
    assert "urn:test#Visible" in iris
    assert "urn:test#Ghost" not in iris
    assert "urn:test#IgnoredTarget" not in iris


def test_stage_conveniences_advance_only_the_required_monotone_stage() -> None:
    compiled = _compiled("Declaration(Class(:A)) Declaration(ObjectProperty(:p))")
    property_session = SaturationSession(compiled)
    object_property_taxonomy(property_session)
    assert int(property_session.stage) == int(Stage.CONSISTENCY)
    class_session = SaturationSession(compiled)
    class_taxonomy(class_session)
    assert int(class_session.stage) == int(Stage.CLASSIFIED)


def test_validator_rejects_coverage_kind_and_transitive_redundancy() -> None:
    compiled = _compiled("SubClassOf(:A :B)")
    taxonomy = class_taxonomy(SaturationSession(compiled))
    redundant = RawTaxonomy(
        nodes=taxonomy.nodes,
        direct_edges=tuple(
            sorted((*taxonomy.direct_edges, (taxonomy.bottom, taxonomy.top)))
        ),
        top=taxonomy.top,
        bottom=taxonomy.bottom,
    )
    with pytest.raises(ValueError, match="transitive redundancy"):
        validate_taxonomy(compiled, redundant, EntityKind.CLASS)
    missing = RawTaxonomy(
        nodes=((taxonomy.nodes[taxonomy.bottom][0], taxonomy.nodes[taxonomy.top][0]),),
        direct_edges=(),
        top=0,
        bottom=0,
    )
    with pytest.raises(ValueError, match="coverage"):
        validate_taxonomy(compiled, missing, EntityKind.CLASS)
    with pytest.raises(ValueError, match="CLASS or OBJECT_PROPERTY"):
        validate_taxonomy(compiled, taxonomy, EntityKind.NAMED_INDIVIDUAL)


def _http_compiled(body: str) -> CompiledOntology:
    source = f"Prefix(:=<http://example.org/>) Ontology({body})".encode()
    return compile_ontology(owl.load_snapshot(source, options=_LOAD_OPTIONS))


def _oracle_class_compiled(name: str) -> CompiledOntology:
    if name == "DuplicateConjuncts" or name == "DuplicateDisjuncts":
        return _http_compiled("SubClassOf(:A :B)")
    if name == "ConjunctionsComplex":
        pair_rows = (
            ("B", "C", "BC"),
            ("B", "D", "BD"),
            ("C", "B", "CB"),
            ("C", "D", "CD"),
            ("D", "C", "DC"),
            ("D", "B", "DB"),
        )
        triple_targets = ("BCD", "BDC", "CBD", "CDB", "DBC", "DCB")
        body = " ".join(
            (
                "SubClassOf(:A :B)",
                "SubClassOf(:A :C)",
                "SubClassOf(:A :D)",
                "SubClassOf(:B :BB)",
                "SubClassOf(:C :CC)",
                "SubClassOf(:D :DD)",
                *(
                    f"SubClassOf(ObjectIntersectionOf(:{first} :{second}) :{target})"
                    for first, second, target in pair_rows
                ),
                *(
                    "SubClassOf(ObjectIntersectionOf(:B :C :D) " f":{target})"
                    for target in triple_targets
                ),
            )
        )
        return _http_compiled(body)
    if name == "DisjointSelf":
        compiled = _http_compiled(
            "Declaration(Class(:A)) Declaration(Class(:B)) Declaration(Class(:C))"
        )
        class_a = _http_class_expression(compiled, "A")
        class_b = _http_class_expression(compiled, "B")
        class_c = _http_class_expression(compiled, "C")
        return replace(
            compiled,
            disjoint_groups=(
                (class_a, class_b, class_a, class_a),
                (class_c, class_c),
            ),
        )
    source = _DATA / "upstream" / "classification" / f"{name}.owl"
    return compile_ontology(owl.load_snapshot(source, options=_LOAD_OPTIONS))


def _http_class_expression(compiled: CompiledOntology, name: str) -> ExpressionId:
    entity = EntityId(entity_id(compiled, f"http://example.org/{name}"))
    return ExpressionId(
        next(
            index
            for index, record in enumerate(compiled.expressions)
            if record.tag is ExpressionTag.CLASS and record.arguments == (entity,)
        )
    )


def _expected_value(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text())["result"]["value"]
    assert isinstance(value, dict)
    return value


def _actual_value(compiled: CompiledOntology, taxonomy: RawTaxonomy) -> dict[str, object]:
    return {
        "bottom": taxonomy.bottom,
        "direct_edges": [list(edge) for edge in taxonomy.direct_edges],
        "nodes": [list(node) for node in _node_iris(compiled, taxonomy)],
        "top": taxonomy.top,
    }


_CLASS_CASES = tuple(path.stem for path in sorted(_CLASS_EXPECTED.glob("*.json")))
_PROPERTY_CASES = tuple(path.stem for path in sorted(_PROPERTY_EXPECTED.glob("*.json")))


@pytest.mark.parametrize("name", _CLASS_CASES)
def test_frozen_class_taxonomy_values(name: str) -> None:
    compiled = _oracle_class_compiled(name)
    actual = class_taxonomy(SaturationSession(compiled))
    assert _actual_value(compiled, actual) == _expected_value(_CLASS_EXPECTED / f"{name}.json")


@pytest.mark.parametrize("name", _PROPERTY_CASES)
def test_frozen_object_property_taxonomy_values(name: str) -> None:
    source = _DATA / "upstream" / "classification" / "object_property" / f"{name}.owl"
    compiled = compile_ontology(owl.load_snapshot(source, options=_LOAD_OPTIONS))
    actual = object_property_taxonomy(SaturationSession(compiled))
    assert _actual_value(compiled, actual) == _expected_value(
        _PROPERTY_EXPECTED / f"{name}.json"
    )
