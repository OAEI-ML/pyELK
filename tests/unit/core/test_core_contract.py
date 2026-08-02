from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from typing import TypeVar, cast

import pyowl_core
import pytest

import pyelk.core as elk_core
from pyelk.core import (
    AdapterCompatibilityError,
    CapturedOntology,
    CoreVersionInfo,
    OptionConflictError,
    capture_compatible_view,
    require_core_compatibility,
)

V = TypeVar("V")

_FEATURES = frozenset(
    {
        "document-boundaries",
        "document-scoped-anonymous",
        "import-manifest",
        "owl2-structural",
    }
)


class _View:
    def __init__(
        self,
        *,
        capabilities: pyowl_core.CoreCapabilities | None = None,
        logical_fingerprint: object | None = None,
    ) -> None:
        self.capabilities = capabilities or pyowl_core.CoreCapabilities(
            adapter_protocol=1,
            model_schema=2,
            wire_format=(1, 2),
            features=_FEATURES,
        )
        self.structural_fingerprint = pyowl_core.Fingerprint("sha256", 2, b"s" * 32)
        self.logical_fingerprint = (
            pyowl_core.Fingerprint("sha256", 2, b"l" * 32)
            if logical_fingerprint is None
            else logical_fingerprint
        )
        self.signature_fingerprint = pyowl_core.Fingerprint("sha256", 2, b"g" * 32)
        self.report = object()
        self.origin_index = pyowl_core.OriginIndex()
        self.is_complete = True

    def iter_axioms(
        self,
        axiom_type: type[pyowl_core.AxiomNode] | None = None,
        *,
        scope: pyowl_core.AxiomScope = pyowl_core.AxiomScope.CLOSURE,
        document_key: str | None = None,
    ) -> Iterator[pyowl_core.AxiomNode]:
        return iter(())

    def iter_extensions(
        self,
        namespace: str | None = None,
        *,
        scope: pyowl_core.AxiomScope = pyowl_core.AxiomScope.CLOSURE,
        document_key: str | None = None,
    ) -> Iterator[pyowl_core.StructuralNode]:
        return iter(())

    def contains(
        self,
        axiom: pyowl_core.AxiomNode,
        *,
        scope: pyowl_core.AxiomScope = pyowl_core.AxiomScope.CLOSURE,
        document_key: str | None = None,
    ) -> bool:
        return False

    def ontology_annotations(
        self,
        *,
        scope: pyowl_core.AxiomScope = pyowl_core.AxiomScope.CLOSURE,
        document_key: str | None = None,
    ) -> pyowl_core.CanonicalSet[pyowl_core.Annotation]:
        return pyowl_core.CanonicalSet()

    def signature(
        self,
        kind: pyowl_core.EntityKind | None = None,
        *,
        scope: pyowl_core.AxiomScope = pyowl_core.AxiomScope.CLOSURE,
        document_key: str | None = None,
        include_builtins: bool = True,
    ) -> tuple[pyowl_core.Entity, ...]:
        return ()

    def view(self, view_type: type[V], /, **options: object) -> V:
        return cast(V, self)


def _as_view(value: object) -> pyowl_core.OntologyView:
    return cast(pyowl_core.OntologyView, value)


def test_shared_contract_exports_are_exact_core_objects() -> None:
    exact_names = (
        "ADAPTER_PROTOCOL_VERSION",
        "API_VERSION",
        "MODEL_SCHEMA_VERSION",
        "WIRE_FORMAT_VERSION",
        "AdapterCompatibilityError",
        "AxiomScope",
        "BackendPreference",
        "CoreCapabilities",
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
        "coerce_snapshot",
        "compose_views",
        "load_snapshot",
    )
    for name in exact_names:
        assert getattr(elk_core, name) is getattr(pyowl_core, name), name
        assert name in elk_core.__all__
    assert AdapterCompatibilityError is pyowl_core.AdapterCompatibilityError
    assert OptionConflictError is pyowl_core.OptionConflictError


def test_current_core_contract_is_compatible() -> None:
    versions = require_core_compatibility()
    assert versions.package_version == pyowl_core.__version__
    assert versions.api_version == pyowl_core.API_VERSION
    assert versions.model_schema_version == pyowl_core.MODEL_SCHEMA_VERSION
    assert versions.wire_format_version == pyowl_core.WIRE_FORMAT_VERSION
    assert versions.adapter_protocol_version == pyowl_core.ADAPTER_PROTOCOL_VERSION


def test_compatible_patch_prerelease_and_wire_minor_are_accepted() -> None:
    versions = CoreVersionInfo("0.2.99rc1+local", (0, 2), 2, (1, 99), 1)
    assert require_core_compatibility(versions) is versions


@pytest.mark.parametrize(
    "versions",
    [
        CoreVersionInfo("0.1.99", (0, 2), 2, (1, 2), 1),
        CoreVersionInfo("not-semver", (0, 2), 2, (1, 2), 1),
        CoreVersionInfo("0.2.0", (0, 1), 2, (1, 2), 1),
        CoreVersionInfo("0.2.0", (0, 2), 1, (1, 2), 1),
        CoreVersionInfo("0.2.0", (0, 2), 2, (2, 0), 1),
        CoreVersionInfo("0.2.0", (0, 2), 2, (1, 1), 1),
        CoreVersionInfo("0.2.0", (0, 2), 2, (1, 2), 2),
    ],
)
def test_incompatible_core_contract_has_structured_expected_actual_diagnostics(
    versions: CoreVersionInfo,
) -> None:
    with pytest.raises(pyowl_core.AdapterCompatibilityError) as caught:
        require_core_compatibility(versions)
    diagnostic = caught.value.diagnostic
    assert diagnostic is not None
    assert diagnostic.code == "ADAPTER_COMPATIBILITY"
    assert diagnostic.details["field"]
    for dimension in ("package", "api", "model", "wire", "adapter"):
        assert f"expected_{dimension}" in diagnostic.details
        assert f"actual_{dimension}" in diagnostic.details


@pytest.mark.parametrize(
    "capabilities",
    [
        pyowl_core.CoreCapabilities(2, 2, (1, 2), _FEATURES),
        pyowl_core.CoreCapabilities(1, 1, (1, 2), _FEATURES),
        pyowl_core.CoreCapabilities(1, 2, (2, 0), _FEATURES),
        pyowl_core.CoreCapabilities(1, 2, (1, 1), _FEATURES),
        pyowl_core.CoreCapabilities(1, 2, (1, 2), frozenset({"owl2-structural"})),
    ],
)
def test_incompatible_view_capabilities_fail_before_capture(
    capabilities: pyowl_core.CoreCapabilities,
) -> None:
    with pytest.raises(pyowl_core.AdapterCompatibilityError) as caught:
        capture_compatible_view(_as_view(_View(capabilities=capabilities)))
    assert caught.value.diagnostic is not None
    assert caught.value.diagnostic.details["field"]


def test_non_view_and_non_core_fingerprint_fail_closed() -> None:
    with pytest.raises(pyowl_core.AdapterCompatibilityError, match="OntologyView"):
        capture_compatible_view(_as_view(object()))
    with pytest.raises(pyowl_core.AdapterCompatibilityError, match="logical_fingerprint"):
        capture_compatible_view(_as_view(_View(logical_fingerprint=object())))
    with pytest.raises(
        pyowl_core.AdapterCompatibilityError,
        match=r"logical_fingerprint\.schema",
    ):
        capture_compatible_view(
            _as_view(
                _View(
                    logical_fingerprint=pyowl_core.Fingerprint("sha256", 1, b"l" * 32),
                )
            )
        )


def test_bounded_view_validation_does_not_evaluate_fingerprint_descriptors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _View()

    def forbidden(_view: object) -> object:
        raise AssertionError("bounded validation read an ontology fingerprint")

    for name in (
        "structural_fingerprint",
        "logical_fingerprint",
        "signature_fingerprint",
    ):
        monkeypatch.setattr(_View, name, property(forbidden), raising=False)

    assert elk_core._require_compatible_view(_as_view(view)) == elk_core.current_core_versions()


def test_capture_retains_exact_view_and_fingerprint_identities() -> None:
    raw_view: object = _View()
    view = cast(_View, raw_view)
    captured = capture_compatible_view(_as_view(raw_view))
    assert captured.view is raw_view
    assert captured.structural_fingerprint is view.structural_fingerprint
    assert captured.logical_fingerprint is view.logical_fingerprint
    assert captured.signature_fingerprint is view.signature_fingerprint
    with pytest.raises(dataclasses.FrozenInstanceError):
        captured.view = _as_view(_View())  # type: ignore[misc]


def test_captured_ontology_is_metadata_not_a_structural_model_copy() -> None:
    captured = capture_compatible_view(_as_view(_View()))
    assert tuple(field.name for field in dataclasses.fields(CapturedOntology)) == (
        "view",
        "structural_fingerprint",
        "logical_fingerprint",
        "signature_fingerprint",
        "core_package_version",
        "core_api_version",
        "core_model_schema_version",
        "core_wire_format_version",
        "core_adapter_protocol_version",
    )
    assert not hasattr(captured, "axioms")
    assert not hasattr(captured, "documents")
