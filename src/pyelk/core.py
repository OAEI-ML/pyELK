"""Exact pyowl-core aliases and the pyELK compatibility boundary.

This module deliberately owns no OWL values and performs no ontology acquisition.  Input
coercion belongs to :mod:`pyelk.inputs`; the helpers here validate a view after it has crossed
pyowl-core's identity-preserving boundary and retain only its identity plus small metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from inspect import getattr_static

import pyowl_core as _core
from pyowl_core import (
    ADAPTER_PROTOCOL_VERSION,
    API_VERSION,
    MODEL_SCHEMA_VERSION,
    WIRE_FORMAT_VERSION,
    AdapterCompatibilityError,
    AxiomScope,
    BackendPreference,
    CoreCapabilities,
    Diagnostic,
    DocumentFormat,
    DocumentInput,
    Fingerprint,
    ImportPolicy,
    ImportResolver,
    LoadOptions,
    OntologyComposite,
    OntologyDelta,
    OntologyDocument,
    OntologyInput,
    OntologyOverlay,
    OntologySnapshot,
    OntologyView,
    OptionConflictError,
    OriginIndex,
    ParseLimits,
    Severity,
    SnapshotProvider,
    SourceMap,
    apply_delta,
    coerce_snapshot,
    compose_views,
    load_snapshot,
)

EXPECTED_PACKAGE_RANGE = ">=0.1,<0.2"
EXPECTED_API_VERSION = (0, 1)
EXPECTED_MODEL_SCHEMA_VERSION = 1
EXPECTED_WIRE_MAJOR = 1
MINIMUM_WIRE_MINOR = 0
EXPECTED_ADAPTER_PROTOCOL_VERSION = 1

_SEMVER = re.compile(
    r"^(\d+)\.(\d+)\.(\d+)"
    r"(?:(?:a|b|rc)\d+|(?:\.dev|\.post)\d+|-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?"
    r"(?:\+[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$"
)
_REQUIRED_VIEW_FEATURES = frozenset(
    {
        "document-boundaries",
        "document-scoped-anonymous",
        "import-manifest",
        "owl2-structural",
    }
)
_ONTOLOGY_VIEW_MEMBERS = frozenset(
    {
        "capabilities",
        "contains",
        "is_complete",
        "iter_axioms",
        "iter_extensions",
        "logical_fingerprint",
        "ontology_annotations",
        "origin_index",
        "report",
        "signature",
        "signature_fingerprint",
        "structural_fingerprint",
        "view",
    }
)


@dataclass(frozen=True, slots=True)
class CoreVersionInfo:
    """The independent pyowl-core compatibility dimensions captured by pyELK."""

    package_version: str
    api_version: tuple[int, int]
    model_schema_version: int
    wire_format_version: tuple[int, int]
    adapter_protocol_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.package_version, str) or not self.package_version:
            raise ValueError("package_version must be a nonempty string")
        for name in ("api_version", "wire_format_version"):
            value = getattr(self, name)
            if (
                not isinstance(value, tuple)
                or len(value) != 2
                or not all(
                    isinstance(item, int) and not isinstance(item, bool) and item >= 0
                    for item in value
                )
            ):
                raise TypeError(f"{name} must be a pair of nonnegative integers")
        for name in ("model_schema_version", "adapter_protocol_version"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")


def current_core_versions() -> CoreVersionInfo:
    """Read the installed core contract without performing discovery or I/O."""

    return CoreVersionInfo(
        package_version=_core.__version__,
        api_version=API_VERSION,
        model_schema_version=MODEL_SCHEMA_VERSION,
        wire_format_version=WIRE_FORMAT_VERSION,
        adapter_protocol_version=ADAPTER_PROTOCOL_VERSION,
    )


def _version_details(
    versions: CoreVersionInfo,
    *,
    capabilities: CoreCapabilities | None = None,
) -> dict[str, str | int]:
    adapter = (
        versions.adapter_protocol_version if capabilities is None else capabilities.adapter_protocol
    )
    model = versions.model_schema_version if capabilities is None else capabilities.model_schema
    wire = versions.wire_format_version if capabilities is None else capabilities.wire_format
    return {
        "expected_package": EXPECTED_PACKAGE_RANGE,
        "actual_package": versions.package_version,
        "expected_api": str(EXPECTED_API_VERSION),
        "actual_api": str(versions.api_version),
        "expected_model": EXPECTED_MODEL_SCHEMA_VERSION,
        "actual_model": model,
        "expected_wire": f"({EXPECTED_WIRE_MAJOR}, >= {MINIMUM_WIRE_MINOR})",
        "actual_wire": str(wire),
        "expected_adapter": EXPECTED_ADAPTER_PROTOCOL_VERSION,
        "actual_adapter": adapter,
    }


def _compatibility_error(
    field: str,
    expected: str,
    actual: str,
    *,
    versions: CoreVersionInfo,
    capabilities: CoreCapabilities | None = None,
) -> AdapterCompatibilityError:
    message = f"incompatible pyowl-core {field}: expected {expected}, got {actual}"
    details = _version_details(versions, capabilities=capabilities)
    details.update({"field": field, "expected": expected, "actual": actual})
    diagnostic = Diagnostic(
        code="ADAPTER_COMPATIBILITY",
        severity=Severity.ERROR,
        message=message,
        details=details,
    )
    return AdapterCompatibilityError(message, diagnostic=diagnostic)


def require_core_compatibility(
    actual: CoreVersionInfo | None = None,
) -> CoreVersionInfo:
    """Require the pyowl-core 0.1 API/model/wire/adapter compatibility line."""

    versions = actual or current_core_versions()
    match = _SEMVER.fullmatch(versions.package_version)
    if match is None:
        raise _compatibility_error(
            "package_version",
            f"{EXPECTED_PACKAGE_RANGE} semantic version",
            versions.package_version,
            versions=versions,
        )
    major, minor, _patch = (int(value) for value in match.groups())
    if (major, minor) != EXPECTED_API_VERSION:
        raise _compatibility_error(
            "package_version",
            EXPECTED_PACKAGE_RANGE,
            versions.package_version,
            versions=versions,
        )
    if versions.api_version != EXPECTED_API_VERSION:
        raise _compatibility_error(
            "API_VERSION",
            str(EXPECTED_API_VERSION),
            str(versions.api_version),
            versions=versions,
        )
    if versions.model_schema_version != EXPECTED_MODEL_SCHEMA_VERSION:
        raise _compatibility_error(
            "MODEL_SCHEMA_VERSION",
            str(EXPECTED_MODEL_SCHEMA_VERSION),
            str(versions.model_schema_version),
            versions=versions,
        )
    wire_major, wire_minor = versions.wire_format_version
    if wire_major != EXPECTED_WIRE_MAJOR or wire_minor < MINIMUM_WIRE_MINOR:
        raise _compatibility_error(
            "WIRE_FORMAT_VERSION",
            f"({EXPECTED_WIRE_MAJOR}, >= {MINIMUM_WIRE_MINOR})",
            str(versions.wire_format_version),
            versions=versions,
        )
    if versions.adapter_protocol_version != EXPECTED_ADAPTER_PROTOCOL_VERSION:
        raise _compatibility_error(
            "ADAPTER_PROTOCOL_VERSION",
            str(EXPECTED_ADAPTER_PROTOCOL_VERSION),
            str(versions.adapter_protocol_version),
            versions=versions,
        )
    return versions


@dataclass(frozen=True, slots=True, eq=False)
class CapturedOntology:
    """One strong view reference plus immutable fingerprint/version metadata."""

    view: OntologyView
    structural_fingerprint: Fingerprint
    logical_fingerprint: Fingerprint
    signature_fingerprint: Fingerprint
    core_package_version: str
    core_api_version: tuple[int, int]
    core_model_schema_version: int
    core_wire_format_version: tuple[int, int]
    core_adapter_protocol_version: int


def _require_view_capabilities(
    capabilities: CoreCapabilities,
    versions: CoreVersionInfo,
) -> None:
    if not isinstance(capabilities, CoreCapabilities):
        raise _compatibility_error(
            "capabilities",
            "pyowl_core.CoreCapabilities",
            type(capabilities).__name__,
            versions=versions,
        )
    checks = (
        (
            "adapter_protocol",
            EXPECTED_ADAPTER_PROTOCOL_VERSION,
            capabilities.adapter_protocol,
        ),
        ("model_schema", EXPECTED_MODEL_SCHEMA_VERSION, capabilities.model_schema),
        ("wire_major", EXPECTED_WIRE_MAJOR, capabilities.wire_format[0]),
    )
    for field, expected, actual in checks:
        if actual != expected:
            raise _compatibility_error(
                field,
                str(expected),
                str(actual),
                versions=versions,
                capabilities=capabilities,
            )
    if capabilities.wire_format[1] < MINIMUM_WIRE_MINOR:
        raise _compatibility_error(
            "wire_minor",
            f">= {MINIMUM_WIRE_MINOR}",
            str(capabilities.wire_format[1]),
            versions=versions,
            capabilities=capabilities,
        )
    missing = sorted(_REQUIRED_VIEW_FEATURES - capabilities.features)
    if missing:
        raise _compatibility_error(
            "features",
            ",".join(sorted(_REQUIRED_VIEW_FEATURES)),
            f"missing:{','.join(missing)}",
            versions=versions,
            capabilities=capabilities,
        )


def _require_compatible_view(view: OntologyView) -> CoreVersionInfo:
    """Validate the bounded core contract without reading ontology-sized state."""

    versions = require_core_compatibility()
    try:
        for member in _ONTOLOGY_VIEW_MEMBERS:
            getattr_static(view, member)
    except AttributeError:
        raise _compatibility_error(
            "OntologyView",
            "runtime protocol",
            type(view).__name__,
            versions=versions,
        ) from None
    capabilities = view.capabilities
    _require_view_capabilities(capabilities, versions)
    return versions


def capture_compatible_view(view: OntologyView) -> CapturedOntology:
    """Validate and retain an already-coerced core view by exact identity."""

    versions = _require_compatible_view(view)
    for name in (
        "structural_fingerprint",
        "logical_fingerprint",
        "signature_fingerprint",
    ):
        value = getattr(view, name)
        if not isinstance(value, Fingerprint):
            raise _compatibility_error(
                name,
                "pyowl_core.Fingerprint",
                type(value).__name__,
                versions=versions,
                capabilities=view.capabilities,
            )
    return CapturedOntology(
        view=view,
        structural_fingerprint=view.structural_fingerprint,
        logical_fingerprint=view.logical_fingerprint,
        signature_fingerprint=view.signature_fingerprint,
        core_package_version=versions.package_version,
        core_api_version=versions.api_version,
        core_model_schema_version=versions.model_schema_version,
        core_wire_format_version=versions.wire_format_version,
        core_adapter_protocol_version=versions.adapter_protocol_version,
    )


__all__ = [
    "ADAPTER_PROTOCOL_VERSION",
    "API_VERSION",
    "EXPECTED_ADAPTER_PROTOCOL_VERSION",
    "EXPECTED_API_VERSION",
    "EXPECTED_MODEL_SCHEMA_VERSION",
    "EXPECTED_PACKAGE_RANGE",
    "EXPECTED_WIRE_MAJOR",
    "MINIMUM_WIRE_MINOR",
    "MODEL_SCHEMA_VERSION",
    "WIRE_FORMAT_VERSION",
    "AdapterCompatibilityError",
    "AxiomScope",
    "BackendPreference",
    "CapturedOntology",
    "CoreCapabilities",
    "CoreVersionInfo",
    "DocumentFormat",
    "DocumentInput",
    "Fingerprint",
    "ImportPolicy",
    "ImportResolver",
    "LoadOptions",
    "OntologyComposite",
    "OntologyDelta",
    "OntologyDocument",
    "OntologyInput",
    "OntologyOverlay",
    "OntologySnapshot",
    "OntologyView",
    "OptionConflictError",
    "OriginIndex",
    "ParseLimits",
    "SnapshotProvider",
    "SourceMap",
    "apply_delta",
    "capture_compatible_view",
    "coerce_snapshot",
    "compose_views",
    "current_core_versions",
    "load_snapshot",
    "require_core_compatibility",
]
