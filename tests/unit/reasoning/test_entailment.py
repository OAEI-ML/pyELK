from __future__ import annotations

import json
from pathlib import Path

import pyowl_core as owl
import pytest

from pyelk.indexing.compiler import compile_entailment_query, compile_ontology
from pyelk.reasoning.entailment import (
    EntailmentEngine,
    entails,
    unsupported_entailment,
)
from pyelk.reasoning.session import SaturationSession

_DATA = Path(__file__).parents[2] / "data" / "elk-v0.6.0"
_EXPECTED = _DATA / "expected" / "query" / "entailment"
_UPSTREAM = _DATA / "upstream" / "query" / "entailment"
_OPTIONS = owl.LoadOptions(
    format=owl.DocumentFormat.FUNCTIONAL,
    imports=owl.ImportPolicy.IGNORE,
    backend=owl.BackendPreference.PYTHON,
)


def _unsafe_singleton_equivalence(expression: owl.ClassExpression) -> owl.EquivalentClasses:
    value = object.__new__(owl.EquivalentClasses)
    object.__setattr__(value, "expressions", (expression,))
    object.__setattr__(value, "annotations", owl.CanonicalSet())
    return value


def _parse_axiom(text: str) -> owl.AxiomNode:
    try:
        snapshot = owl.load_snapshot(f"Ontology({text})".encode(), options=_OPTIONS)
    except owl.StructuralConstraintError:
        # Four EmptyOntology oracle rows use duplicate set positions that canonical OWL
        # intentionally collapses to one expression.  Their normalized entailment boundary
        # is precisely the supported singleton cycle.
        return _unsafe_singleton_equivalence(owl.OWL_THING)
    return next(snapshot.iter_axioms())


_CASES = tuple(path.stem for path in sorted(_EXPECTED.glob("*.json")))


@pytest.mark.parametrize("name", _CASES)
def test_all_16_frozen_entailment_groups(name: str) -> None:
    compiled = compile_ontology(owl.load_snapshot(_UPSTREAM / f"{name}.owl", options=_OPTIONS))
    engine = EntailmentEngine(SaturationSession(compiled))
    rows = json.loads((_EXPECTED / f"{name}.json").read_text())["result"]["value"]["queries"]
    for row in rows:
        query = compile_entailment_query(_parse_axiom(row["axiom"]), compiled)
        assert engine.entails(query.encoded) is row["entailed"]


def test_unindexable_is_false_even_when_supported_queries_explode() -> None:
    inconsistent = owl.load_snapshot(
        b"Prefix(:=<urn:e#>) Ontology(SubClassOf(owl:Thing owl:Nothing))",
        options=_OPTIONS,
    )
    compiled = compile_ontology(inconsistent)
    session = SaturationSession(compiled)
    engine = EntailmentEngine(session)
    supported = compile_entailment_query(
        owl.SubClassOf(
            owl.Class(owl.IRI("urn:e#fresh-a")),
            owl.Class(owl.IRI("urn:e#fresh-b")),
        ),
        compiled,
    )
    unsupported = compile_entailment_query(
        owl.Declaration(owl.Class(owl.IRI("urn:e#fresh"))),
        compiled,
    )
    assert supported.encoded is not None
    assert unsupported.encoded is None
    assert entails(session, supported.encoded, engine=engine) is True
    assert entails(session, unsupported.encoded, engine=engine) is False


def test_singleton_cycles_are_true_and_empty_payload_kind_is_rejected() -> None:
    compiled = compile_ontology(owl.load_snapshot(b"Ontology()", options=_OPTIONS))
    query = compile_entailment_query(_unsafe_singleton_equivalence(owl.OWL_THING), compiled)
    engine = EntailmentEngine(SaturationSession(compiled))
    assert engine.entails(query.encoded) is True
    assert engine.cached_query_count == 1


def test_unsupported_feature_hook_retains_exact_sparse_positions() -> None:
    value, metadata = unsupported_entailment((0, 4, 0, 0, 2))
    assert value is False
    assert metadata.counts == ((1, 4), (4, 2))
