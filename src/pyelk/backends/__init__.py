"""Backend selection, environment policy, and side-effect-light availability reporting."""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from typing import Any, Literal, cast

from pyelk.backends.python import IMPLEMENTATION_VERSION, PythonBackendFactory
from pyelk.config import ReasonerConfig
from pyelk.core import current_core_versions
from pyelk.exceptions import BackendUnavailableError, InternalReasonerError
from pyelk.indexing.codec import SCHEMA_MAJOR, SCHEMA_MINOR
from pyelk.indexing.ir import CompiledOntology
from pyelk.reasoning.contracts import (
    BackendAvailability,
    BackendConfig,
    BackendFactory,
    BackendReport,
    BackendSession,
)

_BACKEND_VALUES = frozenset({"auto", "python", "rust"})
_PURE_VALUES = frozenset({"0", "1"})


@dataclass(frozen=True, slots=True)
class _NativeProbe:
    availability: BackendAvailability
    module: object | None


def create_backend_session(
    compiled: CompiledOntology,
    config: ReasonerConfig,
) -> BackendSession:
    """Resolve one immutable backend choice and create its session."""

    if not isinstance(compiled, CompiledOntology):
        raise TypeError("compiled must be CompiledOntology")
    if not isinstance(config, ReasonerConfig):
        raise TypeError("config must be ReasonerConfig")
    environment_backend, pure = _environment()
    requested = config.backend if config.backend != "auto" else environment_backend
    effective = _apply_pure_mode(requested, pure)
    backend_config = cast(BackendConfig, config)

    if effective == "python":
        return PythonBackendFactory().create_session(compiled, backend_config)

    probe = _probe_native()
    if effective == "rust":
        if probe.module is None:
            raise BackendUnavailableError("rust", probe.availability.reason or "probe failed")
        try:
            return _rust_factory(probe).create_session(compiled, backend_config)
        except (MemoryError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as error:
            reason = _failure_text(error)
            raise BackendUnavailableError("rust", reason) from error

    if probe.module is not None:
        try:
            return _rust_factory(probe).create_session(compiled, backend_config)
        except (MemoryError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as error:
            reason = f"native session creation failed: {_failure_text(error)}"
            return PythonBackendFactory(
                native_available=True,
                fallback_reason=reason,
            ).create_session(compiled, backend_config)
    return PythonBackendFactory(
        native_available=False,
        fallback_reason=probe.availability.reason,
    ).create_session(compiled, backend_config)


def backend_report() -> BackendReport:
    """Report environment-driven backend availability without creating a session."""

    python = _python_availability()
    raw_backend = os.environ.get("PYELK_BACKEND", "auto")
    raw_pure = os.environ.get("PYELK_PURE_PYTHON", "0")
    if raw_backend not in _BACKEND_VALUES:
        return _backend_report(
            requested=raw_backend,
            selected=None,
            python=python,
            rust=_unprobed_rust("backend environment value is invalid"),
            selection_error="PYELK_BACKEND must be 'auto', 'python', or 'rust'",
        )
    if raw_pure not in _PURE_VALUES:
        return _backend_report(
            requested=raw_backend,
            selected=None,
            python=python,
            rust=_unprobed_rust("pure-mode environment value is invalid"),
            selection_error="PYELK_PURE_PYTHON must be '0' or '1'",
        )
    if raw_pure == "1":
        rust = _unprobed_rust("native probing disabled by PYELK_PURE_PYTHON=1")
        if raw_backend == "rust":
            return _backend_report(
                requested=raw_backend,
                selected=None,
                python=python,
                rust=rust,
                selection_error=(
                    "PYELK_PURE_PYTHON=1 conflicts with an explicit rust backend request"
                ),
            )
        return _backend_report(
            requested=raw_backend,
            selected="python",
            python=python,
            rust=rust,
            selection_error=None,
        )

    probe = _probe_native()
    selected: Literal["python", "rust"] | None
    selection_error: str | None = None
    if raw_backend == "python":
        selected = "python"
    elif raw_backend == "rust":
        selected = "rust" if probe.module is not None else None
        selection_error = None if selected is not None else probe.availability.reason
    else:
        selected = "rust" if probe.module is not None else "python"
    return _backend_report(
        requested=raw_backend,
        selected=selected,
        python=python,
        rust=probe.availability,
        selection_error=selection_error,
    )


def _backend_report(
    *,
    requested: str,
    selected: Literal["python", "rust"] | None,
    python: BackendAvailability,
    rust: BackendAvailability,
    selection_error: str | None,
) -> BackendReport:
    versions = current_core_versions()
    return BackendReport(
        requested=requested,
        selected=selected,
        python=python,
        rust=rust,
        selection_error=selection_error,
        core_package_version=versions.package_version,
        core_api_version=versions.api_version,
        core_model_schema_version=versions.model_schema_version,
        core_wire_format_version=versions.wire_format_version,
        core_adapter_protocol_version=versions.adapter_protocol_version,
    )


def _environment() -> tuple[Literal["auto", "python", "rust"], bool]:
    backend = os.environ.get("PYELK_BACKEND", "auto")
    pure = os.environ.get("PYELK_PURE_PYTHON", "0")
    if backend not in _BACKEND_VALUES:
        raise ValueError("PYELK_BACKEND must be 'auto', 'python', or 'rust'")
    if pure not in _PURE_VALUES:
        raise ValueError("PYELK_PURE_PYTHON must be '0' or '1'")
    return cast(Literal["auto", "python", "rust"], backend), pure == "1"


def _apply_pure_mode(
    requested: Literal["auto", "python", "rust"], pure: bool
) -> Literal["auto", "python", "rust"]:
    if not pure:
        return requested
    if requested == "rust":
        raise ValueError("PYELK_PURE_PYTHON=1 conflicts with an explicit rust backend request")
    return "python"


def _python_availability() -> BackendAvailability:
    return BackendAvailability(
        name="python",
        available=True,
        implementation_version=IMPLEMENTATION_VERSION,
        ir_major=SCHEMA_MAJOR,
        ir_minor=SCHEMA_MINOR,
        abi=None,
        reason=None,
    )


def _unprobed_rust(reason: str) -> BackendAvailability:
    return BackendAvailability(
        name="rust",
        available=None,
        implementation_version=None,
        ir_major=None,
        ir_minor=None,
        abi=None,
        reason=reason,
    )


def _unavailable_rust(reason: str) -> _NativeProbe:
    return _NativeProbe(
        BackendAvailability(
            name="rust",
            available=False,
            implementation_version=None,
            ir_major=None,
            ir_minor=None,
            abi=None,
            reason=reason,
        ),
        None,
    )


def _probe_native() -> _NativeProbe:
    try:
        native = importlib.import_module("pyelk._native")
    except (MemoryError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as error:
        return _unavailable_rust(f"native extension import failed: {_failure_text(error)}")

    try:
        version_function = native.implementation_version
        ir_function = native.ir_version
        create_function = native.create_session
        if (
            not callable(version_function)
            or not callable(ir_function)
            or not callable(create_function)
        ):
            raise TypeError("required native entry point is not callable")
        version = version_function()
        ir_version = ir_function()
        if not isinstance(version, str) or not version:
            raise TypeError("implementation_version() must return nonempty text")
        if version != IMPLEMENTATION_VERSION:
            raise ValueError(
                "implementation version mismatch: "
                f"Python {IMPLEMENTATION_VERSION}, native {version}"
            )
        if (
            not isinstance(ir_version, tuple)
            or len(ir_version) != 2
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in ir_version
            )
        ):
            raise TypeError("ir_version() must return two nonnegative integers")
        ir_major, ir_minor = ir_version
        if ir_major != SCHEMA_MAJOR:
            raise ValueError(f"IR major mismatch: Python {SCHEMA_MAJOR}, native {ir_major}")
        self_check = getattr(native, "self_check", None)
        if self_check is not None and (not callable(self_check) or self_check() is not True):
            raise ValueError("native self-check failed")
        abi = _native_abi(native)
    except (MemoryError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as error:
        return _unavailable_rust(f"native extension handshake failed: {_failure_text(error)}")

    return _NativeProbe(
        BackendAvailability(
            name="rust",
            available=True,
            implementation_version=version,
            ir_major=ir_major,
            ir_minor=ir_minor,
            abi=abi,
            reason=None,
        ),
        native,
    )


def _native_abi(native: object) -> str | None:
    value: Any = None
    for name in ("abi_version", "abi"):
        if hasattr(native, name):
            value = getattr(native, name)
            value = value() if callable(value) else value
            break
    if value is not None and not isinstance(value, str):
        raise TypeError("native ABI information must be text or None")
    return value


def _rust_factory(probe: _NativeProbe) -> BackendFactory:
    if probe.module is None or probe.availability.implementation_version is None:
        raise AssertionError("cannot create a Rust factory from an unavailable probe")
    from pyelk.backends.rust import RustBackendFactory

    return RustBackendFactory(
        cast(Any, probe.module),
        implementation_version=probe.availability.implementation_version,
        ir_major=cast(int, probe.availability.ir_major),
        ir_minor=cast(int, probe.availability.ir_minor),
        abi=probe.availability.abi,
    )


def _failure_text(error: BaseException) -> str:
    if isinstance(error, InternalReasonerError):
        return error.detail
    detail = str(error)
    return type(error).__name__ if not detail else f"{type(error).__name__}: {detail}"


__all__ = ["backend_report"]
