"""Native receipt admission and lazy symbol contracts over the actual candidate binary."""

from __future__ import annotations

from unittest.mock import patch

import pyowl_core as owl
import pytest

from pyelk import Reasoner, ReasonerConfig, require_native_pipeline_support
from pyelk.exceptions import BackendUnavailableError
from pyelk.indexing.metadata import NativeCompilerMetadata
from tests.backends.test_rust_core import native_module  # noqa: F401

SOURCE = b"""Ontology(<urn:strict>
 Declaration(Class(<urn:a>)) Declaration(Class(<urn:long-name>))
 Declaration(NamedIndividual(<urn:a>)) Declaration(ObjectProperty(<urn:p>))
 SubClassOf(<urn:a> <urn:long-name>) ClassAssertion(<urn:a> <urn:a>)
)"""


@pytest.fixture
def native_core():
    pytest.importorskip("pyowl_core._native", reason="requires the native core wheel")


def native_snapshot():
    return owl.load_snapshot(SOURCE, options=owl.LoadOptions(backend=owl.BackendPreference.NATIVE))


@pytest.mark.usefixtures("native_core")
def test_strict_symbols_are_lazy_native_and_keep_punning():
    view = native_snapshot()
    with (
        patch(
            "pyelk.backends.rust.decode_compiler_metadata",
            side_effect=AssertionError("bulk metadata"),
        ),
        patch(
            "pyelk.api._compile_ontology_with_materialization_count",
            side_effect=AssertionError("scalar"),
        ),
    ):
        with Reasoner(view, ReasonerConfig(require_native_pipeline=True)) as reasoner:
            metadata = reasoner._require_metadata()
            assert isinstance(metadata, NativeCompilerMetadata)
            assert len(reasoner._entity_by_record) == 0
            assert reasoner.diagnostics()["native_symbol_rows_materialized"] == 0
            assert not reasoner.is_inconsistent().value
            assert len(reasoner._entity_by_record) == 0
            assert {x.iri.value for x in reasoner.all_classes()} >= {"urn:a", "urn:long-name"}
            owner = metadata.native_owner
            assert owner.find(0, "urn:a") != owner.find(1, "urn:a")
            assert owner.find(2, "urn:missing") is None
            with pytest.raises(TypeError):
                type(owner)()
        with pytest.raises(RuntimeError, match="closed"):
            owner.record(0)


def test_strict_rejects_python_owner_before_consumer_compilation():
    view = owl.load_snapshot(SOURCE, options=owl.LoadOptions(backend=owl.BackendPreference.PYTHON))
    with (
        patch(
            "pyelk.backends.rust.RustBackendFactory.create_encoded_session",
            side_effect=AssertionError("compiled"),
        ),
        pytest.raises(owl.AdapterCompatibilityError),
    ):
        Reasoner(view, ReasonerConfig(require_native_pipeline=True))


def test_preflight_rejects_old_binary_and_runs_before_loader(request):
    module = request.getfixturevalue("native_module")
    with patch.object(module, "NATIVE_SERVICE_API_VERSION", 0):
        with pytest.raises(BackendUnavailableError):
            require_native_pipeline_support()
        with (
            patch("pyelk.api._acquire_input", side_effect=AssertionError("parsed")),
            pytest.raises(BackendUnavailableError),
        ):
            Reasoner(SOURCE, ReasonerConfig(require_native_pipeline=True))


def test_strict_config_and_loader_reject_python():
    with pytest.raises(ValueError, match="Python"):
        ReasonerConfig(require_native_pipeline=True, backend="python")
    with pytest.raises(ValueError, match="Python document loader"):
        Reasoner(
            SOURCE,
            ReasonerConfig(require_native_pipeline=True),
            load_options=owl.LoadOptions(backend=owl.BackendPreference.PYTHON),
        )


def _answers(reasoner):
    a = owl.Class(owl.IRI("urn:a"))
    b = owl.Class(owl.IRI("urn:long-name"))
    p = owl.ObjectProperty(owl.IRI("urn:p"))
    fresh = owl.Class(owl.IRI("urn:short-new"))
    return (
        reasoner.classify(),
        reasoner.classify_object_properties(),
        reasoner.realize(),
        reasoner.superclasses(a),
        reasoner.subclasses(b, direct=True),
        reasoner.equivalent_classes(a),
        reasoner.is_satisfiable(a),
        reasoner.instances(b),
        reasoner.types(owl.NamedIndividual(owl.IRI("urn:a"))),
        reasoner.types(owl.NamedIndividual(owl.IRI("urn:fresh"))),
        reasoner.super_object_properties(p),
        reasoner.sub_object_properties(p),
        reasoner.equivalent_object_properties(p),
        reasoner.equivalent_classes(fresh),
        reasoner.equivalent_object_properties(owl.ObjectProperty(owl.IRI("urn:new"))),
        reasoner.is_entailed(owl.SubClassOf(a, b)),
    )


@pytest.mark.usefixtures("native_core")
def test_strict_results_match_public_values_without_python_bulk_validation():
    from contextlib import ExitStack

    source = SOURCE.replace(
        b"Declaration(Class(<urn:a>))",
        b"Declaration(Class(<urn:a>)) Declaration(Class(<urn:zz>)) Declaration(Class(<urn:"
        + b"x" * 128
        + b">))",
    )
    view = owl.load_snapshot(source, options=owl.LoadOptions(backend=owl.BackendPreference.NATIVE))
    with Reasoner(view, ReasonerConfig(backend="python")) as baseline:
        expected = _answers(baseline)
    with ExitStack() as stack:
        for target in [
            "pyelk.backends.rust.decode_raw_taxonomy",
            "pyelk.backends.rust.decode_raw_realization",
            "pyelk.backends.rust.decode_raw_query_result",
            "pyelk.api.validate_taxonomy_entities",
            "pyelk.api.raw_types",
            "pyelk.api.named_object_property_query",
            "pyelk.reasoning.contracts.RawTaxonomy.__post_init__",
            "pyelk.reasoning.contracts.RawRealization.__post_init__",
            "pyelk.reasoning.contracts.RawQueryResult.__post_init__",
            "pyelk.result.Taxonomy.__post_init__",
            "pyelk.result.InstanceTaxonomy.__post_init__",
            "pyelk.result.EntityNode.__post_init__",
        ]:
            stack.enter_context(
                patch(target, side_effect=AssertionError("Python bulk result handling"))
            )
        with Reasoner(view, ReasonerConfig(require_native_pipeline=True)) as actual:
            assert _answers(actual) == expected
            diagnostics = actual.diagnostics()
            assert diagnostics["native_metadata_domain_copies"] == 0
            assert diagnostics["native_result_publications"] > 0
            assert diagnostics["native_live_result_envelopes"] <= 5


@pytest.mark.usefixtures("native_core")
def test_native_result_owner_and_immutable_payload_reject_substitution():
    from pyelk.exceptions import BackendProtocolError

    with (
        Reasoner(native_snapshot(), ReasonerConfig(require_native_pipeline=True)) as left,
        Reasoner(native_snapshot(), ReasonerConfig(require_native_pipeline=True)) as right,
    ):
        raw = left._raw_class_taxonomy()
        session = left._require_session()
        with pytest.raises(BackendProtocolError, match="issued by this native session"):
            right._validate_taxonomy(
                raw, __import__("pyelk.indexing.ir", fromlist=["EntityKind"]).EntityKind.CLASS
            )
        proof = session._native_results["class"][1]
        with pytest.raises(ValueError, match="different session"):
            proof.payloads(right._require_session()._native)
        with pytest.raises(TypeError):
            type(proof)()
        object.__setattr__(raw, "nodes", tuple(reversed(raw.nodes)))
        with pytest.raises(BackendProtocolError, match="unchanged"):
            session.native_payload(raw)


@pytest.mark.parametrize(
    "extra",
    [
        b"EquivalentClasses(<urn:a> <urn:long-name>)",
        b"SubClassOf(<urn:long-name> <urn:a>)",
        b"SubClassOf(<http://www.w3.org/2002/07/owl#Thing> <http://www.w3.org/2002/07/owl#Nothing>)",
        b"Declaration(NamedIndividual(<urn:second>)) SameIndividual(<urn:a> <urn:second>)",
        b"SubClassOf(<urn:a> ObjectSomeValuesFrom(<urn:p> <urn:long-name>))",
        b"Declaration(Class(<urn:\xc3\xa9>)) Declaration(ObjectProperty(<urn:longer-property>)) "
        b"SubObjectPropertyOf(<urn:p> <urn:longer-property>)",
    ],
)
@pytest.mark.usefixtures("native_core")
def test_strict_result_parity_across_equivalence_inconsistency_and_unicode(extra):
    view = owl.load_snapshot(
        SOURCE.rstrip()[:-1] + extra + b")",
        options=owl.LoadOptions(backend=owl.BackendPreference.NATIVE),
    )
    with (
        Reasoner(view, ReasonerConfig(backend="python")) as baseline,
        Reasoner(view, ReasonerConfig(require_native_pipeline=True)) as actual,
    ):
        assert _answers(actual) == _answers(baseline)
        a = owl.Class(owl.IRI("urn:a"))
        fresh = owl.Class(owl.IRI("urn:anonymous-fresh"))
        for expression in [
            owl.ObjectIntersectionOf((a, fresh)),
            owl.ObjectSomeValuesFrom(owl.ObjectProperty(owl.IRI("urn:p")), a),
        ]:
            assert actual.superclasses(expression) == baseline.superclasses(expression)
            assert actual.equivalent_classes(expression) == baseline.equivalent_classes(expression)


def test_strict_environment_conflict_rejects_before_parse(monkeypatch):
    monkeypatch.setenv("PYELK_PURE_PYTHON", "1")
    with (
        patch("pyelk.api._acquire_input", side_effect=AssertionError("parsed")),
        pytest.raises(BackendUnavailableError),
    ):
        Reasoner(SOURCE, ReasonerConfig(require_native_pipeline=True))


@pytest.mark.usefixtures("native_core")
def test_strict_import_closure_keeps_imported_classes_and_origin_owner():
    imported = (
        b"Ontology(<urn:imported> Declaration(Class(<urn:import-only>)) "
        b"SubClassOf(<urn:long-name> <urn:import-only>))"
    )
    source = SOURCE.replace(
        b"Ontology(<urn:strict>", b"Ontology(<urn:strict> Import(<urn:imported>)"
    )
    view = owl.load_snapshot(
        source,
        options=owl.LoadOptions(backend=owl.BackendPreference.NATIVE),
        resolver=owl.MappingResolver({"urn:imported": imported}),
    )
    with (
        Reasoner(view, ReasonerConfig(backend="python")) as baseline,
        Reasoner(view, ReasonerConfig(require_native_pipeline=True)) as actual,
    ):
        assert actual.ontology is view
        assert actual.classify() == baseline.classify()
        assert owl.Class(owl.IRI("urn:import-only")) in actual.all_classes()
        assert actual.diagnostics()["native_core_receipt_validated"] is True
