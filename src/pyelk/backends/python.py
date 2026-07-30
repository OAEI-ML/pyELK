"""Pure-Python adapter over the internal saturation, taxonomy, and query stages."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from pyelk.exceptions import ReasonerClosedError
from pyelk.indexing.codec import SCHEMA_MAJOR, SCHEMA_MINOR
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
from pyelk.reasoning.entailment import EntailmentEngine
from pyelk.reasoning.queries import ClassQueryEngine
from pyelk.reasoning.realization import realization
from pyelk.reasoning.session import SaturationSession
from pyelk.reasoning.taxonomy import class_taxonomy, object_property_taxonomy

IMPLEMENTATION_VERSION = "0.1.1"


class PythonBackendFactory:
    """Create pure sessions while retaining dispatch diagnostics."""

    __slots__ = ("_fallback_reason", "_native_available")

    def __init__(
        self,
        *,
        native_available: bool = False,
        fallback_reason: str | None = None,
    ) -> None:
        self._native_available = native_available
        self._fallback_reason = fallback_reason

    def create_session(
        self, compiled: CompiledOntology, config: BackendConfig
    ) -> PythonBackendSession:
        return PythonBackendSession(
            compiled,
            requested_workers=config.workers,
            native_available=self._native_available,
            fallback_reason=self._fallback_reason,
        )


class PythonBackendSession:
    """One lazily saturated pure-Python backend session."""

    __slots__ = (
        "_class_queries",
        "_class_taxonomy",
        "_closed",
        "_entailments",
        "_info",
        "_object_taxonomy",
        "_realization",
        "_session",
    )

    def __init__(
        self,
        compiled: CompiledOntology,
        *,
        requested_workers: int,
        native_available: bool,
        fallback_reason: str | None,
    ) -> None:
        if not isinstance(compiled, CompiledOntology):
            raise TypeError("compiled must be CompiledOntology")
        self._session: SaturationSession | None = SaturationSession(compiled)
        self._class_taxonomy: RawTaxonomy | None = None
        self._object_taxonomy: RawTaxonomy | None = None
        self._realization: RawRealization | None = None
        self._class_queries: ClassQueryEngine | None = None
        self._entailments: EntailmentEngine | None = None
        self._closed = False
        self._info = BackendInfo(
            name="python",
            implementation_version=IMPLEMENTATION_VERSION,
            ir_major=SCHEMA_MAJOR,
            ir_minor=SCHEMA_MINOR,
            requested_workers=requested_workers,
            effective_workers=1,
            native_available=native_available,
            fallback_reason=fallback_reason,
        )

    @property
    def info(self) -> BackendInfo:
        self._ensure_open()
        return self._info

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._class_queries = None
        self._entailments = None
        self._class_taxonomy = None
        self._object_taxonomy = None
        self._realization = None
        self._session = None

    def is_inconsistent(self) -> bool:
        return self._require_session().ensure_consistency().inconsistent

    def class_taxonomy(self) -> RawTaxonomy:
        session = self._require_session()
        if self._class_taxonomy is None:
            self._class_taxonomy = class_taxonomy(session)
        return self._class_taxonomy

    def object_property_taxonomy(self) -> RawTaxonomy:
        session = self._require_session()
        if self._object_taxonomy is None:
            self._object_taxonomy = object_property_taxonomy(session)
        return self._object_taxonomy

    def realization(self) -> RawRealization:
        session = self._require_session()
        if self._realization is None:
            self._realization = realization(session, self.class_taxonomy())
        return self._realization

    def query_class_expression(
        self, encoded_expression: bytes | None, kind: QueryKind, direct: bool
    ) -> RawQueryResult:
        session = self._require_session()
        if self._class_queries is None:
            self._class_queries = ClassQueryEngine(
                session,
                self.class_taxonomy(),
                self.realization(),
            )
        return self._class_queries.query(encoded_expression, kind, direct)

    def entails(self, encoded_axiom: bytes | None) -> bool:
        session = self._require_session()
        if self._entailments is None:
            self._entailments = EntailmentEngine(session)
        return self._entailments.entails(encoded_axiom)

    def diagnostics(self) -> Mapping[str, DiagnosticScalar]:
        session = self._require_session()
        values: dict[str, DiagnosticScalar] = dict(session.diagnostics())
        values["cached_class_queries"] = (
            0 if self._class_queries is None else self._class_queries.cached_query_count
        )
        values["cached_entailment_queries"] = (
            0 if self._entailments is None else self._entailments.cached_query_count
        )
        values["class_taxonomy_cached"] = self._class_taxonomy is not None
        values["ingestion_path"] = "scalar-python"
        values["object_property_taxonomy_cached"] = self._object_taxonomy is not None
        values["realization_cached"] = self._realization is not None
        return MappingProxyType(dict(sorted(values.items())))

    def _ensure_open(self) -> None:
        if self._closed:
            raise ReasonerClosedError

    def _require_session(self) -> SaturationSession:
        self._ensure_open()
        if self._session is None:  # pragma: no cover - close invariant
            raise ReasonerClosedError
        return self._session


__all__ = ["IMPLEMENTATION_VERSION", "PythonBackendFactory", "PythonBackendSession"]
