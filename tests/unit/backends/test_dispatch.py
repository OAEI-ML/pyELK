from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

import pyowl_core
import pytest

from pyelk.backends import (
    backend_report,
    create_backend_session,
    try_create_encoded_backend_session,
)
from pyelk.backends.python import IMPLEMENTATION_VERSION
from pyelk.config import ReasonerConfig
from pyelk.exceptions import BackendProtocolError, BackendUnavailableError, ReasonerClosedError
from pyelk.indexing.codec import SCHEMA_MAJOR, SCHEMA_MINOR
from pyelk.indexing.ir import EntityId
from pyelk.indexing.metadata import CompilerMetadata, metadata_from_compiled
from pyelk.reasoning.contracts import RawTaxonomy
from tests.helpers.contracts import FakeBackendSession, TinyCompiledOntologyBuilder


class _NativeSession:
    def close(self) -> None:
        return None


def _native(
    *,
    version: str = IMPLEMENTATION_VERSION,
    ir: tuple[int, int] = (SCHEMA_MAJOR, SCHEMA_MINOR),
    self_check: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        implementation_version=lambda: version,
        ir_version=lambda: ir,
        self_check=lambda: self_check,
        abi_version=lambda: "abi3-py310",
        create_session=lambda payload, workers: _NativeSession(),
    )


def test_explicit_python_never_imports_native(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(name: str) -> Any:
        raise AssertionError(f"unexpected native import: {name}")

    monkeypatch.setattr(importlib, "import_module", forbidden)
    session = create_backend_session(
        TinyCompiledOntologyBuilder().build(),
        ReasonerConfig(backend="python", workers=7),
    )
    assert session.info.name == "python"
    assert session.info.effective_workers == 1
    session.close()


def test_auto_selects_valid_fake_native(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _native()
    monkeypatch.setattr(importlib, "import_module", lambda name: fake)
    session = create_backend_session(
        TinyCompiledOntologyBuilder().build(),
        ReasonerConfig(backend="auto", workers=2),
    )
    assert session.info.name == "rust"
    assert session.info.effective_workers == 2
    assert session.info.native_available is True
    session.close()


def test_auto_falls_back_and_explicit_rust_fails_on_absence_or_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(name: str) -> Any:
        raise ModuleNotFoundError(name)

    compiled = TinyCompiledOntologyBuilder().build()
    monkeypatch.setattr(importlib, "import_module", missing)
    fallback = create_backend_session(compiled, ReasonerConfig())
    assert fallback.info.name == "python"
    assert fallback.info.native_available is False
    assert "import failed" in (fallback.info.fallback_reason or "")
    with pytest.raises(BackendUnavailableError, match="unavailable"):
        create_backend_session(compiled, ReasonerConfig(backend="rust"))

    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: _native(version="99.0.0"),
    )
    mismatched = create_backend_session(compiled, ReasonerConfig())
    assert mismatched.info.name == "python"
    assert "version mismatch" in (mismatched.info.fallback_reason or "")


def test_environment_precedence_validation_and_hard_pure_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiled = TinyCompiledOntologyBuilder().build()
    fake = _native()
    monkeypatch.setattr(importlib, "import_module", lambda name: fake)
    monkeypatch.setenv("PYELK_BACKEND", "rust")
    assert create_backend_session(compiled, ReasonerConfig()).info.name == "rust"
    assert create_backend_session(compiled, ReasonerConfig(backend="python")).info.name == "python"

    monkeypatch.setenv("PYELK_PURE_PYTHON", "1")

    def forbidden(name: str) -> Any:
        raise AssertionError(f"pure mode imported {name}")

    monkeypatch.setattr(importlib, "import_module", forbidden)
    assert create_backend_session(compiled, ReasonerConfig(backend="python")).info.name == "python"
    with pytest.raises(ValueError, match="conflicts"):
        create_backend_session(compiled, ReasonerConfig())

    monkeypatch.setenv("PYELK_BACKEND", "invalid")
    with pytest.raises(ValueError, match="PYELK_BACKEND"):
        create_backend_session(compiled, ReasonerConfig(backend="python"))


def test_backend_report_covers_valid_invalid_and_unprobed_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _native()
    monkeypatch.setattr(importlib, "import_module", lambda name: fake)
    report = backend_report()
    assert report.requested == "auto"
    assert report.selected == "rust"
    assert report.python.available is True
    assert report.rust.available is True
    assert report.rust.abi == "abi3-py310"
    assert report.core_package_version == pyowl_core.__version__
    assert report.core_api_version == pyowl_core.API_VERSION
    assert report.core_model_schema_version == pyowl_core.MODEL_SCHEMA_VERSION
    assert report.core_wire_format_version == pyowl_core.WIRE_FORMAT_VERSION
    assert report.core_adapter_protocol_version == pyowl_core.ADAPTER_PROTOCOL_VERSION

    monkeypatch.setenv("PYELK_PURE_PYTHON", "1")
    pure = backend_report()
    assert pure.selected == "python"
    assert pure.rust.available is None
    assert "disabled" in (pure.rust.reason or "")

    monkeypatch.setenv("PYELK_BACKEND", "bad")
    invalid = backend_report()
    assert invalid.selected is None
    assert invalid.selection_error is not None


class _MetadataSession(FakeBackendSession):
    def __init__(self, metadata: object) -> None:
        taxonomy = RawTaxonomy(nodes=((EntityId(0),),), direct_edges=(), top=0, bottom=0)
        super().__init__(class_taxonomy=taxonomy)
        self.metadata = metadata

    def compiler_metadata(self) -> object:
        return self.metadata


class _EncodedFactory:
    def __init__(self, handoff: object | None, session: _MetadataSession) -> None:
        self.handoff = handoff
        self.session = session
        self.created = False

    def negotiate_encoded_input(self, ontology: object) -> SimpleNamespace:
        return SimpleNamespace(handoff=self.handoff)

    def create_encoded_session(self, handoff: object, config: object) -> _MetadataSession:
        assert handoff is self.handoff
        self.created = True
        return self.session


def test_encoded_backend_selection_is_precompile_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiled = TinyCompiledOntologyBuilder().build()
    metadata = metadata_from_compiled(compiled)
    view = pyowl_core.load_snapshot(b"Ontology()")
    session = _MetadataSession(metadata)
    factory = _EncodedFactory(object(), session)
    monkeypatch.setattr(
        "pyelk.backends._probe_native",
        lambda: SimpleNamespace(module=object()),
    )
    monkeypatch.setattr("pyelk.backends._rust_factory", lambda probe: factory)

    selected = try_create_encoded_backend_session(view, ReasonerConfig(backend="rust"))
    assert selected is not None
    assert selected.session is session
    assert selected.metadata == metadata
    assert factory.created is True

    malformed = _MetadataSession(object())
    malformed_factory = _EncodedFactory(object(), malformed)
    monkeypatch.setattr("pyelk.backends._rust_factory", lambda probe: malformed_factory)
    with pytest.raises(BackendProtocolError, match="CompilerMetadata"):
        try_create_encoded_backend_session(view, ReasonerConfig(backend="rust"))
    with pytest.raises(ReasonerClosedError):
        _ = malformed.info


def test_encoded_backend_capability_absence_does_not_create_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata: CompilerMetadata = metadata_from_compiled(TinyCompiledOntologyBuilder().build())
    factory = _EncodedFactory(None, _MetadataSession(metadata))
    monkeypatch.setattr(
        "pyelk.backends._probe_native",
        lambda: SimpleNamespace(module=object()),
    )
    monkeypatch.setattr("pyelk.backends._rust_factory", lambda probe: factory)

    assert (
        try_create_encoded_backend_session(
            pyowl_core.load_snapshot(b"Ontology()"),
            ReasonerConfig(backend="rust"),
        )
        is None
    )
    assert factory.created is False
