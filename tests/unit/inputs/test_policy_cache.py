from __future__ import annotations

import pyowl_core
import pytest

from pyelk.inputs import (
    capture_input,
    semantic_cache_record,
    structural_cache_record,
)

from ._support import CountingResolver, functional, load_options, snapshot


def test_ignored_and_unresolved_imports_are_captured_by_manifest_identity() -> None:
    root = functional("urn:root", imports=("urn:missing",))
    resolver = CountingResolver()
    ignored = capture_input(
        root,
        options=load_options(pyowl_core.ImportPolicy.IGNORE),
        resolver=resolver,
    )
    assert resolver.calls == []
    assert not ignored.imports.is_complete
    assert ignored.imports.requires_incomplete_imports
    assert ignored.imports.policies == (pyowl_core.ImportPolicy.IGNORE,)
    ignored_view = ignored.ontology.view
    assert isinstance(ignored_view, pyowl_core.OntologySnapshot)
    assert ignored.imports.manifests[0] is ignored_view.import_manifest

    with pytest.warns(pyowl_core.UnresolvedImportWarning):
        unresolved = capture_input(
            root,
            options=load_options(pyowl_core.ImportPolicy.RECORD_UNRESOLVED),
            resolver=resolver,
        )
    assert not unresolved.imports.is_complete
    assert unresolved.imports.policies == (pyowl_core.ImportPolicy.RECORD_UNRESOLVED,)


@pytest.mark.parametrize(
    "policy",
    (pyowl_core.ImportPolicy.RESOLVE_LOCAL, pyowl_core.ImportPolicy.RESOLVE_STRICT),
)
def test_strict_missing_import_error_is_not_wrapped(policy: pyowl_core.ImportPolicy) -> None:
    with pytest.raises(pyowl_core.UnresolvedImportError):
        capture_input(
            functional("urn:root", imports=("urn:missing",)),
            options=load_options(policy),
            resolver=CountingResolver(),
        )


def test_complete_cycle_is_loaded_once_and_capture_does_not_resolve_again() -> None:
    first = functional(
        "urn:a",
        imports=("urn:b",),
        body=("Declaration(Class(:A))",),
    )
    second = functional(
        "urn:b",
        imports=("urn:a",),
        body=("Declaration(Class(:B))",),
    )
    resolver = CountingResolver({"urn:a": first, "urn:b": second})
    captured = capture_input(
        first,
        options=load_options(pyowl_core.ImportPolicy.RESOLVE_LOCAL),
        resolver=resolver,
    )
    assert resolver.calls == ["urn:b", "urn:a"]
    assert captured.imports.is_complete
    view = captured.ontology.view
    assert isinstance(view, pyowl_core.OntologySnapshot)
    assert len(view.documents) == 2
    assert len(view.import_manifest.edges) == 2

    recaptured = capture_input(view)
    assert recaptured.ontology.view is view
    assert resolver.calls == ["urn:b", "urn:a"]


def test_composite_and_overlay_retain_leaf_manifest_objects() -> None:
    complete = snapshot("complete", "A")
    incomplete_view = pyowl_core.load_snapshot(
        functional("urn:incomplete", imports=("urn:missing",)),
        options=load_options(pyowl_core.ImportPolicy.IGNORE),
    )
    overlay = pyowl_core.apply_delta(complete, pyowl_core.OntologyDelta())
    composite = pyowl_core.compose_views(
        overlay,
        incomplete_view,
        roles=("source", "target"),
    )
    captured = capture_input(composite)
    assert not captured.imports.is_complete
    assert captured.imports.requires_incomplete_imports
    assert captured.imports.manifests == (
        complete.import_manifest,
        incomplete_view.import_manifest,
    )
    assert captured.imports.manifests[0] is complete.import_manifest
    assert captured.imports.manifests[1] is incomplete_view.import_manifest


def test_semantic_and_structural_records_bind_independent_core_dimensions() -> None:
    captured = capture_input(snapshot("keys", "A")).ontology
    options_fingerprint = b"o" * 32
    semantic = semantic_cache_record(
        captured,
        compiler_schema_version=7,
        compatibility_id="elk-b8ac5ce-v1",
        semantic_options_fingerprint=options_fingerprint,
    )
    structural = structural_cache_record(
        captured,
        compiler_schema_version=7,
        compatibility_id="elk-b8ac5ce-v1",
        semantic_options_fingerprint=options_fingerprint,
    )
    assert semantic.logical_fingerprint is captured.logical_fingerprint
    assert semantic.signature_fingerprint is captured.signature_fingerprint
    assert semantic.core_package_version == captured.core_package_version
    assert semantic.core_api_version == captured.core_api_version
    assert semantic.core_model_schema_version == captured.core_model_schema_version
    assert semantic.core_wire_format_version == captured.core_wire_format_version
    assert semantic.core_adapter_protocol_version == captured.core_adapter_protocol_version
    assert semantic.compiler_schema_version == 7
    assert semantic.compatibility_id == "elk-b8ac5ce-v1"
    assert semantic.semantic_options_fingerprint is options_fingerprint
    assert structural.semantic == semantic
    assert structural.structural_fingerprint is captured.structural_fingerprint


def test_cache_records_reject_ambiguous_schema_and_option_identity() -> None:
    captured = capture_input(snapshot("bad-key", "A")).ontology
    with pytest.raises(ValueError, match="compiler_schema_version"):
        semantic_cache_record(
            captured,
            compiler_schema_version=0,
            compatibility_id="elk",
        )
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        semantic_cache_record(
            captured,
            compiler_schema_version=1,
            compatibility_id="elk",
            semantic_options_fingerprint=b"short",
        )
