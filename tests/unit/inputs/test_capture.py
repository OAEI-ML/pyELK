from __future__ import annotations

import pyowl_core
import pytest

import pyelk.inputs as inputs

from ._support import CountingProvider, load_options, snapshot


def test_acquisition_and_coercion_exports_are_exact_core_functions() -> None:
    assert inputs.load_snapshot is pyowl_core.load_snapshot
    assert inputs.coerce_snapshot is pyowl_core.coerce_snapshot


def test_capture_calls_core_coercion_once_and_provider_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = snapshot("provider", "A")
    provider = CountingProvider(view)
    calls = 0
    original = pyowl_core.coerce_snapshot

    def counted(*args: object, **kwargs: object) -> pyowl_core.OntologyView:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pyowl_core, "coerce_snapshot", counted)
    captured = inputs.capture_input(provider)
    assert calls == 1
    assert provider.calls == 1
    assert provider.path_fallback_calls == 0
    assert captured.ontology.view is view


def test_snapshot_overlay_composite_revision_shape_and_identity() -> None:
    source = snapshot("source", "A")
    target = snapshot("target", "B")
    added = pyowl_core.Declaration(pyowl_core.Class(pyowl_core.IRI("urn:test#Bridge")))
    overlay = pyowl_core.apply_delta(
        source,
        pyowl_core.OntologyDelta(add_axioms=pyowl_core.CanonicalSet((added,))),
    )
    composite = pyowl_core.compose_views(
        source,
        target,
        delta=pyowl_core.OntologyDelta(add_axioms=pyowl_core.CanonicalSet((added,))),
        roles=("source", "target"),
    )

    snapshot_capture = inputs.capture_input(source)
    overlay_capture = inputs.capture_input(overlay)
    composite_capture = inputs.capture_input(composite)

    assert snapshot_capture.ontology.view is source
    assert snapshot_capture.revision.kind is inputs.OntologyViewKind.SNAPSHOT
    assert overlay_capture.ontology.view is overlay
    assert overlay_capture.revision.kind is inputs.OntologyViewKind.OVERLAY
    assert overlay_capture.revision.overlay_depth == 1
    assert overlay_capture.revision.delta_entries == 1
    assert composite_capture.ontology.view is composite
    assert composite_capture.revision.kind is inputs.OntologyViewKind.COMPOSITE
    assert composite_capture.revision.component_count == 2
    assert composite_capture.revision.delta_entries == 1


def test_document_is_assembled_without_reparse(monkeypatch: pytest.MonkeyPatch) -> None:
    document = pyowl_core.parse_document(
        b"Ontology(<urn:document> Declaration(Class(<urn:A>)))",
        options=load_options(),
    )
    from pyowl_core.backends.python import PythonParser

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("a core document must not be reparsed")

    monkeypatch.setattr(PythonParser, "parse", forbidden)
    captured = inputs.capture_input(document, options=load_options())
    view = captured.ontology.view
    assert isinstance(view, pyowl_core.OntologySnapshot)
    assert view.root is document


def test_load_snapshot_rejects_views_and_providers_without_materializing() -> None:
    view = snapshot("reject", "A")
    provider = CountingProvider(view)
    with pytest.raises(TypeError, match="document source"):
        inputs.load_snapshot(view)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="document source"):
        inputs.load_snapshot(provider)
    assert provider.calls == 0


def test_document_iri_cannot_rebase_view_or_provider() -> None:
    view = snapshot("bound", "A")
    provider = CountingProvider(view)
    for source in (view, provider):
        with pytest.raises(pyowl_core.OptionConflictError) as caught:
            inputs.capture_input(source, document_iri="urn:replacement")
        assert caught.value.code == "DOCUMENT_IRI_SOURCE_CONFLICT"
    assert provider.calls == 0


def test_view_option_conflict_propagates_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = snapshot("options", "A")
    parse_calls = 0
    from pyowl_core.backends.python import PythonParser

    def forbidden(*args: object, **kwargs: object) -> object:
        nonlocal parse_calls
        parse_calls += 1
        raise AssertionError("view option conflict must not trigger parsing")

    monkeypatch.setattr(PythonParser, "parse", forbidden)
    with pytest.raises(pyowl_core.OptionConflictError) as caught:
        inputs.capture_input(
            view,
            options=load_options(pyowl_core.ImportPolicy.RESOLVE_LOCAL),
        )
    assert caught.value.code == "VIEW_IMPORT_OPTION_CONFLICT"
    assert parse_calls == 0
