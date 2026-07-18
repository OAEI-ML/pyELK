from __future__ import annotations

import importlib.util
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

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
    candidates = (
        root / "target" / "release" / "lib_native.dylib",
        root / "target" / "release" / "lib_native.so",
        root / "target" / "debug" / "lib_native.dylib",
        root / "target" / "debug" / "lib_native.so",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    installed = importlib.util.find_spec("pyelk._native")
    if installed is not None and installed.origin is not None:
        candidate = Path(installed.origin).resolve()
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


def test_native_handshake_and_defensive_decoder(native_module: ModuleType) -> None:
    assert native_module.abi_version() == "abi3-py310"
    assert native_module.implementation_version() == "0.1.0.dev0"
    assert native_module.ir_version() == (1, 0)
    assert native_module.self_check() is True
    with pytest.raises(ValueError, match=r"IR|payload|header|magic"):
        native_module.create_session(b"not a compiled ontology", 1)


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
