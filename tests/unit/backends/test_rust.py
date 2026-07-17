from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest

from pyelk.backends.python import IMPLEMENTATION_VERSION, PythonBackendFactory
from pyelk.backends.rust import RustBackendFactory, RustBackendSession
from pyelk.config import ReasonerConfig
from pyelk.exceptions import BackendProtocolError, InternalReasonerError, ReasonerClosedError
from pyelk.indexing.codec import SCHEMA_MAJOR, SCHEMA_MINOR
from pyelk.indexing.ir import CompiledOntology
from pyelk.reasoning.contracts import BackendConfig, QueryKind
from pyelk.reasoning.wire import (
    encode_raw_query_result,
    encode_raw_realization,
    encode_raw_taxonomy,
)
from tests.helpers.contracts import TinyCompiledOntologyBuilder


class _NativeSession:
    def __init__(self, compiled: CompiledOntology) -> None:
        self.python = PythonBackendFactory().create_session(
            compiled,
            cast(BackendConfig, ReasonerConfig()),
        )
        self.closed = False

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
    assert session.is_inconsistent() is False
    assert session.class_taxonomy().nodes
    assert session.object_property_taxonomy().nodes
    assert session.realization().class_taxonomy.nodes
    assert session.query_class_expression(None, QueryKind.SATISFIABLE, False).boolean is True
    assert session.entails(None) is False
    assert session.diagnostics() == {"calls": 1, "native": True}
    session.close()
    session.close()
    with pytest.raises(ReasonerClosedError):
        session.is_inconsistent()


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
