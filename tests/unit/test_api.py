from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import cast

import pyowl_core as owl
import pytest

import pyelk
from pyelk import Reasoner, ReasonerConfig
from pyelk.backends import EncodedBackendSelection
from pyelk.backends.python import PythonBackendFactory
from pyelk.exceptions import (
    BackendProtocolError,
    FreshEntityError,
    IncompleteReasoningError,
    ReasonerClosedError,
    UnsupportedFeatureError,
)
from pyelk.indexing.compiler import compile_ontology
from pyelk.indexing.ir import CompiledOntology
from pyelk.indexing.metadata import metadata_from_compiled
from pyelk.indexing.summary import compiler_digest
from pyelk.reasoning.contracts import (
    BackendConfig,
    BackendInfo,
    BackendSession,
    DiagnosticScalar,
    QueryKind,
    RawQueryResult,
    RawRealization,
    RawTaxonomy,
)
from tests.unit.inputs._support import CountingProvider

_OPTIONS = owl.LoadOptions(
    format=owl.DocumentFormat.FUNCTIONAL,
    imports=owl.ImportPolicy.IGNORE,
    backend=owl.BackendPreference.PYTHON,
)


def _snapshot(body: str = "") -> owl.OntologySnapshot:
    source = f"Prefix(:=<urn:api#>) Ontology(<urn:api> {body})".encode()
    return owl.load_snapshot(source, options=_OPTIONS)


def _class(name: str) -> owl.Class:
    return owl.Class(owl.IRI(f"urn:api#{name}"))


def _individual(name: str) -> owl.NamedIndividual:
    return owl.NamedIndividual(owl.IRI(f"urn:api#{name}"))


def _property(name: str) -> owl.ObjectProperty:
    return owl.ObjectProperty(owl.IRI(f"urn:api#{name}"))


class _DelegatingSession:
    def __init__(self, delegate: BackendSession) -> None:
        self.delegate = delegate

    @property
    def info(self) -> BackendInfo:
        return self.delegate.info

    def close(self) -> None:
        self.delegate.close()

    def is_inconsistent(self) -> bool:
        return self.delegate.is_inconsistent()

    def class_taxonomy(self) -> RawTaxonomy:
        return self.delegate.class_taxonomy()

    def object_property_taxonomy(self) -> RawTaxonomy:
        return self.delegate.object_property_taxonomy()

    def realization(self) -> RawRealization:
        return self.delegate.realization()

    def query_class_expression(
        self, encoded_expression: bytes | None, kind: QueryKind, direct: bool
    ) -> RawQueryResult:
        return self.delegate.query_class_expression(encoded_expression, kind, direct)

    def entails(self, encoded_axiom: bytes | None) -> bool:
        return self.delegate.entails(encoded_axiom)

    def diagnostics(self) -> Mapping[str, DiagnosticScalar]:
        return self.delegate.diagnostics()


def _python_session(compiled: CompiledOntology, config: ReasonerConfig) -> BackendSession:
    return PythonBackendFactory().create_session(compiled, cast(BackendConfig, config))


def test_public_surface_uses_exact_core_types_and_aliases() -> None:
    assert vars(pyelk)["Class"] is owl.Class
    assert pyelk.OntologySnapshot is owl.OntologySnapshot
    assert pyelk.OntologyView is owl.OntologyView
    assert pyelk.API_VERSION is owl.API_VERSION
    assert pyelk.load_snapshot is owl.load_snapshot
    assert "CompiledOntology" not in pyelk.__all__


def test_basic_end_to_end_api_shapes_ordering_caching_and_entailment() -> None:
    snapshot = _snapshot(
        "Declaration(Class(:A)) Declaration(Class(:B)) SubClassOf(:A :B) "
        "Declaration(NamedIndividual(:i)) ClassAssertion(:A :i) "
        "Declaration(ObjectProperty(:p)) Declaration(ObjectProperty(:q)) "
        "SubObjectPropertyOf(:p :q)"
    )
    with Reasoner(snapshot, ReasonerConfig(backend="python", workers=12)) as reasoner:
        assert reasoner.ontology is snapshot
        assert reasoner.backend.name == "python"
        assert reasoner.backend.requested_workers == 12
        assert reasoner.backend.effective_workers == 1
        diagnostics = reasoner.diagnostics()
        assert diagnostics["ingestion_path"] == "scalar-python"
        assert diagnostics["stage"] == "compiled"
        assert diagnostics["compiler_cache_schema_version"] == 1
        assert diagnostics["ir_schema_version"] == 1
        assert diagnostics["implementation_version"] == reasoner.backend.implementation_version
        assert isinstance(diagnostics["consumer_compile_seconds"], float)
        assert diagnostics["consumer_compile_seconds"] >= 0.0
        assert diagnostics["materialized_scalar_rows"] == sum(
            1 for _ in snapshot.iter_axioms()
        ) + sum(1 for _ in snapshot.iter_extensions())
        digest = diagnostics["compiler_digest"]
        assert isinstance(digest, str)
        assert len(digest) == 64
        assert set(digest) <= set("0123456789abcdef")
        assert {
            name: value for name, value in diagnostics.items() if name.startswith("encoded_")
        } == {
            "encoded_buffer_bytes": 0,
            "encoded_buffer_count": 0,
            "encoded_compiler_gil_released": False,
            "encoded_detached_buffer_count": 0,
            "encoded_indexed_buffer_count": 0,
            "encoded_posting_bytes": 0,
            "encoded_private_ir_bytes": 0,
            "encoded_referenced_view_count": 0,
            "encoded_segment_count": 0,
            "encoded_staging_copy_bytes": 0,
            "encoded_zero_copy_buffers": 0,
        }
        with pytest.raises(TypeError):
            cast(dict[str, DiagnosticScalar], diagnostics)["stage"] = "hostile"
        assert reasoner.is_consistent().value is True
        assert reasoner.is_inconsistent().value is False
        classes = reasoner.classify()
        assert classes.complete is True
        assert reasoner.classify().value is classes.value
        assert classes.value.node(_class("A")) is not None
        assert classes.value.supers(_class("A"), direct=True) == (classes.value.node(_class("B")),)
        assert reasoner.subclasses(_class("B"), direct=True).value == (
            classes.value.node(_class("A")),
        )
        assert reasoner.superclasses(_class("A"), direct=True).value == (
            classes.value.node(_class("B")),
        )
        assert reasoner.equivalent_classes(_class("A")).value == (classes.value.node(_class("A")),)
        assert reasoner.is_satisfiable(_class("A")).value is True
        assert reasoner.instances(_class("B")).value[0].members == (_individual("i"),)
        assert _class("A") in {
            node.canonical_member for node in reasoner.types(_individual("i")).value
        }
        realized = reasoner.realize().value
        assert realized.class_taxonomy is classes.value
        assert realized.instances[0].members == (_individual("i"),)
        properties = reasoner.classify_object_properties().value
        assert reasoner.super_object_properties(_property("p"), direct=True).value == (
            properties.node(_property("q")),
        )
        assert reasoner.sub_object_properties(_property("q"), direct=True).value == (
            properties.node(_property("p")),
        )
        assert reasoner.equivalent_object_properties(_property("p")).value == properties.node(
            _property("p")
        )
        assert reasoner.is_entailed(owl.SubClassOf(_class("A"), _class("B"))).value is True
        assert reasoner.all_classes() == tuple(
            sorted(reasoner.all_classes(), key=lambda entity: entity.iri.value.encode())
        )
        assert reasoner.all_named_individuals() == (_individual("i"),)
        assert _property("p") in reasoner.all_object_properties()


def test_scalar_native_without_backend_digest_uses_facade_compiler_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot("Declaration(Class(:A)) SubClassOf(:A owl:Thing)")
    expected_digest = compiler_digest(compile_ontology(snapshot)).hex()

    class OlderNative(_DelegatingSession):
        @property
        def info(self) -> BackendInfo:
            delegate = self.delegate.info
            return BackendInfo(
                name="rust",
                implementation_version=delegate.implementation_version,
                ir_major=delegate.ir_major,
                ir_minor=delegate.ir_minor,
                requested_workers=delegate.requested_workers,
                effective_workers=delegate.effective_workers,
                native_available=True,
                fallback_reason=None,
            )

        def diagnostics(self) -> Mapping[str, DiagnosticScalar]:
            values = dict(self.delegate.diagnostics())
            values["ingestion_path"] = "scalar-wire"
            values.pop("compiler_digest", None)
            return values

    def factory(compiled: CompiledOntology, config: ReasonerConfig) -> BackendSession:
        return OlderNative(_python_session(compiled, config))

    monkeypatch.setattr("pyelk.api.create_backend_session", factory)
    with Reasoner(snapshot, ReasonerConfig(backend="python")) as reasoner:
        diagnostics = reasoner.diagnostics()
        assert diagnostics["ingestion_path"] == "scalar-wire"
        assert diagnostics["compiler_digest"] == expected_digest
        assert diagnostics["materialized_scalar_rows"] == 2


def test_provider_is_called_once_and_view_identity_is_retained() -> None:
    snapshot = _snapshot("Declaration(Class(:A))")
    provider = CountingProvider(snapshot)
    reasoner = Reasoner(provider, ReasonerConfig(backend="python"))
    assert provider.calls == 1
    assert provider.path_fallback_calls == 0
    assert reasoner.ontology is snapshot
    assert _class("A") in reasoner.all_classes()
    reasoner.close()
    assert snapshot.signature()


def test_public_facade_runs_from_encoded_metadata_without_scalar_compilation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(
        "Declaration(Class(:A)) Declaration(Class(:B)) SubClassOf(:A :B) "
        "Declaration(NamedIndividual(:i)) ClassAssertion(:A :i)"
    )
    config = ReasonerConfig(backend="python")
    compiled = compile_ontology(snapshot)
    selection = EncodedBackendSelection(
        session=_python_session(compiled, config),
        metadata=metadata_from_compiled(compiled),
    )

    monkeypatch.setattr(
        "pyelk.api.try_create_encoded_backend_session",
        lambda ontology, supplied: selection,
    )

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("scalar compiler/backend path was reached")

    monkeypatch.setattr("pyelk.api._compile_ontology_with_materialization_count", forbidden)
    monkeypatch.setattr("pyelk.api.create_backend_session", forbidden)

    with Reasoner(snapshot, config) as reasoner:
        assert reasoner._entity_by_record == {}
        assert reasoner.is_consistent().value is True
        assert reasoner.classify().value.supers(_class("A"), direct=True) == (
            reasoner.classify().value.node(_class("B")),
        )
        assert reasoner.is_satisfiable(_class("A")).value is True
        assert reasoner.is_entailed(owl.SubClassOf(_class("A"), _class("B"))).value is True
        assert reasoner.types(_individual("i")).value


def test_fresh_entity_policy_and_quiet_inconsistency_precedence() -> None:
    empty = _snapshot()
    fresh_class = _class("fresh")
    fresh_property = _property("fresh-property")
    fresh_individual = _individual("fresh-individual")
    with Reasoner(empty, ReasonerConfig(backend="python")) as reasoner:
        assert reasoner.equivalent_classes(fresh_class).value[0].members == (fresh_class,)
        assert reasoner.superclasses(fresh_class, direct=True).value == (
            reasoner.classify().value.top,
        )
        assert reasoner.subclasses(fresh_class, direct=True).value == (
            reasoner.classify().value.bottom,
        )
        assert reasoner.equivalent_object_properties(fresh_property).value.members == (
            fresh_property,
        )
        assert reasoner.types(fresh_individual, direct=True).value == (
            reasoner.classify().value.top,
        )
        assert fresh_class not in reasoner.all_classes()

    denied = Reasoner(
        empty,
        ReasonerConfig(backend="python", allow_fresh_entities=False),
    )
    with pytest.raises(FreshEntityError) as caught:
        denied.is_satisfiable(
            owl.ObjectIntersectionOf(owl.CanonicalSet((_class("z"), _class("a"))))
        )
    assert caught.value.entities == tuple(
        sorted((_class("a"), _class("z")), key=owl.canonical_bytes)
    )
    with pytest.raises(FreshEntityError):
        denied.types(fresh_individual)
    with pytest.raises(FreshEntityError):
        denied.equivalent_object_properties(fresh_property)
    denied.close()

    inconsistent = Reasoner(
        _snapshot("SubClassOf(owl:Thing owl:Nothing)"),
        ReasonerConfig(backend="python", allow_fresh_entities=False),
    )
    collapsed = inconsistent.classify().value
    assert inconsistent.is_satisfiable(fresh_class).value is False
    assert inconsistent.equivalent_classes(fresh_class).value == (collapsed.top,)
    assert inconsistent.types(fresh_individual).value == (collapsed.top,)
    assert inconsistent.equivalent_object_properties(fresh_property).value == (
        inconsistent.classify_object_properties().value.top
    )
    inconsistent.close()


def test_unsupported_and_incomplete_import_policies_attach_exact_metadata() -> None:
    unsupported = _snapshot("SubClassOf(ObjectAllValuesFrom(:p :A) :B)")
    with Reasoner(unsupported, ReasonerConfig(backend="python")) as reasoner:
        result = reasoner.classify()
        assert result.complete is False
        assert any("OBJECT_ALL_VALUES_FROM" in issue.features for issue in result.reasons)
        with pytest.raises(IncompleteReasoningError) as caught:
            result.require_complete()
        assert caught.value.reasons == result.reasons
    with pytest.raises(UnsupportedFeatureError):
        Reasoner(
            unsupported,
            ReasonerConfig(backend="python", unsupported="error"),
        )

    incomplete = owl.load_snapshot(
        b"Ontology(<urn:imports> Import(<urn:missing>))",
        options=_OPTIONS,
    )
    assert incomplete.is_complete is False
    with pytest.raises(owl.UnresolvedImportError):
        Reasoner(incomplete, ReasonerConfig(backend="python"))
    with Reasoner(
        incomplete,
        ReasonerConfig(backend="python", allow_incomplete_imports=True),
    ) as reasoner:
        consistency = reasoner.is_consistent()
        taxonomy = reasoner.classify()
        assert consistency.complete is taxonomy.complete is False
        assert consistency.reasons[0].features == ("PYELK_IGNORED_IMPORT",)
        assert taxonomy.reasons[0].features == ("PYELK_IGNORED_IMPORT",)


def test_malformed_backend_taxonomy_is_rejected_without_id_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Hostile(_DelegatingSession):
        def class_taxonomy(self) -> RawTaxonomy:
            value = self.delegate.class_taxonomy()
            object.__setattr__(value, "nodes", tuple(reversed(value.nodes)))
            return value

    def factory(compiled: CompiledOntology, config: ReasonerConfig) -> BackendSession:
        return Hostile(_python_session(compiled, config))

    monkeypatch.setattr("pyelk.api.create_backend_session", factory)
    reasoner = Reasoner(
        _snapshot("Declaration(Class(:A))"),
        ReasonerConfig(backend="python"),
    )
    with pytest.raises(BackendProtocolError, match=r"RawTaxonomy|taxonomy"):
        reasoner.classify()
    reasoner.close()


def test_malformed_backend_diagnostics_are_rejected_without_value_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Hostile(_DelegatingSession):
        def diagnostics(self) -> Mapping[str, DiagnosticScalar]:
            return cast(Mapping[str, DiagnosticScalar], {"ingestion_path": object()})

    def factory(compiled: CompiledOntology, config: ReasonerConfig) -> BackendSession:
        return Hostile(_python_session(compiled, config))

    monkeypatch.setattr("pyelk.api.create_backend_session", factory)
    reasoner = Reasoner(_snapshot(), ReasonerConfig(backend="python"))
    with pytest.raises(BackendProtocolError, match="string-to-scalar backend diagnostics"):
        reasoner.diagnostics()
    reasoner.close()


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"ingestion_path": "private-native"}, "public ingestion path"),
        (
            {"ingestion_path": "scalar-wire"},
            "ingestion path consistent with the selected backend",
        ),
        ({"compiler_digest": "not-a-digest"}, "scalar compiler digest"),
        ({"compiler_cache_schema_version": True}, "compiler_cache_schema_version"),
        ({"encoded_buffer_count": False}, "exact scalar encoded_buffer_count"),
        ({"encoded_validation_seconds": float("nan")}, "no scalar"),
        ({"native_abi_version": ""}, "native ABI version"),
        ({"native_abi_version": "abi3-py310"}, "no native ABI"),
        ({"encoded_view_publication_seconds": 0.25}, "no encoded-view publication"),
        ({"materialized_scalar_rows": -1}, "scalar materialization row count"),
    ],
)
def test_public_diagnostics_reject_malformed_known_handoff_fields(
    monkeypatch: pytest.MonkeyPatch,
    override: dict[str, DiagnosticScalar],
    message: str,
) -> None:
    class Hostile(_DelegatingSession):
        def diagnostics(self) -> Mapping[str, DiagnosticScalar]:
            values = dict(self.delegate.diagnostics())
            values.update(override)
            return values

    def factory(compiled: CompiledOntology, config: ReasonerConfig) -> BackendSession:
        return Hostile(_python_session(compiled, config))

    monkeypatch.setattr("pyelk.api.create_backend_session", factory)
    reasoner = Reasoner(_snapshot(), ReasonerConfig(backend="python"))
    with pytest.raises(BackendProtocolError, match=message):
        reasoner.diagnostics()
    reasoner.close()


def test_facade_lock_serializes_calls_and_close_waits_for_inflight_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    class Blocking(_DelegatingSession):
        def is_inconsistent(self) -> bool:
            entered.set()
            assert release.wait(5)
            return self.delegate.is_inconsistent()

        def close(self) -> None:
            super().close()
            closed.set()

    def factory(compiled: CompiledOntology, config: ReasonerConfig) -> BackendSession:
        return Blocking(_python_session(compiled, config))

    monkeypatch.setattr("pyelk.api.create_backend_session", factory)
    reasoner = Reasoner(_snapshot(), ReasonerConfig(backend="python"))
    errors: list[BaseException] = []

    def classify() -> None:
        try:
            reasoner.is_consistent()
        except BaseException as error:  # pragma: no cover - asserted empty below
            errors.append(error)

    worker = threading.Thread(target=classify)
    closer = threading.Thread(target=reasoner.close)
    worker.start()
    assert entered.wait(5)
    closer.start()
    assert not closed.wait(0.05)
    release.set()
    worker.join(5)
    closer.join(5)
    assert not worker.is_alive() and not closer.is_alive()
    assert errors == [] and closed.is_set()
    with pytest.raises(ReasonerClosedError):
        reasoner.is_consistent()


def test_close_is_idempotent_terminal_and_returned_values_survive() -> None:
    reasoner = Reasoner(
        _snapshot("Declaration(Class(:A))"),
        ReasonerConfig(backend="python"),
    )
    taxonomy = reasoner.classify().value
    reasoner.close()
    reasoner.close()
    assert taxonomy.node(_class("A")) is not None
    operations = (
        lambda: reasoner.backend,
        lambda: reasoner.ontology,
        reasoner.diagnostics,
        reasoner.is_consistent,
        reasoner.classify,
        reasoner.realize,
        reasoner.all_classes,
    )
    for operation in operations:
        with pytest.raises(ReasonerClosedError):
            operation()
