"""Exact feature-count parity against all 79 pinned Java-oracle fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pyowl_core as owl
import pytest
from pyowl_core.extensions.swrl import ClassAtom, SWRLRule, Variable

from pyelk.exceptions import UnsupportedFeatureError
from pyelk.indexing.builder import PREDEFINED_ENTITIES
from pyelk.indexing.compiler import compile_entailment_query, compile_ontology
from pyelk.indexing.conversion import (
    FEATURE_INDEX,
    ONTOLOGY_FEATURE_NAMES,
    QUERY_FEATURE_NAMES,
)
from pyelk.reasoning.completeness import Feature

from ._support import ExtensionView, as_view, load_functional

_DATA = Path(__file__).resolve().parents[2] / "data" / "elk-v0.6.0"
_ONTOLOGY_FIXTURES = _DATA / "features" / "ontology"
_QUERY_FIXTURES = _DATA / "features" / "query"
_EXPECTED = _DATA / "expected" / "features"
_OPTIONS = owl.LoadOptions(
    format=owl.DocumentFormat.FUNCTIONAL,
    imports=owl.ImportPolicy.IGNORE,
    backend=owl.BackendPreference.PYTHON,
)
_IGNORED_ONTOLOGY_FEATURES = frozenset(
    {
        "ANONYMOUS_INDIVIDUAL",
        "ASYMMETRIC_OBJECT_PROPERTY",
        "DATA_ALL_VALUES_FROM",
        "DATA_EXACT_CARDINALITY",
        "DATA_MAX_CARDINALITY",
        "DATA_MIN_CARDINALITY",
        "DATA_PROPERTY",
        "DATA_PROPERTY_ASSERTION",
        "DATA_PROPERTY_DOMAIN",
        "DATA_PROPERTY_RANGE",
        "DATA_SOME_VALUES_FROM",
        "DATATYPE",
        "DATATYPE_DEFINITION",
        "DISJOINT_DATA_PROPERTIES",
        "DISJOINT_OBJECT_PROPERTIES",
        "EQUIVALENT_DATA_PROPERTIES",
        "FUNCTIONAL_DATA_PROPERTY",
        "FUNCTIONAL_OBJECT_PROPERTY",
        "HAS_KEY",
        "INVERSE_FUNCTIONAL_OBJECT_PROPERTY",
        "INVERSE_OBJECT_PROPERTIES",
        "IRREFLEXIVE_OBJECT_PROPERTY",
        "NEGATIVE_DATA_PROPERTY_ASSERTION",
        "NEGATIVE_OBJECT_PROPERTY_ASSERTION",
        "OBJECT_ALL_VALUES_FROM",
        "OBJECT_EXACT_CARDINALITY",
        "OBJECT_INVERSE_OF",
        "OBJECT_MAX_CARDINALITY",
        "OBJECT_MIN_CARDINALITY",
        "SUB_DATA_PROPERTY_OF",
        "SWRL_RULE",
        "SYMMETRIC_OBJECT_PROPERTY",
    }
)


def _actual_counts(values: tuple[int, ...]) -> dict[str, int]:
    return {name: values[index] for name, index in FEATURE_INDEX.items() if values[index]}


def test_compiler_feature_order_is_the_exact_checked_upstream_enum() -> None:
    assert (*ONTOLOGY_FEATURE_NAMES, *QUERY_FEATURE_NAMES) == tuple(
        feature.name for feature in Feature
    )


def _oracle_counts(scope: str, name: str) -> dict[str, int]:
    payload = json.loads((_EXPECTED / scope / f"{name}.json").read_text())
    return {str(key): int(value) for key, value in payload["feature"]["actual_counts"].items()}


def _has_key() -> owl.HasKey:
    return owl.HasKey(
        owl.Class(owl.IRI("http://example.org/A")),
        owl.CanonicalSet((owl.ObjectProperty(owl.IRI("http://example.org/r")),)),
        owl.CanonicalSet((owl.DataProperty(owl.IRI("http://example.org/dp")),)),
    )


def _swrl_rule() -> SWRLRule:
    variable = Variable(owl.IRI("http://example.org/x"))
    return SWRLRule(
        owl.CanonicalSet((ClassAtom(owl.Class(owl.IRI("http://example.org/A")), variable),)),
        owl.CanonicalSet((ClassAtom(owl.Class(owl.IRI("http://example.org/B")), variable),)),
    )


def _ontology_feature_view(name: str) -> owl.OntologyView:
    if name == "HAS_KEY":
        base = load_functional("")
        return owl.apply_delta(
            base,
            owl.OntologyDelta(add_axioms=owl.CanonicalSet((_has_key(),))),
        )
    if name == "SWRL_RULE":
        return as_view(ExtensionView(load_functional(""), _swrl_rule()))
    return owl.load_snapshot(
        _ONTOLOGY_FIXTURES.joinpath(f"{name}.ofn").read_bytes(), options=_OPTIONS
    )


@pytest.mark.parametrize(
    "name",
    tuple(path.stem for path in sorted(_ONTOLOGY_FIXTURES.glob("*.ofn"))),
)
def test_ontology_feature_corpus_matches_exact_java_counts(name: str) -> None:
    view = _ontology_feature_view(name)
    compiled = compile_ontology(view)
    assert _actual_counts(compiled.feature_counts) == _oracle_counts("ontology", name)
    if name in _IGNORED_ONTOLOGY_FEATURES:
        assert set(compiled.entities) == set(PREDEFINED_ENTITIES)
        assert compiled.subclass_axioms == ()
        assert compiled.equivalent_class_axioms == ()
        assert compiled.disjoint_groups == ()
        assert compiled.subproperty_axioms == ()
        assert compiled.property_ranges == ()
        with pytest.raises(UnsupportedFeatureError) as caught:
            compile_ontology(view, unsupported="error")
        assert caught.value.feature == name


def _query_value(name: str) -> owl.StructuralNode:
    if name == "QUERY_HAS_KEY_AXIOM":
        return _has_key()
    if name == "QUERY_SWRL_RULE":
        return _swrl_rule()
    snapshot = owl.load_snapshot(
        _QUERY_FIXTURES.joinpath(f"{name}.ofn").read_bytes(), options=_OPTIONS
    )
    values = (
        *snapshot.iter_axioms(scope=owl.AxiomScope.ROOT),
        *snapshot.iter_extensions(scope=owl.AxiomScope.ROOT),
    )
    assert len(values) == 1
    return values[0]


@pytest.mark.parametrize(
    "name",
    tuple(path.stem for path in sorted(_QUERY_FIXTURES.glob("*.ofn"))),
)
def test_query_feature_corpus_matches_exact_java_counts(name: str) -> None:
    symbols = compile_ontology(load_functional("", ontology_iri="urn:empty"))
    compiled = compile_entailment_query(_query_value(name), symbols)
    assert compiled.encoded is None
    assert _actual_counts(compiled.feature_counts) == _oracle_counts("query", name)
