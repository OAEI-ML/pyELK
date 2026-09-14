"""Thin defensive adapter over the private optional :mod:`pyelk._native` module."""

from __future__ import annotations

import os
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Protocol, cast

import pyowl_core as owl

from pyelk.exceptions import (
    BackendProtocolError,
    InternalReasonerError,
    ReasonerClosedError,
    UnsupportedFeatureError,
)
from pyelk.indexing.conversion import FEATURE_INDEX
from pyelk.indexing.encoded import (
    ENCODED_BUFFER_WIDTHS,
    ENCODED_SCHEMA_NAME,
    EncodedStructuralHandoff,
    EncodedViewNegotiation,
    negotiate_encoded_structural_view,
)
from pyelk.indexing.ir import CompiledOntology
from pyelk.indexing.metadata import (
    CompilerMetadata,
    NativeCompilerMetadata,
    decode_compiler_metadata,
)
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

    __slots__ = (
        "_abi",
        "_encoded_view_schemas",
        "_implementation_version",
        "_ir_major",
        "_ir_minor",
        "_native",
    )

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
        self._encoded_view_schemas = _encoded_view_schemas(native)

    @property
    def encoded_view_schemas(self) -> Mapping[str, int]:
        """Structural-column schemas explicitly supported by this extension."""

        return self._encoded_view_schemas

    def negotiate_encoded_input(
        self,
        ontology: owl.OntologyView,
        *,
        scope: owl.AxiomScope = owl.AxiomScope.CLOSURE,
        require_native_validation: bool = False,
    ) -> EncodedViewNegotiation:
        """Acquire core columns only if this exact native build can consume them."""

        if not isinstance(ontology, owl.OntologyView):
            raise TypeError("ontology must implement pyowl_core.OntologyView")
        supported = self._encoded_view_schemas.get(ENCODED_SCHEMA_NAME)
        advertised = ontology.capabilities.encoded_view_schemas.get(ENCODED_SCHEMA_NAME)
        if supported is None:
            return EncodedViewNegotiation(
                handoff=None,
                advertised_schema=advertised,
                reason="native extension does not advertise the pyowl-core structural schema",
            )
        return negotiate_encoded_structural_view(
            ontology, scope=scope, require_native_validation=require_native_validation
        )

    def create_session(
        self, compiled: CompiledOntology, config: BackendConfig
    ) -> RustBackendSession:
        try:
            native_session = self._native.create_session(compiled.encode(), config.workers)
        except (MemoryError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as error:
            raise InternalReasonerError("create_session", "rust", str(error)) from error
        _configure_query_cache(native_session, config)
        return RustBackendSession(
            native_session,
            abi=self._abi,
            implementation_version=self._implementation_version,
            ir_major=self._ir_major,
            ir_minor=self._ir_minor,
            requested_workers=config.workers,
            ingestion_path="scalar-wire",
        )

    def create_encoded_session(
        self,
        handoff: EncodedStructuralHandoff,
        config: BackendConfig,
    ) -> RustBackendSession:
        """Create a native session through one negotiated structural-column call.

        This method never falls back.  Callers decide whether to use scalar-wire before
        invoking it; once an encoded handoff is selected, any protocol/compiler failure is
        surfaced so partially consumed or falsely advertised data cannot change paths.
        """

        if not isinstance(handoff, EncodedStructuralHandoff):
            raise TypeError("handoff must be EncodedStructuralHandoff")
        supported = self._encoded_view_schemas.get(handoff.schema_name)
        if supported is None or supported < handoff.schema_version:
            raise BackendProtocolError(
                f"native support for {handoff.schema_name} schema >= {handoff.schema_version}",
                dict(self._encoded_view_schemas),
            )
        create = getattr(self._native, "create_session_from_encoded", None)
        if not callable(create):
            raise BackendProtocolError(
                "create_session_from_encoded on an extension advertising structural columns",
                create,
            )
        unsupported = getattr(config, "unsupported", "ignore")
        if unsupported not in {"ignore", "error"}:
            raise BackendProtocolError("unsupported policy 'ignore' or 'error'", unsupported)
        try:
            native_session = create(
                handoff.encoded_view,
                config.workers,
                unsupported,
                **(
                    {"require_native_pipeline": True}
                    if getattr(config, "require_native_pipeline", False)
                    else {}
                ),
            )
        except (MemoryError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as error:
            unsupported_type = getattr(self._native, "NativeUnsupportedFeatureError", None)
            if isinstance(unsupported_type, type) and isinstance(error, unsupported_type):
                feature = str(error)
                if feature not in FEATURE_INDEX:
                    raise BackendProtocolError(
                        "a canonical native unsupported feature identifier",
                        feature,
                    ) from error
                raise UnsupportedFeatureError(feature, handoff.owner) from error
            if isinstance(error, ValueError):
                raise BackendProtocolError(
                    "valid encoded structural compiler input",
                    str(error),
                ) from error
            raise InternalReasonerError(
                "create_session_from_encoded",
                "rust",
                str(error),
            ) from error
        _configure_query_cache(native_session, config)
        return RustBackendSession(
            native_session,
            abi=self._abi,
            implementation_version=self._implementation_version,
            ir_major=self._ir_major,
            ir_minor=self._ir_minor,
            requested_workers=config.workers,
            ingestion_path="encoded-native",
            encoded_owner=handoff,
            require_native_pipeline=getattr(config, "require_native_pipeline", False),
            native_symbols_type=getattr(self._native, "_NativeServiceSymbols", None),
            native_result_type=getattr(self._native, "_NativeServiceResult", None),
        )


def _configure_query_cache(native_session: object, config: BackendConfig) -> None:
    limit = getattr(config, "query_cache_bytes", 64 * 1024 * 1024)
    configure = getattr(native_session, "set_query_cache_bytes", None)
    if callable(configure):
        configure(limit)
    elif limit != 64 * 1024 * 1024:
        raise BackendProtocolError("native support for query_cache_bytes", native_session)


class RustBackendSession:
    """Decode and validate coarse native calls without leaking native values."""

    __slots__ = (
        "_abi",
        "_closed",
        "_compiler_metadata",
        "_encoded_owner",
        "_info",
        "_ingestion_path",
        "_native",
        "_native_result_publications",
        "_native_result_type",
        "_native_results",
        "_native_symbols_type",
        "_require_native_pipeline",
    )

    def __init__(
        self,
        native_session: object,
        *,
        abi: str | None,
        implementation_version: str,
        ir_major: int,
        ir_minor: int,
        requested_workers: int,
        ingestion_path: str,
        encoded_owner: EncodedStructuralHandoff | None = None,
        require_native_pipeline: bool = False,
        native_symbols_type: type | None = None,
        native_result_type: type | None = None,
    ) -> None:
        self._require_native_pipeline = require_native_pipeline
        self._native_symbols_type = native_symbols_type
        self._native_result_type = native_result_type
        self._native_result_publications = 0
        self._native_results: dict[str, tuple[Any, Any, int | None]] = {}
        self._native: Any = native_session
        self._abi = abi
        self._closed = False
        self._compiler_metadata: CompilerMetadata | None = None
        if ingestion_path not in {"scalar-wire", "encoded-native"}:
            raise ValueError("invalid Rust ingestion path")
        if (ingestion_path == "encoded-native") != (encoded_owner is not None):
            raise ValueError("encoded-native sessions must retain exactly one encoded owner")
        self._ingestion_path = ingestion_path
        self._encoded_owner = encoded_owner
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
            _compiler_handoff=(
                {
                    "buffer_widths": ENCODED_BUFFER_WIDTHS,
                    "descriptor_sha256": encoded_owner.descriptor_digest.hex(),
                    "model_schema": encoded_owner.model_schema,
                    "schema_name": encoded_owner.schema_name,
                    "schema_version": encoded_owner.schema_version,
                }
                if encoded_owner is not None
                else None
            ),
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
            self._native_results.clear()
            self._native = None
            self._compiler_metadata = None
            self._encoded_owner = None

    def compiler_metadata(self) -> CompilerMetadata:
        """Decode the bounded native facade ledger lazily and at most once."""

        if self._compiler_metadata is None:
            if self._require_native_pipeline:
                if self._native_symbols_type is None:
                    raise BackendProtocolError("native service symbol type", None)
                self._compiler_metadata = NativeCompilerMetadata(
                    self._call("service_symbols"), self._native_symbols_type
                )
            else:
                self._compiler_metadata = decode_compiler_metadata(
                    self._payload("compiler_metadata")
                )
        return self._compiler_metadata

    def is_inconsistent(self) -> bool:
        value = self._call("is_inconsistent")
        if not isinstance(value, bool):
            raise BackendProtocolError("a native inconsistency boolean", value)
        return value

    def class_taxonomy(self) -> RawTaxonomy:
        if self._require_native_pipeline:
            return cast(RawTaxonomy, self._service_result("class", "service_taxonomy", 0))
        return decode_raw_taxonomy(self._payload("class_taxonomy"))

    def object_property_taxonomy(self) -> RawTaxonomy:
        if self._require_native_pipeline:
            return cast(RawTaxonomy, self._service_result("object", "service_taxonomy", 2))
        return decode_raw_taxonomy(self._payload("object_property_taxonomy"))

    def realization(self) -> RawRealization:
        if self._require_native_pipeline:
            return cast(RawRealization, self._service_result("realization", "service_realization"))
        return decode_raw_realization(self._payload("realization"))

    def query_class_expression(
        self, encoded_expression: bytes | None, kind: QueryKind, direct: bool
    ) -> RawQueryResult:
        if self._require_native_pipeline:
            return cast(
                RawQueryResult,
                self._service_result(
                    "query", "service_query", encoded_expression, int(kind), direct
                ),
            )
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

    def named_query(
        self, entity_kind: int, iri: str, kind: QueryKind, direct: bool
    ) -> RawQueryResult:
        return cast(
            RawQueryResult,
            self._service_result(
                "query", "service_named_query", entity_kind, iri, int(kind), direct
            ),
        )

    def _service_result(self, key: str, stage: str, *args: object) -> object:
        proof = self._call(stage, *args)
        if self._native_result_type is None or type(proof) is not self._native_result_type:
            raise BackendProtocolError("native-issued result envelope", proof)
        proof = cast(Any, proof)
        raw, _public = proof.payloads(self._native)
        if key in {"class", "object"}:
            result: Any = _native_value(RawTaxonomy, raw)
        elif key == "realization":
            taxonomy = _native_value(RawTaxonomy, raw[0])
            self._native_results["realization_class"] = (taxonomy, proof, 0)
            result = _native_value(RawRealization, (taxonomy, raw[1], raw[2]))
        else:
            result = _native_value(RawQueryResult, (QueryKind(raw[0]), raw[1], raw[2]))
        # Three persistent base outputs and the last requested query, bounded independently
        # of the number of queries. The opaque native owner retains the immutable payload.
        self._native_results[key] = (result, proof, None)
        self._native_result_publications += 1
        return result

    def native_payload(self, value: object) -> tuple[Any, ...]:
        self._ensure_open()
        for candidate, proof, index in self._native_results.values():
            if candidate is not value:
                continue
            raw, public = proof.payloads(self._native)
            if index is not None:
                raw, public = raw[index], public[index]
            if isinstance(value, RawTaxonomy):
                valid = (
                    value.nodes is raw[0]
                    and value.direct_edges is raw[1]
                    and value.top == raw[2]
                    and value.bottom == raw[3]
                )
            elif isinstance(value, RawRealization):
                self.native_payload(value.class_taxonomy)
                valid = value.instance_nodes is raw[1] and value.direct_types is raw[2]
            else:
                valid = (
                    isinstance(value, RawQueryResult)
                    and int(value.kind) == raw[0]
                    and value.boolean is raw[1]
                    and value.nodes is raw[2]
                )
            if not valid:
                raise BackendProtocolError("unchanged native result payload", value)
            return cast(tuple[Any, ...], public)
        raise BackendProtocolError("a result issued by this native session", value)

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
        result["ingestion_path"] = self._ingestion_path
        if self._require_native_pipeline:
            result["native_pipeline_required"] = True
            result["native_core_receipt_validated"] = True
            result["native_metadata_validation"] = True
            result["native_result_validation"] = True
            result["native_metadata_domain_copies"] = 0
            metadata = self.compiler_metadata()
            if not isinstance(metadata, NativeCompilerMetadata):
                raise BackendProtocolError("native metadata in strict session", metadata)
            records, lookups = metadata.native_owner.metrics()
            result["native_symbol_rows_materialized"] = records
            result["native_symbol_lookups"] = lookups
            result["native_result_publications"] = self._native_result_publications
            result["native_live_result_envelopes"] = len(self._native_results)
        if self._abi is not None:
            result["native_abi_version"] = self._abi
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


def _encoded_view_schemas(native: object) -> Mapping[str, int]:
    schemas_function = getattr(native, "encoded_view_schemas", None)
    create_function = getattr(native, "create_session_from_encoded", None)
    if schemas_function is None and create_function is None:
        return MappingProxyType({})
    if not callable(schemas_function) or not callable(create_function):
        raise BackendProtocolError(
            "paired callable encoded_view_schemas/create_session_from_encoded entry points",
            {
                "encoded_view_schemas": schemas_function,
                "create_session_from_encoded": create_function,
            },
        )
    raw = schemas_function()
    if not isinstance(raw, Mapping):
        raise BackendProtocolError("a native encoded-view schema mapping", raw)
    schemas: dict[str, int] = {}
    for name, version in raw.items():
        if (
            not isinstance(name, str)
            or not name
            or isinstance(version, bool)
            or not isinstance(version, int)
            or version < 1
        ):
            raise BackendProtocolError("nonempty schema names and positive versions", raw)
        schemas[name] = version
    if ENCODED_SCHEMA_NAME not in schemas and schemas:
        # Extensions may eventually advertise other consumers' schemas, but pyELK must not
        # infer that they implement its compiler entry point.
        raise BackendProtocolError(
            f"native schema mapping containing {ENCODED_SCHEMA_NAME!r}",
            raw,
        )
    return MappingProxyType(dict(sorted(schemas.items())))


__all__ = ["NativeModule", "RustBackendFactory", "RustBackendSession"]


def _native_value(cls: type, values: tuple[Any, ...]) -> Any:
    """Construct the existing raw value only after native type/owner validation."""
    fields = {
        RawTaxonomy: ("nodes", "direct_edges", "top", "bottom"),
        RawRealization: ("class_taxonomy", "instance_nodes", "direct_types"),
        RawQueryResult: ("kind", "boolean", "nodes"),
    }[cls]
    result: Any = object.__new__(cls)
    for name, value in zip(fields, values, strict=True):
        object.__setattr__(result, name, value)
    return result
