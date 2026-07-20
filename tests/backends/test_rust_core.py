from __future__ import annotations

import gc
import importlib.util
import shutil
import sys
import weakref
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from pyelk import Reasoner, ReasonerConfig
from pyelk.indexing.codec import OntologySection
from tests.integration.test_pure_reasoner import (
    _CLASS_CASES,
    _CLASS_QUERY_CASES,
    _ENTAILMENT_CASES,
    _EXPECTED,
    _PROPERTY_CASES,
    _REALIZATION_CASES,
    _UPSTREAM,
    _parse_axiom,
    _parse_expression,
    _payload,
    _query_snapshot,
    _snapshot,
)


def _native_library() -> Path:
    root = Path(__file__).parents[2]
    installed = importlib.util.find_spec("pyelk._native")
    if installed is not None and installed.origin is not None:
        candidate = Path(installed.origin).resolve()
        if root not in candidate.parents and candidate.is_file():
            return candidate
    candidates = (
        root / "target" / "release" / "lib_native.dylib",
        root / "target" / "release" / "lib_native.so",
        root / "target" / "debug" / "lib_native.dylib",
        root / "target" / "debug" / "lib_native.so",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    pytest.skip("build the pyelk-pyo3 crate before running native-backend tests")


@pytest.fixture(scope="session", autouse=True)
def native_module(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ModuleType]:
    """Load the workspace extension without installing it into the source tree."""

    destination = tmp_path_factory.mktemp("pyelk-native") / "_native.so"
    shutil.copy2(_native_library(), destination)
    spec = importlib.util.spec_from_file_location("pyelk._native", destination)
    if spec is None or spec.loader is None:
        pytest.fail(f"could not create an import spec for {destination}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["pyelk._native"] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        if sys.modules.get("pyelk._native") is module:
            del sys.modules["pyelk._native"]


def _reasoners(snapshot: object, *, workers: int = 1) -> tuple[Reasoner, Reasoner]:
    return (
        Reasoner(snapshot, ReasonerConfig(backend="python", workers=workers)),
        Reasoner(snapshot, ReasonerConfig(backend="rust", workers=workers)),
    )


def _direct_encoded_snapshot(source: bytes) -> tuple[Any, Any]:
    import pyowl_core as owl

    native_views = pytest.importorskip("pyowl_core.backends.native_views")
    produce = getattr(native_views, "produce_encoded_structural_view_v1", None)
    if not callable(produce):
        pytest.skip("installed pyowl-core does not provide structural-columns v1")
    options = owl.LoadOptions(backend=owl.BackendPreference.PYTHON)
    document = owl.parse_document(source, format=owl.DocumentFormat.FUNCTIONAL, options=options)
    snapshot = owl.load_snapshot(
        document,
        options=owl.LoadOptions(
            backend=owl.BackendPreference.PYTHON,
            imports=owl.ImportPolicy.IGNORE,
        ),
    )
    return snapshot, produce(snapshot)


def _encoded_wrapper(encoded: Any, **changes: object) -> SimpleNamespace:
    values = {
        name: getattr(encoded, name)
        for name in (
            "schema_name",
            "schema_version",
            "model_schema",
            "owner",
            "buffers",
            "descriptor",
            "structural_fingerprint",
            "segments",
            "scope",
            "document_key",
        )
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_native_handshake_and_defensive_decoder(native_module: ModuleType) -> None:
    assert native_module.abi_version() == "abi3-py310"
    assert native_module.implementation_version() == "0.1.0.dev0"
    assert native_module.ir_version() == (1, 0)
    assert native_module.self_check() is True
    assert native_module.encoded_view_schemas() == {}
    with pytest.raises(ValueError, match=r"IR|payload|header|magic"):
        native_module.create_session(b"not a compiled ontology", 1)


def test_hidden_direct_encoded_session_matches_scalar_wire(native_module: ModuleType) -> None:
    from pyelk.indexing.compiler import compile_ontology

    snapshot, encoded = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded#>) Ontology(<urn:encoded>
        Declaration(Class(:A))
        Declaration(Class(:B))
        Declaration(Class(:C))
        Declaration(Class(:D))
        Declaration(ObjectProperty(:p))
        Declaration(ObjectProperty(:q))
        Declaration(NamedIndividual(:i))
        Declaration(NamedIndividual(:j))
        SubClassOf(:A :B)
        SubClassOf(:B ObjectSomeValuesFrom(:p :C))
        SubClassOf(
          ObjectIntersectionOf(:A ObjectSomeValuesFrom(:p ObjectComplementOf(:B)) :C)
          ObjectUnionOf(ObjectSomeValuesFrom(:q :D) ObjectOneOf(:i :j))
        )
        ClassAssertion(:A :i)
        ObjectPropertyDomain(:p :A)
        ObjectPropertyRange(:p :C)
        )"""
    )
    direct = native_module.create_session_from_encoded(encoded, 1, "error")
    scalar = native_module.create_session(
        compile_ontology(snapshot, unsupported="error").encode(),
        1,
    )
    try:
        diagnostics = direct.diagnostics()
        assert diagnostics["encoded_buffer_count"] == 11
        assert diagnostics["encoded_buffer_bytes"] == sum(
            value.nbytes for value in encoded.buffers.values()
        )
        assert diagnostics["encoded_zero_copy_buffers"] == 11
        assert diagnostics["encoded_indexed_buffer_count"] == 0
        assert diagnostics["encoded_staging_copy_bytes"] == 0
        assert diagnostics["encoded_private_ir_bytes"] == 0
        for operation in (
            "is_inconsistent",
            "class_taxonomy",
            "object_property_taxonomy",
            "realization",
        ):
            assert getattr(direct, operation)() == getattr(scalar, operation)()
        assert direct.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
    finally:
        direct.close()
        scalar.close()


def test_hidden_encoded_session_rolls_back_ignored_axioms(native_module: ModuleType) -> None:
    from pyelk.indexing.compiler import compile_ontology
    from pyelk.indexing.conversion import FEATURE_INDEX

    snapshot, encoded = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-transaction#>) Ontology(<urn:encoded-transaction>
        Declaration(Class(:A))
        Declaration(Class(:B))
        Declaration(Class(:C))
        Declaration(Class(:D))
        Declaration(ObjectProperty(:p))
        Declaration(ObjectProperty(:q))
        Declaration(NamedIndividual(:i))
        SubClassOf(ObjectIntersectionOf(:A ObjectAllValuesFrom(:p :B)) :C)
        SubClassOf(:A ObjectOneOf(:i _:anonymous))
        EquivalentObjectProperties(:p ObjectInverseOf(:q))
        SubObjectPropertyOf(ObjectPropertyChain(:p ObjectInverseOf(:q)) :p)
        SubClassOf(:D :A)
        )"""
    )
    compiled = compile_ontology(snapshot, unsupported="ignore")
    assert compiled.feature_counts[FEATURE_INDEX["ANONYMOUS_INDIVIDUAL"]] == 1
    assert compiled.feature_counts[FEATURE_INDEX["OBJECT_ALL_VALUES_FROM"]] == 1
    assert compiled.feature_counts[FEATURE_INDEX["OBJECT_INVERSE_OF"]] == 2
    assert compiled.feature_counts[FEATURE_INDEX["OBJECT_ONE_OF"]] == 0

    direct = native_module.create_session_from_encoded(encoded, 1, "ignore")
    scalar = native_module.create_session(compiled.encode(), 1)
    try:
        assert direct.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
    finally:
        direct.close()
        scalar.close()

    with pytest.raises(ValueError, match=r"unsupported ELK feature"):
        native_module.create_session_from_encoded(encoded, 1, "error")


def test_hidden_direct_encoded_general_class_axioms_match_scalar(
    native_module: ModuleType,
) -> None:
    from pyelk.indexing.compiler import compile_ontology

    snapshot, encoded = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-general#>) Ontology(<urn:encoded-general>
        Declaration(Class(:A))
        Declaration(Class(:B))
        Declaration(Class(:C))
        Declaration(Class(:D))
        Declaration(Class(:E))
        Declaration(ObjectProperty(:p))
        Declaration(NamedIndividual(:i))
        EquivalentClasses(
          ObjectIntersectionOf(:A :B)
          ObjectSomeValuesFrom(:p ObjectComplementOf(:C))
          :D
        )
        DisjointClasses(ObjectUnionOf(:A :B) ObjectSomeValuesFrom(:p :C))
        DisjointClasses(:A ObjectComplementOf(:B) ObjectSomeValuesFrom(:p :C))
        DisjointUnion(
          :E
          ObjectIntersectionOf(:A :B)
          ObjectSomeValuesFrom(:p :C)
        )
        ClassAssertion(ObjectUnionOf(:A ObjectHasSelf(:p)) :i)
        ObjectPropertyDomain(:p ObjectIntersectionOf(:A :B))
        ObjectPropertyRange(:p ObjectUnionOf(:C ObjectComplementOf(:D)))
        )"""
    )
    direct = native_module.create_session_from_encoded(encoded, 1, "error")
    scalar = native_module.create_session(
        compile_ontology(snapshot, unsupported="error").encode(),
        1,
    )
    try:
        assert direct.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
    finally:
        direct.close()
        scalar.close()


def test_hidden_encoded_session_deduplicates_annotated_axioms(
    native_module: ModuleType,
) -> None:
    from pyelk.indexing.compiler import compile_ontology
    from pyelk.indexing.conversion import FEATURE_INDEX

    snapshot, encoded = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-annotations#>) Ontology(<urn:encoded-annotations>
        Declaration(AnnotationProperty(:ap))
        Declaration(Class(:A))
        Declaration(Class(:B))
        Declaration(Class(:C))
        Declaration(ObjectProperty(:p))
        SubClassOf(Annotation(:ap "one") :A :B)
        SubClassOf(Annotation(:ap "two") :A :B)
        SubClassOf(:A :B)
        EquivalentClasses(Annotation(:ap "one") :B :C)
        EquivalentClasses(Annotation(:ap "two") :B :C)
        FunctionalObjectProperty(Annotation(:ap "one") :p)
        FunctionalObjectProperty(Annotation(:ap "two") :p)
        )"""
    )
    compiled = compile_ontology(snapshot, unsupported="ignore")
    assert compiled.feature_counts[FEATURE_INDEX["FUNCTIONAL_OBJECT_PROPERTY"]] == 1
    direct = native_module.create_session_from_encoded(encoded, 1, "ignore")
    scalar = native_module.create_session(compiled.encode(), 1)
    try:
        assert direct.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
    finally:
        direct.close()
        scalar.close()


def test_hidden_encoded_session_rejects_hostile_envelopes_before_publication(
    native_module: ModuleType,
) -> None:
    _snapshot_value, encoded = _direct_encoded_snapshot(
        b"Ontology(Declaration(Class(<urn:encoded:A>)))"
    )
    buffers = dict(encoded.buffers)
    buffers["root_kinds"] = memoryview(bytearray(buffers["root_kinds"]))
    cases = (
        _encoded_wrapper(encoded, descriptor=encoded.descriptor + b" "),
        _encoded_wrapper(encoded, segments=()),
        _encoded_wrapper(encoded, buffers=buffers),
    )
    for candidate in cases:
        with pytest.raises(ValueError, match=r"encoded|descriptor|segment|buffer"):
            native_module.create_session_from_encoded(candidate, 1, "error")


def test_hidden_encoded_session_retains_owner_until_close(native_module: ModuleType) -> None:
    snapshot, encoded = _direct_encoded_snapshot(
        b"Ontology(Declaration(Class(<urn:encoded:retained>)))"
    )
    encoded_ref = weakref.ref(encoded)
    session = native_module.create_session_from_encoded(encoded, 1, "error")
    del snapshot, encoded
    gc.collect()
    assert encoded_ref() is not None
    session.close()
    gc.collect()
    assert encoded_ref() is None


def test_native_session_close_is_idempotent_and_terminal(native_module: ModuleType) -> None:
    from tests.helpers.contracts import TinyCompiledOntologyBuilder

    session = native_module.create_session(TinyCompiledOntologyBuilder().build().encode(), 1)
    session.close()
    session.close()
    with pytest.raises(RuntimeError, match="closed"):
        session.class_taxonomy()


def test_native_query_decoder_rejects_corruption(native_module: ModuleType) -> None:
    from tests.helpers.contracts import TinyCompiledOntologyBuilder

    session = native_module.create_session(TinyCompiledOntologyBuilder().build().encode(), 1)
    try:
        for payload in (b"", b"not a query IR", b"PYELKQ\0\0"):
            with pytest.raises(ValueError):
                session.query_class_expression(payload, 0, False)
            with pytest.raises(ValueError):
                session.entails(payload)
        with pytest.raises(ValueError, match="query kind"):
            session.query_class_expression(None, 255, False)
    finally:
        session.close()


def test_none_query_fallbacks_match_python(native_module: ModuleType) -> None:
    from pyelk.backends.python import PythonBackendSession
    from pyelk.reasoning.contracts import QueryKind
    from pyelk.reasoning.wire import decode_raw_query_result
    from tests.helpers.contracts import TinyCompiledOntologyBuilder

    compiled = TinyCompiledOntologyBuilder().add_class("urn:fallback:A").build()
    native = native_module.create_session(compiled.encode(), 1)
    python = PythonBackendSession(
        compiled,
        requested_workers=1,
        native_available=True,
        fallback_reason=None,
    )
    try:
        for kind in QueryKind:
            for direct in (False, True):
                actual = decode_raw_query_result(
                    native.query_class_expression(None, int(kind), direct)
                )
                assert actual == python.query_class_expression(None, kind, direct)
        assert native.entails(None) is python.entails(None) is False
    finally:
        native.close()
        python.close()


@pytest.mark.parametrize(
    "case",
    (
        "magic",
        "major",
        "checksum",
        "truncated",
        "noncontiguous",
        "oversized-count",
        "entity-enum",
        "expression-id",
        "utf8",
        "csr-offset",
        "unknown-required-section",
    ),
)
def test_native_decoder_rejects_frozen_corrupt_input_families(
    native_module: ModuleType, case: str
) -> None:
    from tests.unit.indexing.test_codec import (
        _HEADER,
        _U64,
        _empty_ontology_bytes,
        _refresh_checksum,
        _section_location,
        _with_extra_section,
    )

    encoded = bytearray(_empty_ontology_bytes())
    if case == "magic":
        encoded[0] ^= 0xFF
    elif case == "major":
        encoded[8:10] = (2).to_bytes(2, "little")
    elif case == "checksum":
        encoded[-1] ^= 0xFF
    elif case == "truncated":
        del encoded[-10:]
    elif case == "noncontiguous":
        offset_field = _HEADER.size + 2
        current = int.from_bytes(encoded[offset_field : offset_field + 8], "little")
        encoded[offset_field : offset_field + 8] = (current + 1).to_bytes(8, "little")
    elif case == "oversized-count":
        count_field = _HEADER.size + 2 + 8 + 8
        encoded[count_field : count_field + 8] = _U64.pack(2**63)
    elif case == "entity-enum":
        offset, _, _ = _section_location(encoded, OntologySection.ENTITY_KINDS)
        encoded[offset] = 0xFF
        _refresh_checksum(encoded)
    elif case == "expression-id":
        offset, _, _ = _section_location(encoded, OntologySection.EXPRESSION_ARGUMENTS)
        encoded[offset : offset + 4] = (0xFFFF_FFFF).to_bytes(4, "little")
        _refresh_checksum(encoded)
    elif case == "utf8":
        offset, _, _ = _section_location(encoded, OntologySection.ENTITY_IRI_BYTES)
        encoded[offset] = 0xFF
        _refresh_checksum(encoded)
    elif case == "csr-offset":
        offset, _, count = _section_location(encoded, OntologySection.PROPERTY_CHAIN_OFFSETS)
        final_offset = offset + count * _U64.size
        encoded[final_offset : final_offset + _U64.size] = _U64.pack(999)
        _refresh_checksum(encoded)
    else:
        encoded = bytearray(_with_extra_section(bytes(encoded), 21))
    with pytest.raises(ValueError):
        native_module.create_session(bytes(encoded), 1)


@pytest.mark.parametrize("name", _CLASS_CASES)
def test_frozen_class_taxonomy_matches_python(name: str) -> None:
    snapshot = _snapshot(_UPSTREAM / "classification" / f"{name}.owl")
    python, rust = _reasoners(snapshot)
    with python, rust:
        assert rust.classify() == python.classify()
        assert rust.is_consistent() == python.is_consistent()


@pytest.mark.parametrize("name", _PROPERTY_CASES)
def test_frozen_object_property_taxonomy_matches_python(name: str) -> None:
    snapshot = _snapshot(_UPSTREAM / "classification" / "object_property" / f"{name}.owl")
    python, rust = _reasoners(snapshot)
    with python, rust:
        assert rust.classify_object_properties() == python.classify_object_properties()


@pytest.mark.parametrize("name", _REALIZATION_CASES)
def test_frozen_realization_matches_python(name: str) -> None:
    snapshot = _snapshot(_UPSTREAM / "realization" / f"{name}.owl")
    python, rust = _reasoners(snapshot)
    with python, rust:
        assert rust.realize() == python.realize()


@pytest.mark.parametrize("name", _CLASS_QUERY_CASES)
def test_frozen_class_queries_match_python_for_direct_and_transitive(name: str) -> None:
    expected_rows = _payload(_EXPECTED / "query" / "class" / f"{name}.json")["result"]["value"][
        "queries"
    ]
    python, rust = _reasoners(_query_snapshot(name))
    with python, rust:
        for row in expected_rows:
            expression = _parse_expression(name, row["expression"])
            assert rust.is_satisfiable(expression) == python.is_satisfiable(expression)
            assert rust.equivalent_classes(expression) == python.equivalent_classes(expression)
            for direct in (False, True):
                assert rust.subclasses(expression, direct=direct) == python.subclasses(
                    expression, direct=direct
                )
                assert rust.superclasses(expression, direct=direct) == python.superclasses(
                    expression, direct=direct
                )
                assert rust.instances(expression, direct=direct) == python.instances(
                    expression, direct=direct
                )


@pytest.mark.parametrize("name", _ENTAILMENT_CASES)
def test_frozen_entailment_queries_match_python(name: str) -> None:
    expected_rows = _payload(_EXPECTED / "query" / "entailment" / f"{name}.json")["result"][
        "value"
    ]["queries"]
    snapshot = _snapshot(_UPSTREAM / "query" / "entailment" / f"{name}.owl")
    python, rust = _reasoners(snapshot)
    with python, rust:
        for row in expected_rows:
            axiom = _parse_axiom(row["axiom"])
            assert rust.is_entailed(axiom) == python.is_entailed(axiom)


@pytest.mark.parametrize("workers", (0, 1, 2, 4))
def test_worker_count_does_not_change_results(workers: int) -> None:
    snapshot = _snapshot(_UPSTREAM / "classification" / "Existentials.owl")
    baseline = Reasoner(snapshot, ReasonerConfig(backend="python", workers=1))
    candidate = Reasoner(snapshot, ReasonerConfig(backend="rust", workers=workers))
    with baseline, candidate:
        assert candidate.classify() == baseline.classify()
        assert candidate.classify_object_properties() == baseline.classify_object_properties()
        assert candidate.realize() == baseline.realize()


def test_reused_worker_workspace_drops_root_local_range_facts() -> None:
    import pyowl_core as owl

    from tests.unit.indexing._support import load_functional

    snapshot = load_functional(
        "SubClassOf(:A ObjectSomeValuesFrom(:p :X)) "
        "ObjectPropertyRange(:p :R) "
        "SubClassOf(:B ObjectSomeValuesFrom(:q :X)) "
        "SubClassOf(ObjectSomeValuesFrom(:q ObjectIntersectionOf(:X :R)) :Leaked)",
        ontology_iri="urn:worker-workspace-isolation",
    )
    python, rust = _reasoners(snapshot, workers=1)
    with python, rust:
        assert rust.classify() == python.classify()
        leaked = owl.Class(owl.IRI("urn:test#Leaked"))
        assert all(
            leaked not in node.members
            for node in rust.superclasses(owl.Class(owl.IRI("urn:test#B"))).value
        )
