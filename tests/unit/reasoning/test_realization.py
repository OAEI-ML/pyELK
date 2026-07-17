from __future__ import annotations

import json
from pathlib import Path

import pyowl_core as owl
import pytest

from pyelk.indexing.compiler import compile_ontology
from pyelk.indexing.ir import EntityId
from pyelk.reasoning.realization import (
    equivalent_individuals,
    fresh_equivalent_node,
    instances,
    realization,
    taxonomy_sub_nodes,
    taxonomy_super_nodes,
    types,
)
from pyelk.reasoning.session import SaturationSession
from pyelk.reasoning.taxonomy import class_taxonomy

_DATA = Path(__file__).parents[2] / "data" / "elk-v0.6.0"
_OPTIONS = owl.LoadOptions(
    format=owl.DocumentFormat.FUNCTIONAL,
    imports=owl.ImportPolicy.IGNORE,
    backend=owl.BackendPreference.PYTHON,
)


def _load(path: Path) -> owl.OntologySnapshot:
    return owl.load_snapshot(path, options=_OPTIONS)


@pytest.mark.parametrize(
    "name",
    tuple(path.stem for path in sorted((_DATA / "expected" / "realization").glob("*.json"))),
)
def test_all_five_frozen_realization_values(name: str) -> None:
    compiled = compile_ontology(_load(_DATA / "upstream" / "realization" / f"{name}.owl"))
    session = SaturationSession(compiled)
    taxonomy = class_taxonomy(session)
    actual = realization(session, taxonomy)
    expected = json.loads(
        (_DATA / "expected" / "realization" / f"{name}.json").read_text()
    )["result"]["value"]
    assert {
        "bottom": taxonomy.bottom,
        "direct_edges": [list(edge) for edge in taxonomy.direct_edges],
        "direct_types": [list(row) for row in actual.direct_types],
        "instance_nodes": [
            [compiled.entities[member].iri for member in node]
            for node in actual.instance_nodes
        ],
        "nodes": [
            [compiled.entities[member].iri for member in node] for node in taxonomy.nodes
        ],
        "top": taxonomy.top,
    } == expected


def test_same_individual_quotient_has_no_unique_name_assumption() -> None:
    source = b"""
        Prefix(:=<urn:realization#>) Ontology(
          Declaration(NamedIndividual(:declared))
          SameIndividual(:a :b)
          ClassAssertion(:A :a)
        )
    """
    compiled = compile_ontology(owl.load_snapshot(source, options=_OPTIONS))
    session = SaturationSession(compiled)
    taxonomy = class_taxonomy(session)
    value = realization(session, taxonomy)
    by_iri = {record.iri: index for index, record in enumerate(compiled.entities)}
    assert equivalent_individuals(value, by_iri["urn:realization#a"]) == (
        by_iri["urn:realization#a"],
        by_iri["urn:realization#b"],
    )
    assert equivalent_individuals(value, by_iri["urn:realization#declared"]) == (
        by_iri["urn:realization#declared"],
    )


def test_direct_and_transitive_type_instance_views_are_inverse() -> None:
    source = b"""
        Prefix(:=<urn:realization#>) Ontology(
          SubClassOf(:A :B) SubClassOf(:B :C) ClassAssertion(:A :i)
        )
    """
    compiled = compile_ontology(owl.load_snapshot(source, options=_OPTIONS))
    session = SaturationSession(compiled)
    taxonomy = class_taxonomy(session)
    value = realization(session, taxonomy)
    by_iri = {record.iri: index for index, record in enumerate(compiled.entities)}
    direct_iris = {
        compiled.entities[node[0]].iri
        for node in types(value, by_iri["urn:realization#i"], direct=True)
    }
    all_iris = {
        compiled.entities[node[0]].iri
        for node in types(value, by_iri["urn:realization#i"], direct=False)
    }
    assert direct_iris == {"urn:realization#A"}
    assert {"urn:realization#A", "urn:realization#B", "urn:realization#C"} <= all_iris
    assert instances(value, by_iri["urn:realization#B"], direct=True) == ()
    assert instances(value, by_iri["urn:realization#B"], direct=False) == (
        equivalent_individuals(value, by_iri["urn:realization#i"]),
    )


def test_named_taxonomy_and_fresh_helpers_preserve_canonical_order() -> None:
    source = b"Prefix(:=<urn:realization#>) Ontology(SubClassOf(:A :B))"
    compiled = compile_ontology(owl.load_snapshot(source, options=_OPTIONS))
    taxonomy = class_taxonomy(SaturationSession(compiled))
    by_iri = {record.iri: index for index, record in enumerate(compiled.entities)}
    assert taxonomy_super_nodes(taxonomy, by_iri["urn:realization#A"], direct=True) == (
        (EntityId(by_iri["urn:realization#B"]),),
    )
    assert taxonomy_sub_nodes(taxonomy, by_iri["urn:realization#B"], direct=True) == (
        (EntityId(by_iri["urn:realization#A"]),),
    )
    assert fresh_equivalent_node(len(compiled.entities)) == (EntityId(len(compiled.entities)),)
