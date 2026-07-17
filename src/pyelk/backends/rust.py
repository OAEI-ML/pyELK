"""Thin defensive adapter over the private optional :mod:`pyelk._native` module."""

from __future__ import annotations

import os
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Protocol, cast

from pyelk.exceptions import BackendProtocolError, InternalReasonerError, ReasonerClosedError
from pyelk.indexing.ir import CompiledOntology
from pyelk.reasoning.contracts import (
    BackendConfig,
    BackendInfo,
    DiagnosticScalar,
    QueryKind,
    RawQueryResult,
    RawRealization,
    RawTaxonomy,
)
from pyelk.reasoning.wire import (
    decode_raw_query_result,
    decode_raw_realization,
    decode_raw_taxonomy,
)


class NativeModule(Protocol):
    """Private structural shape used by the adapter and fake-native tests."""

    def create_session(self, ir: bytes, workers: int) -> object: ...


class RustBackendFactory:
    """Create sessions from an already handshaken private extension module."""

    __slots__ = ("_abi", "_implementation_version", "_ir_major", "_ir_minor", "_native")

    def __init__(
        self,
        native: NativeModule,
        *,
        implementation_version: str,
        ir_major: int,
        ir_minor: int,
        abi: str | None = None,
    ) -> None:
        self._native = native
        self._implementation_version = implementation_version
        self._ir_major = ir_major
        self._ir_minor = ir_minor
        self._abi = abi

    def create_session(
        self, compiled: CompiledOntology, config: BackendConfig
    ) -> RustBackendSession:
        try:
            native_session = self._native.create_session(compiled.encode(), config.workers)
        except (MemoryError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as error:
            raise InternalReasonerError("create_session", "rust", str(error)) from error
        return RustBackendSession(
            native_session,
            implementation_version=self._implementation_version,
            ir_major=self._ir_major,
            ir_minor=self._ir_minor,
            requested_workers=config.workers,
        )


class RustBackendSession:
    """Decode and validate coarse native calls without leaking native values."""

    __slots__ = ("_closed", "_info", "_native")

    def __init__(
        self,
        native_session: object,
        *,
        implementation_version: str,
        ir_major: int,
        ir_minor: int,
        requested_workers: int,
    ) -> None:
        self._native: Any = native_session
        self._closed = False
        effective_workers = requested_workers or max(1, os.cpu_count() or 1)
        self._info = BackendInfo(
            name="rust",
            implementation_version=implementation_version,
            ir_major=ir_major,
            ir_minor=ir_minor,
            requested_workers=requested_workers,
            effective_workers=effective_workers,
            native_available=True,
            fallback_reason=None,
        )

    @property
    def info(self) -> BackendInfo:
        self._ensure_open()
        return self._info

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._call("close")
        finally:
            self._closed = True
            self._native = None

    def is_inconsistent(self) -> bool:
        value = self._call("is_inconsistent")
        if not isinstance(value, bool):
            raise BackendProtocolError("a native inconsistency boolean", value)
        return value

    def class_taxonomy(self) -> RawTaxonomy:
        return decode_raw_taxonomy(self._payload("class_taxonomy"))

    def object_property_taxonomy(self) -> RawTaxonomy:
        return decode_raw_taxonomy(self._payload("object_property_taxonomy"))

    def realization(self) -> RawRealization:
        return decode_raw_realization(self._payload("realization"))

    def query_class_expression(
        self, encoded_expression: bytes | None, kind: QueryKind, direct: bool
    ) -> RawQueryResult:
        value = decode_raw_query_result(
            self._payload(
                "query_class_expression",
                encoded_expression,
                int(kind),
                direct,
            )
        )
        if value.kind is not kind:
            raise BackendProtocolError(f"query kind {kind.name}", value.kind.name)
        return value

    def entails(self, encoded_axiom: bytes | None) -> bool:
        value = self._call("entails", encoded_axiom)
        if not isinstance(value, bool):
            raise BackendProtocolError("a native entailment boolean", value)
        return value

    def diagnostics(self) -> Mapping[str, DiagnosticScalar]:
        value = self._call("diagnostics")
        if not isinstance(value, Mapping):
            raise BackendProtocolError("a native diagnostics mapping", value)
        result: dict[str, DiagnosticScalar] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not isinstance(item, (int, float, str, bool)):
                raise BackendProtocolError("string-to-scalar native diagnostics", value)
            result[key] = item
        return MappingProxyType(dict(sorted(result.items())))

    def _payload(self, stage: str, *args: object) -> bytes:
        value = self._call(stage, *args)
        if not isinstance(value, bytes):
            raise BackendProtocolError(f"packed bytes from native {stage}", value)
        return value

    def _call(self, stage: str, *args: object) -> object:
        self._ensure_open()
        try:
            method = getattr(self._native, stage)
            if not callable(method):
                raise TypeError(f"native session attribute {stage!r} is not callable")
            return cast(object, method(*args))
        except (
            BackendProtocolError,
            ReasonerClosedError,
            MemoryError,
            KeyboardInterrupt,
            SystemExit,
        ):
            raise
        except Exception as error:
            raise InternalReasonerError(stage, "rust", str(error)) from error

    def _ensure_open(self) -> None:
        if self._closed:
            raise ReasonerClosedError


__all__ = ["NativeModule", "RustBackendFactory", "RustBackendSession"]
