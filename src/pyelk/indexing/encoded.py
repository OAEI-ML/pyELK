"""Negotiated public pyowl-core structural-column handoff.

The objects in this module are deliberately an adapter boundary rather than another OWL
model.  They retain the exact public core view and its encoded-view owner while presenting a
small, validated envelope to the optional native compiler.  Schema-local identifiers and
buffer contents remain opaque here; the schema-specific Rust compiler validates and consumes
them in one coarse call.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Any

import pyowl_core as _core

ENCODED_SCHEMA_NAME = "pyowl-core/structural-columns"
ENCODED_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True, eq=False)
class EncodedStructuralHandoff:
    """Validated, owning input for pyELK's schema-specific native compiler.

    ``encoded_view`` and ``owner`` are both retained intentionally.  The former owns any
    exported buffers and the latter makes the public snapshot lifetime explicit even for a
    provider whose encoded view uses a separate lightweight wrapper.
    """

    encoded_view: object
    owner: _core.OntologyView
    schema_name: str
    schema_version: int
    model_schema: int
    scope: _core.AxiomScope
    descriptor: bytes
    descriptor_digest: bytes
    buffers: Mapping[str, memoryview]
    segments: tuple[object, ...]
    structural_fingerprint: _core.Fingerprint

    @property
    def buffer_count(self) -> int:
        """Number of coarse buffers crossing the native boundary."""

        return len(self.buffers)

    @property
    def buffer_bytes(self) -> int:
        """Total exported buffer bytes without materialising their contents."""

        return sum(value.nbytes for value in self.buffers.values())


@dataclass(frozen=True, slots=True)
class EncodedViewNegotiation:
    """Capability result; absence is compatible while malformed advertising is an error."""

    handoff: EncodedStructuralHandoff | None
    advertised_schema: int | None
    reason: str | None

    def __post_init__(self) -> None:
        if self.handoff is None:
            if not isinstance(self.reason, str) or not self.reason:
                raise ValueError("an unavailable encoded view requires a reason")
        elif self.reason is not None:
            raise ValueError("an available encoded view cannot contain an unavailable reason")
        if self.advertised_schema is not None and (
            isinstance(self.advertised_schema, bool)
            or not isinstance(self.advertised_schema, int)
            or self.advertised_schema < 1
        ):
            raise ValueError("advertised_schema must be a positive integer or None")

    @property
    def available(self) -> bool:
        """Whether a validated handoff is available."""

        return self.handoff is not None


def negotiate_encoded_structural_view(
    ontology: _core.OntologyView,
    *,
    scope: _core.AxiomScope = _core.AxiomScope.CLOSURE,
) -> EncodedViewNegotiation:
    """Request and validate structural columns without scalar ontology traversal.

    A provider that does not advertise the schema is a normal scalar-fallback case.  Once a
    provider advertises it, acquisition or envelope validation fails closed: callers must not
    silently switch paths after observing a falsely advertised or malformed encoded view.
    """

    if not isinstance(ontology, _core.OntologyView):
        raise TypeError("ontology must implement pyowl_core.OntologyView")
    if not isinstance(scope, _core.AxiomScope):
        raise TypeError("scope must be pyowl_core.AxiomScope")
    capabilities = ontology.capabilities
    if not isinstance(capabilities, _core.CoreCapabilities):
        raise _compatibility_error("encoded-view negotiation requires pyowl_core.CoreCapabilities")
    advertised = capabilities.encoded_view_schemas.get(ENCODED_SCHEMA_NAME)
    if advertised is None:
        return EncodedViewNegotiation(
            handoff=None,
            advertised_schema=None,
            reason=f"core does not advertise {ENCODED_SCHEMA_NAME!r}",
        )
    if advertised < ENCODED_SCHEMA_VERSION:
        return EncodedViewNegotiation(
            handoff=None,
            advertised_schema=advertised,
            reason=(
                f"core advertises {ENCODED_SCHEMA_NAME!r} schema {advertised}, "
                f"but pyELK requires {ENCODED_SCHEMA_VERSION}"
            ),
        )

    encoded_type = getattr(_core, "EncodedStructuralView", None)
    if not isinstance(encoded_type, type):
        raise _compatibility_error(
            "core advertises structural columns but exports no EncodedStructuralView type"
        )
    try:
        encoded: Any = ontology.view(
            encoded_type,
            schema_version=ENCODED_SCHEMA_VERSION,
            scope=scope,
        )
    except (MemoryError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as error:
        raise _compatibility_error(
            "core advertised structural columns but acquisition failed: "
            f"{type(error).__name__}: {error}"
        ) from error
    if not isinstance(encoded, encoded_type):
        raise _protocol_error(
            "view_type",
            f"expected {encoded_type.__module__}.{encoded_type.__qualname__}, "
            f"received {type(encoded).__name__}",
        )
    return EncodedViewNegotiation(
        handoff=_validate_encoded_view(ontology, encoded, scope),
        advertised_schema=advertised,
        reason=None,
    )


def _validate_encoded_view(
    ontology: _core.OntologyView,
    encoded: object,
    scope: _core.AxiomScope,
) -> EncodedStructuralHandoff:
    schema_name = _required_attribute(encoded, "schema_name")
    if schema_name != ENCODED_SCHEMA_NAME:
        raise _protocol_error("schema_name", repr(schema_name))
    schema_version = _positive_int(encoded, "schema_version")
    if schema_version != ENCODED_SCHEMA_VERSION:
        raise _protocol_error("schema_version", str(schema_version))
    model_schema = _positive_int(encoded, "model_schema")
    if model_schema != ontology.capabilities.model_schema:
        raise _protocol_error(
            "model_schema",
            f"view {model_schema}, owner {ontology.capabilities.model_schema}",
        )
    owner = _required_attribute(encoded, "owner")
    if owner is not ontology:
        raise _protocol_error("owner", "encoded view does not retain the requested view identity")

    raw_descriptor = _required_attribute(encoded, "descriptor")
    if not isinstance(raw_descriptor, bytes) or not raw_descriptor:
        raise _protocol_error("descriptor", "expected nonempty canonical bytes")
    descriptor_digest = sha256(raw_descriptor).digest()
    advertised_digest = getattr(encoded, "descriptor_digest", descriptor_digest)
    if not isinstance(advertised_digest, bytes) or advertised_digest != descriptor_digest:
        raise _protocol_error("descriptor_digest", "does not match SHA-256(descriptor)")

    raw_fingerprint = _required_attribute(encoded, "structural_fingerprint")
    if not isinstance(raw_fingerprint, _core.Fingerprint):
        raise _protocol_error("structural_fingerprint", type(raw_fingerprint).__name__)

    raw_scope = getattr(encoded, "scope", scope)
    if raw_scope != scope and raw_scope != scope.value:
        raise _protocol_error("scope", f"expected {scope.value!r}, received {raw_scope!r}")

    raw_buffers = _required_attribute(encoded, "buffers")
    if not isinstance(raw_buffers, Mapping):
        raise _protocol_error("buffers", "expected a mapping of names to read-only buffers")
    buffers: dict[str, memoryview] = {}
    for name, value in raw_buffers.items():
        if not isinstance(name, str) or not name:
            raise _protocol_error("buffers", "buffer names must be nonempty strings")
        if name in buffers:  # pragma: no cover - Mapping keys are unique by construction
            raise _protocol_error("buffers", f"duplicate buffer name {name!r}")
        buffers[name] = _readonly_byte_view(name, value)
    if not buffers:
        raise _protocol_error("buffers", "schema 1 requires its named column buffers")

    raw_segments = getattr(encoded, "segments", ())
    try:
        segments = tuple(raw_segments)
    except TypeError as error:
        raise _protocol_error("segments", "expected a finite iterable") from error

    return EncodedStructuralHandoff(
        encoded_view=encoded,
        owner=ontology,
        schema_name=schema_name,
        schema_version=schema_version,
        model_schema=model_schema,
        scope=scope,
        descriptor=raw_descriptor,
        descriptor_digest=descriptor_digest,
        buffers=MappingProxyType(dict(sorted(buffers.items()))),
        segments=segments,
        structural_fingerprint=raw_fingerprint,
    )


def _readonly_byte_view(name: str, value: object) -> memoryview:
    try:
        result = memoryview(value)  # type: ignore[arg-type]
    except TypeError as error:
        raise _protocol_error(
            "buffers", f"{name!r} does not support the buffer protocol"
        ) from error
    if not result.readonly:
        result.release()
        raise _protocol_error("buffers", f"{name!r} is writable")
    if not result.c_contiguous:
        result.release()
        raise _protocol_error("buffers", f"{name!r} is not C-contiguous")
    try:
        if result.format != "B" or result.ndim != 1 or result.itemsize != 1:
            result = result.cast("B")
    except (TypeError, ValueError) as error:
        result.release()
        raise _protocol_error("buffers", f"{name!r} cannot be viewed as bytes") from error
    return result


def _positive_int(value: object, name: str) -> int:
    result = _required_attribute(value, name)
    if isinstance(result, bool) or not isinstance(result, int) or result < 1:
        raise _protocol_error(name, f"expected a positive integer, received {result!r}")
    return result


def _required_attribute(value: object, name: str) -> Any:
    try:
        return getattr(value, name)
    except AttributeError as error:
        raise _protocol_error(name, "required attribute is missing") from error


def _compatibility_error(message: str) -> _core.AdapterCompatibilityError:
    diagnostic = _core.Diagnostic(
        code="ADAPTER_COMPATIBILITY",
        severity=_core.Severity.ERROR,
        message=message,
        details={
            "consumer": "pyelk-reasoner",
            "encoded_schema": ENCODED_SCHEMA_NAME,
            "required_schema_version": ENCODED_SCHEMA_VERSION,
        },
    )
    return _core.AdapterCompatibilityError(message, diagnostic=diagnostic)


def _protocol_error(field: str, detail: str) -> _core.BackendProtocolError:
    return _core.BackendProtocolError(
        f"invalid {ENCODED_SCHEMA_NAME} schema {ENCODED_SCHEMA_VERSION} field {field}: {detail}"
    )


__all__ = [
    "ENCODED_SCHEMA_NAME",
    "ENCODED_SCHEMA_VERSION",
    "EncodedStructuralHandoff",
    "EncodedViewNegotiation",
    "negotiate_encoded_structural_view",
]
