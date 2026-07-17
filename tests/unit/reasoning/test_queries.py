from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pyowl_core as owl
import pytest

from pyelk.indexing.compiler import compile_ontology, compile_query_expression
from pyelk.indexing.ir import CompiledOntology, EntityId, QueryEntityRecord, QueryIR
from pyelk.reasoning.contracts import QueryKind
from pyelk.reasoning.queries import (
    ClassQueryEngine,
    named_class_query,
    query_feature_metadata,
)
from pyelk.reasoning.realization import realization
from pyelk.reasoning.session import SaturationSession
from pyelk.reasoning.taxonomy import class_taxonomy

_DATA = Path(__file__).parents[2] / "data" / "elk-v0.6.0"
_EXPECTED = _DATA / "expected" / "query" / "class"
_UPSTREAM = _DATA / "upstream" / "query" / "class"
_OPTIONS = owl.LoadOptions(
    format=owl.DocumentFormat.FUNCTIONAL,
    imports=owl.ImportPolicy.IGNORE,
    backend=owl.BackendPreference.PYTHON,
)


def _compiled_case(name: str) -> CompiledOntology:
    return compile_ontology(owl.load_snapshot(_UPSTREAM / f"{name}.owl", options=_OPTIONS))


def _parse_expression(name: str, text: str) -> owl.ClassExpression:
    del name
    snapshot = owl.load_snapshot(
        f"Ontology(SubClassOf(<urn:query-root> {text}))".encode(),
        options=_OPTIONS,
    )
    axiom = next(snapshot.iter_axioms(owl.SubClassOf))
    if not isinstance(axiom, owl.SubClassOf):  # pragma: no cover - filtered iterator
        raise AssertionError("synthetic query parser returned another axiom family")
    return axiom.super_class


def _result_iris(
    nodes: tuple[tuple[int, ...], ...],
    compiled: CompiledOntology,
    fresh: tuple[object, ...],
) -> list[list[str]]:
    result: list[list[str]] = []
    for node in nodes:
        members: list[str] = []
        for member in node:
            record = (
                compiled.entities[member]
                if member < len(compiled.entities)
                else fresh[member - len(compiled.entities)]
            )
            members.append(record.iri)  # type: ignore[attr-defined]
        result.append(members)
    return result


_CASES = tuple(path.stem for path in sorted(_EXPECTED.glob("*.json")))


@pytest.mark.parametrize("name", _CASES)
def test_all_26_frozen_class_query_families(name: str) -> None:
    compiled = _compiled_case(name)
    session = SaturationSession(compiled)
    taxonomy = class_taxonomy(session)
    realized = realization(session, taxonomy)
    engine = ClassQueryEngine(session, taxonomy, realized)
    queries = json.loads((_EXPECTED / f"{name}.json").read_text())["result"]["value"]["queries"]
    for expected in queries:
        expression = _parse_expression(name, expected["expression"])
        query = compile_query_expression(expression, compiled)
        equivalent = engine.query(query.encoded, QueryKind.EQUIVALENT_CLASSES)
        equivalent_nodes = _result_iris(equivalent.nodes, compiled, query.fresh_entities)
        actual_equivalent = equivalent_nodes[0] if equivalent_nodes else []
        assert (
            engine.query(query.encoded, QueryKind.SATISFIABLE).boolean
            is expected["satisfiable"]["value"]
        )
        assert actual_equivalent == expected["equivalent_classes"]["value"]
        assert (
            _result_iris(
                engine.query(query.encoded, QueryKind.SUBCLASSES, True).nodes,
                compiled,
                query.fresh_entities,
            )
            == expected["direct_subclasses"]["value"]
        )
        assert (
            _result_iris(
                engine.query(query.encoded, QueryKind.SUPERCLASSES, True).nodes,
                compiled,
                query.fresh_entities,
            )
            == expected["direct_superclasses"]["value"]
        )
        assert (
            _result_iris(
                engine.query(query.encoded, QueryKind.INSTANCES, True).nodes,
                compiled,
                query.fresh_entities,
            )
            == expected["direct_instances"]["value"]
        )


def test_fresh_class_and_unindexed_fallbacks_are_exact() -> None:
    compiled = compile_ontology(owl.load_snapshot(b"Ontology()", options=_OPTIONS))
    session = SaturationSession(compiled)
    taxonomy = class_taxonomy(session)
    realized = realization(session, taxonomy)
    engine = ClassQueryEngine(session, taxonomy, realized)
    fresh = owl.Class(owl.IRI("urn:fresh#A"))
    query = compile_query_expression(fresh, compiled)
    fresh_id = len(compiled.entities)
    assert engine.query(query.encoded, QueryKind.EQUIVALENT_CLASSES).nodes == ((fresh_id,),)
    assert engine.query(query.encoded, QueryKind.SUBCLASSES, True).nodes == (
        tuple(int(value) for value in taxonomy.nodes[taxonomy.bottom]),
    )
    assert engine.query(query.encoded, QueryKind.SUPERCLASSES, True).nodes == (
        tuple(int(value) for value in taxonomy.nodes[taxonomy.top]),
    )
    assert engine.query(None, QueryKind.SATISFIABLE).boolean is True
    assert engine.query(None, QueryKind.EQUIVALENT_CLASSES).nodes == ()
    assert engine.query(None, QueryKind.SUBCLASSES, False).nodes == ()
    assert engine.query(None, QueryKind.SUBCLASSES, True).nodes
    assert engine.query(None, QueryKind.SUPERCLASSES, False).nodes == ()
    assert engine.query(None, QueryKind.SUPERCLASSES, True).nodes


def test_inconsistent_quiet_fallback_precedes_query_selection() -> None:
    source = b"Prefix(:=<urn:q#>) Ontology(ClassAssertion(owl:Nothing :i))"
    compiled = compile_ontology(owl.load_snapshot(source, options=_OPTIONS))
    session = SaturationSession(compiled)
    taxonomy = class_taxonomy(session)
    realized = realization(session, taxonomy)
    engine = ClassQueryEngine(session, taxonomy, realized)
    assert engine.query(None, QueryKind.SATISFIABLE).boolean is False
    assert engine.query(None, QueryKind.EQUIVALENT_CLASSES).nodes == (
        tuple(int(value) for value in taxonomy.nodes[0]),
    )
    assert engine.query(None, QueryKind.SUBCLASSES, True).nodes == ()
    assert engine.query(None, QueryKind.SUPERCLASSES, True).nodes == ()
    assert engine.query(None, QueryKind.INSTANCES, False).nodes


def test_query_cache_does_not_mutate_public_enumeration() -> None:
    compiled = compile_ontology(owl.load_snapshot(b"Ontology()", options=_OPTIONS))
    session = SaturationSession(compiled)
    taxonomy = class_taxonomy(session)
    realized = realization(session, taxonomy)
    engine = ClassQueryEngine(session, taxonomy, realized)
    query = compile_query_expression(owl.Class(owl.IRI("urn:fresh#A")), compiled)
    before = (compiled.entities, taxonomy.nodes, realized.instance_nodes)
    first = engine.query(query.encoded, QueryKind.SUPERCLASSES, True)
    second = engine.query(query.encoded, QueryKind.SUPERCLASSES, True)
    assert first is second
    assert engine.cached_query_count == 1
    assert (compiled.entities, taxonomy.nodes, realized.instance_nodes) == before


def test_named_query_and_sparse_feature_hook() -> None:
    compiled = compile_ontology(owl.load_snapshot(b"Ontology()", options=_OPTIONS))
    taxonomy = class_taxonomy(SaturationSession(compiled))
    top_entity = taxonomy.nodes[taxonomy.top][0]
    result = named_class_query(taxonomy, top_entity, QueryKind.SUBCLASSES, direct=True)
    assert result.nodes
    assert query_feature_metadata((0, 2, 0, 3)).counts == ((1, 2), (3, 3))


def test_query_overlay_rejects_a_mismatched_session_entity_reference() -> None:
    compiled = compile_ontology(owl.load_snapshot(b"Ontology()", options=_OPTIONS))
    session = SaturationSession(compiled)
    taxonomy = class_taxonomy(session)
    realized = realization(session, taxonomy)
    query = compile_query_expression(owl.Class(owl.IRI("urn:fresh#A")), compiled)
    assert query.encoded is not None
    decoded = QueryIR.decode(query.encoded)
    hostile = replace(
        decoded,
        entities=(
            QueryEntityRecord(decoded.entities[0].entity, EntityId(0)),
            *decoded.entities[1:],
        ),
    )
    with pytest.raises(ValueError, match="does not match"):
        ClassQueryEngine(session, taxonomy, realized).query(
            hostile.encode(),
            QueryKind.SATISFIABLE,
        )
