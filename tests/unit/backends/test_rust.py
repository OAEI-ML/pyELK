from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

import pyowl_core as owl
import pytest

from pyelk.backends.python import IMPLEMENTATION_VERSION, PythonBackendFactory
from pyelk.backends.rust import RustBackendFactory, RustBackendSession
from pyelk.config import ReasonerConfig
from pyelk.exceptions import (
    BackendProtocolError,
    InternalReasonerError,
    ReasonerClosedError,
    UnsupportedFeatureError,
)
from pyelk.indexing.codec import SCHEMA_MAJOR, SCHEMA_MINOR
from pyelk.indexing.encoded import (
    ENCODED_BUFFER_WIDTHS,
    ENCODED_DESCRIPTOR_SHA256,
    ENCODED_SCHEMA_NAME,
    ENCODED_SCHEMA_VERSION,
    EncodedStructuralHandoff,
)
from pyelk.indexing.ir import CompiledOntology
from pyelk.indexing.metadata import encode_compiler_metadata, metadata_from_compiled
from pyelk.reasoning.contracts import BackendConfig, QueryKind
from pyelk.reasoning.wire import (
    encode_raw_query_result,
    encode_raw_realization,
    encode_raw_taxonomy,
)
from tests.helpers.contracts import TinyCompiledOntologyBuilder


class _NativeSession:
    def __init__(self, compiled: CompiledOntology) -> None:
        self.compiled = compiled
        self.python = PythonBackendFactory().create_session(
            compiled,
            cast(BackendConfig, ReasonerConfig()),
        )
        self.closed = False
        self.metadata_calls = 0

    def close(self) -> None:
        self.closed = True
        self.python.close()

    def is_inconsistent(self) -> bool:
        return self.python.is_inconsistent()

    def class_taxonomy(self) -> bytes:
        return encode_raw_taxonomy(self.python.class_taxonomy())

    def object_property_taxonomy(self) -> bytes:
        return encode_raw_taxonomy(self.python.object_property_taxonomy())

    def realization(self) -> bytes:
        return encode_raw_realization(self.python.realization())

    def query_class_expression(self, encoded: bytes | None, kind: int, direct: bool) -> bytes:
        return encode_raw_query_result(
            self.python.query_class_expression(encoded, QueryKind(kind), direct)
        )

    def entails(self, encoded: bytes | None) -> bool:
        return self.python.entails(encoded)

    def compiler_metadata(self) -> bytes:
        self.metadata_calls += 1
        return encode_compiler_metadata(metadata_from_compiled(self.compiled))

    def diagnostics(self) -> Mapping[str, int | float | str | bool]:
        return {"native": True, "calls": 1}


class _NativeModule:
    def __init__(self, compiled: CompiledOntology) -> None:
        self.compiled = compiled
        self.payload: bytes | None = None
        self.workers: int | None = None

    def create_session(self, payload: bytes, workers: int) -> _NativeSession:
        self.payload = payload
        self.workers = workers
        return _NativeSession(self.compiled)


class _EncodedNativeModule(_NativeModule):
    class NativeUnsupportedFeatureError(ValueError):
        pass

    def __init__(self, compiled: CompiledOntology) -> None:
        super().__init__(compiled)
        self.encoded: object | None = None
        self.unsupported: str | None = None
        self.create_error: Exception | None = None

    def encoded_view_schemas(self) -> Mapping[str, int]:
        return {ENCODED_SCHEMA_NAME: ENCODED_SCHEMA_VERSION}

    def create_session_from_encoded(
        self,
        encoded: object,
        workers: int,
        unsupported: str,
    ) -> _NativeSession:
        if self.create_error is not None:
            raise self.create_error
        self.encoded = encoded
        self.workers = workers
        self.unsupported = unsupported
        return _NativeSession(self.compiled)


def _session() -> tuple[RustBackendSession, _NativeModule]:
    compiled = TinyCompiledOntologyBuilder().add_class("urn:rust#A").build()
    native = _NativeModule(compiled)
    session = RustBackendFactory(
        native,
        implementation_version=IMPLEMENTATION_VERSION,
        ir_major=SCHEMA_MAJOR,
        ir_minor=SCHEMA_MINOR,
        abi="abi3-py310",
    ).create_session(compiled, cast(BackendConfig, ReasonerConfig(workers=3)))
    return session, native


def test_rust_adapter_transfers_ir_once_and_decodes_every_result() -> None:
    session, native = _session()
    assert native.payload is not None and native.workers == 3
    assert session.info.name == "rust"
    assert getattr(session.info, "compiler_handoff", None) is None
    assert session.is_inconsistent() is False
    assert session.class_taxonomy().nodes
    assert session.object_property_taxonomy().nodes
    assert session.realization().class_taxonomy.nodes
    assert session.query_class_expression(None, QueryKind.SATISFIABLE, False).boolean is True
    assert session.entails(None) is False
    assert session.diagnostics() == {
        "calls": 1,
        "ingestion_path": "scalar-wire",
        "native": True,
        "native_abi_version": "abi3-py310",
    }
    session.close()
    session.close()
    with pytest.raises(ReasonerClosedError):
        session.is_inconsistent()


def test_rust_adapter_decodes_compiler_metadata_lazily_once() -> None:
    session, _native = _session()
    native_session = session._native
    expected = metadata_from_compiled(native_session.compiled)

    assert native_session.metadata_calls == 0
    assert session.compiler_metadata() == expected
    assert session.compiler_metadata() is session.compiler_metadata()
    assert native_session.metadata_calls == 1

    session.close()
    with pytest.raises(ReasonerClosedError):
        session.compiler_metadata()


def test_rust_adapter_rejects_wrong_payload_and_maps_native_failure() -> None:
    session, _native = _session()
    native_session = session._native
    native_session.class_taxonomy = lambda: b"not-a-wire-value"
    with pytest.raises(BackendProtocolError):
        session.class_taxonomy()

    def panic() -> bool:
        raise RuntimeError("panic caught at boundary")

    native_session.is_inconsistent = panic
    with pytest.raises(InternalReasonerError, match="panic caught"):
        session.is_inconsistent()


def test_encoded_factory_uses_one_coarse_call_and_retains_owner_until_close() -> None:
    compiled = TinyCompiledOntologyBuilder().add_class("urn:rust#Encoded").build()
    native = _EncodedNativeModule(compiled)
    owner = object()
    encoded = object()
    fingerprint = owl.Fingerprint("sha256", 1, b"s" * 32)
    handoff = EncodedStructuralHandoff(
        encoded_view=encoded,
        owner=cast(owl.OntologyView, owner),
        schema_name=ENCODED_SCHEMA_NAME,
        schema_version=ENCODED_SCHEMA_VERSION,
        model_schema=1,
        scope=owl.AxiomScope.CLOSURE,
        descriptor=b"descriptor",
        descriptor_digest=ENCODED_DESCRIPTOR_SHA256,
        buffers=MappingProxyType({"column": memoryview(b"value")}),
        segments=(),
        structural_fingerprint=fingerprint,
    )
    factory = RustBackendFactory(
        native,
        implementation_version=IMPLEMENTATION_VERSION,
        ir_major=SCHEMA_MAJOR,
        ir_minor=SCHEMA_MINOR,
    )
    assert factory.encoded_view_schemas == {ENCODED_SCHEMA_NAME: 1}
    session = factory.create_encoded_session(
        handoff,
        cast(BackendConfig, ReasonerConfig(workers=2, unsupported="error")),
    )
    assert native.payload is None
    assert native.encoded is encoded
    assert native.workers == 2
    assert native.unsupported == "error"
    assert session._encoded_owner is handoff
    assert session.info.compiler_handoff == {
        "buffer_widths": dict(ENCODED_BUFFER_WIDTHS),
        "descriptor_sha256": ENCODED_DESCRIPTOR_SHA256.hex(),
        "model_schema": 1,
        "schema_name": ENCODED_SCHEMA_NAME,
        "schema_version": ENCODED_SCHEMA_VERSION,
    }
    with pytest.raises(TypeError):
        session.info.compiler_handoff["schema_name"] = "mutated"  # type: ignore[index]
    assert session.diagnostics()["ingestion_path"] == "encoded-native"
    session.close()
    assert session._encoded_owner is None


def test_encoded_factory_preserves_unsupported_and_protocol_error_categories() -> None:
    compiled = TinyCompiledOntologyBuilder().build()
    native = _EncodedNativeModule(compiled)
    owner = cast(owl.OntologyView, object())
    handoff = EncodedStructuralHandoff(
        encoded_view=object(),
        owner=owner,
        schema_name=ENCODED_SCHEMA_NAME,
        schema_version=ENCODED_SCHEMA_VERSION,
        model_schema=1,
        scope=owl.AxiomScope.CLOSURE,
        descriptor=b"descriptor",
        descriptor_digest=b"d" * 32,
        buffers=MappingProxyType({"column": memoryview(b"value")}),
        segments=(),
        structural_fingerprint=owl.Fingerprint("sha256", 1, b"s" * 32),
    )
    factory = RustBackendFactory(
        native,
        implementation_version=IMPLEMENTATION_VERSION,
        ir_major=SCHEMA_MAJOR,
        ir_minor=SCHEMA_MINOR,
    )
    config = cast(BackendConfig, ReasonerConfig(unsupported="error"))

    native.create_error = native.NativeUnsupportedFeatureError("FUNCTIONAL_OBJECT_PROPERTY")
    with pytest.raises(UnsupportedFeatureError) as unsupported:
        factory.create_encoded_session(handoff, config)
    assert unsupported.value.feature == "FUNCTIONAL_OBJECT_PROPERTY"
    assert unsupported.value.axiom is owner

    native.create_error = ValueError("corrupt structural column")
    with pytest.raises(BackendProtocolError, match="valid encoded structural"):
        factory.create_encoded_session(handoff, config)


@pytest.mark.parametrize(
    "native",
    [
        object(),
        type(
            "SchemasOnly",
            (),
            {"encoded_view_schemas": lambda self: {ENCODED_SCHEMA_NAME: 1}},
        )(),
        type(
            "CreateOnly",
            (),
            {"create_session_from_encoded": lambda self, view, workers, unsupported: object()},
        )(),
    ],
)
def test_native_encoded_entry_points_are_paired_and_validated(native: object) -> None:
    if type(native) is object:
        factory = RustBackendFactory(
            cast(_NativeModule, native),
            implementation_version=IMPLEMENTATION_VERSION,
            ir_major=SCHEMA_MAJOR,
            ir_minor=SCHEMA_MINOR,
        )
        assert factory.encoded_view_schemas == {}
        return
    with pytest.raises(BackendProtocolError, match="paired callable"):
        RustBackendFactory(
            cast(_NativeModule, native),
            implementation_version=IMPLEMENTATION_VERSION,
            ir_major=SCHEMA_MAJOR,
            ir_minor=SCHEMA_MINOR,
        )


def test_factory_does_not_build_core_columns_when_native_cannot_consume_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiled = TinyCompiledOntologyBuilder().build()
    factory = RustBackendFactory(
        _NativeModule(compiled),
        implementation_version=IMPLEMENTATION_VERSION,
        ir_major=SCHEMA_MAJOR,
        ir_minor=SCHEMA_MINOR,
    )
    ontology = owl.load_snapshot(b"Ontology()")

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("encoded core view was requested without a native compiler")

    monkeypatch.setattr("pyelk.backends.rust.negotiate_encoded_structural_view", forbidden)
    result = factory.negotiate_encoded_input(ontology)
    assert result.available is False
    assert "native extension" in (result.reason or "")
