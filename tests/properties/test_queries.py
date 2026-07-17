from __future__ import annotations

import random

import pyowl_core as owl
import pytest

from pyelk.indexing.compiler import (
    compile_ontology,
    compile_query_expression,
)
from pyelk.indexing.ir import EntityId
from pyelk.reasoning.contracts import QueryKind, RawTaxonomy
from pyelk.reasoning.queries import ClassQueryEngine, named_taxonomy_query
from pyelk.reasoning.realization import realization
from pyelk.reasoning.session import SaturationSession
from pyelk.reasoning.taxonomy import class_taxonomy

_OPTIONS = owl.LoadOptions(
    format=owl.DocumentFormat.FUNCTIONAL,
    imports=owl.ImportPolicy.IGNORE,
    backend=owl.BackendPreference.PYTHON,
)


@pytest.mark.parametrize("seed", range(20))
def test_named_query_closure_matches_slow_chain_selector(seed: int) -> None:
    randomizer = random.Random(seed)
    count = randomizer.randint(2, 20)
    taxonomy = RawTaxonomy(
        nodes=tuple((EntityId(index),) for index in range(count)),
        direct_edges=tuple((index, index + 1) for index in range(count - 1)),
        top=count - 1,
        bottom=0,
    )
    start = randomizer.randrange(count)
    assert named_taxonomy_query(
        taxonomy,
        start,
        QueryKind.SUBCLASSES,
        direct=False,
    ).nodes == tuple((index,) for index in range(start))
    assert named_taxonomy_query(
        taxonomy,
        start,
        QueryKind.SUPERCLASSES,
        direct=False,
    ).nodes == tuple((index,) for index in range(start + 1, count))


@pytest.mark.parametrize("seed", range(8))
def test_tiny_complex_queries_match_entailment_based_slow_selector(seed: int) -> None:
    randomizer = random.Random(seed)
    names = ("A", "B", "C", "D")
    rows = [f"Declaration(Class(:{name}))" for name in names]
    for sub_index, sub in enumerate(names):
        for super_name in names[sub_index + 1 :]:
            if randomizer.random() < 0.45:
                rows.append(f"SubClassOf(:{sub} :{super_name})")
    asserted_name = randomizer.choice(names)
    rows.append(f"ClassAssertion(:{asserted_name} :i)")
    source = (
        "Prefix(:=<urn:property-query#>) Ontology(" + " ".join(rows) + ")"
    ).encode()
    compiled = compile_ontology(owl.load_snapshot(source, options=_OPTIONS))
    session = SaturationSession(compiled)
    taxonomy = class_taxonomy(session)
    realized = realization(session, taxonomy)
    query_engine = ClassQueryEngine(session, taxonomy, realized)
    classes = {
        name: owl.Class(owl.IRI(f"urn:property-query#{name}")) for name in names
    }
    first, second = randomizer.sample(names, 2)
    expression = owl.ObjectIntersectionOf(
        owl.CanonicalSet((classes[first], classes[second]))
    )
    compiled_query = compile_query_expression(expression, compiled)

    entity_ids = {
        record.iri: EntityId(index) for index, record in enumerate(compiled.entities)
    }
    entity_nodes = {
        member: node_index
        for node_index, node in enumerate(taxonomy.nodes)
        for member in node
    }
    outgoing: list[list[int]] = [[] for _ in taxonomy.nodes]
    for sub_node, super_node in taxonomy.direct_edges:
        outgoing[sub_node].append(super_node)

    def ancestors(start: int) -> set[int]:
        reached = {start}
        pending = [start]
        while pending:
            for target in outgoing[pending.pop()]:
                if target not in reached:
                    reached.add(target)
                    pending.append(target)
        return reached

    first_node = entity_nodes[entity_ids[f"urn:property-query#{first}"]]
    second_node = entity_nodes[entity_ids[f"urn:property-query#{second}"]]
    query_supers = ancestors(first_node) | ancestors(second_node)
    expected_supers: set[tuple[int, ...]] = set()
    expected_subs: set[tuple[int, ...]] = set()
    expected_equivalent: tuple[int, ...] | None = None
    for node_index, node in enumerate(taxonomy.nodes):
        query_below = node_index in query_supers
        named_ancestors = ancestors(node_index)
        named_below = first_node in named_ancestors and second_node in named_ancestors
        plain_node = tuple(int(member) for member in node)
        if query_below and named_below:
            expected_equivalent = plain_node
        elif query_below:
            expected_supers.add(plain_node)
        elif named_below:
            expected_subs.add(plain_node)

    actual_equivalent = query_engine.query(
        compiled_query.encoded,
        QueryKind.EQUIVALENT_CLASSES,
    ).nodes
    assert actual_equivalent == (() if expected_equivalent is None else (expected_equivalent,))
    assert set(
        query_engine.query(compiled_query.encoded, QueryKind.SUPERCLASSES, False).nodes
    ) == expected_supers
    assert set(
        query_engine.query(compiled_query.encoded, QueryKind.SUBCLASSES, False).nodes
    ) == expected_subs

    asserted_node = entity_nodes[entity_ids[f"urn:property-query#{asserted_name}"]]
    asserted_ancestors = ancestors(asserted_node)
    expected_instance = first_node in asserted_ancestors and second_node in asserted_ancestors
    actual_instances = query_engine.query(
        compiled_query.encoded,
        QueryKind.INSTANCES,
        False,
    ).nodes
    assert bool(actual_instances) is expected_instance
