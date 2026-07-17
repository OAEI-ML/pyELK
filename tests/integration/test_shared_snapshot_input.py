from __future__ import annotations

import io
from pathlib import Path

import pyowl_core
import pytest

from pyelk.inputs import InputCapture, capture_input
from tests.unit.inputs._support import CountingProvider, functional, load_options


def _observation(captured: InputCapture) -> tuple[object, ...]:
    view = captured.ontology.view
    return (
        tuple(view.iter_axioms()),
        view.signature(include_builtins=False),
        captured.ontology.logical_fingerprint,
        captured.ontology.signature_fingerprint,
        captured.imports.is_complete,
    )


def test_standalone_and_shared_inputs_have_equal_effective_observations(
    tmp_path: Path,
) -> None:
    payload = functional(
        "urn:shared-input",
        body=(
            "Declaration(Class(:A))",
            "Declaration(Class(:B))",
            "SubClassOf(:A :B)",
        ),
    )
    document_iri = pyowl_core.IRI("urn:shared-input-document")
    options = load_options(format=pyowl_core.DocumentFormat.FUNCTIONAL)
    path = tmp_path / "shared.ofn"
    path.write_bytes(payload)
    acquisition_sources = (
        payload,
        bytearray(payload),
        memoryview(payload),
        path,
        str(path),
        io.BytesIO(payload),
        io.StringIO(payload.decode("utf-8")),
    )
    standalone = tuple(
        capture_input(
            source,
            document_iri=document_iri,
            options=options,
        )
        for source in acquisition_sources
    )
    expected = _observation(standalone[0])
    assert all(_observation(item) == expected for item in standalone)
    assert len({item.ontology.structural_fingerprint for item in standalone}) == 1

    snapshot = standalone[0].ontology.view
    assert isinstance(snapshot, pyowl_core.OntologySnapshot)
    document = snapshot.root
    duplicate = pyowl_core.load_snapshot(
        payload,
        document_iri=document_iri,
        options=options,
    )
    overlay = pyowl_core.apply_delta(snapshot, pyowl_core.OntologyDelta())
    composite = pyowl_core.compose_views(
        snapshot,
        duplicate,
        roles=("source", "target"),
    )
    provider = CountingProvider(snapshot)
    shared = (
        capture_input(document, options=options),
        capture_input(snapshot),
        capture_input(overlay),
        capture_input(composite),
        capture_input(provider),
    )
    assert provider.calls == 1
    assert all(_observation(item) == expected for item in shared)
    assert shared[1].ontology.view is snapshot
    assert shared[2].ontology.view is overlay
    assert shared[3].ontology.view is composite
    assert shared[4].ontology.view is snapshot


def test_every_core_required_syntax_enters_through_the_same_adapter() -> None:
    document_iri = pyowl_core.IRI("urn:syntax-document")
    document = pyowl_core.parse_document(
        functional(
            "urn:syntax",
            body=(
                "Declaration(Class(:A))",
                "Declaration(Class(:B))",
                "SubClassOf(:A :B)",
            ),
        ),
        format=pyowl_core.DocumentFormat.FUNCTIONAL,
        document_iri=document_iri,
        options=load_options(),
    )
    captures: list[InputCapture] = []
    for format in pyowl_core.DocumentFormat:
        encoded = pyowl_core.render_document(document, format=format)
        captures.append(
            capture_input(
                encoded,
                document_iri=document_iri,
                options=load_options(format=format),
            )
        )
    expected = _observation(captures[0])
    assert all(_observation(item) == expected for item in captures)
    assert len({item.ontology.logical_fingerprint for item in captures}) == 1
    assert len({item.ontology.signature_fingerprint for item in captures}) == 1


def test_parser_runs_for_source_once_and_never_for_shared_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pyowl_core.backends.python import PythonParser

    calls = 0
    original = PythonParser.parse

    def counted(self: PythonParser, *args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(PythonParser, "parse", counted)
    loaded = capture_input(
        functional("urn:parse-count", body=("Declaration(Class(:A))",)),
        options=load_options(),
    )
    assert calls == 1
    snapshot = loaded.ontology.view
    assert isinstance(snapshot, pyowl_core.OntologySnapshot)
    second = pyowl_core.load_snapshot(
        functional("urn:second", body=("Declaration(Class(:B))",)),
        options=load_options(),
    )
    assert calls == 2
    overlay = pyowl_core.apply_delta(snapshot, pyowl_core.OntologyDelta())
    composite = pyowl_core.compose_views(snapshot, second, roles=("source", "target"))
    provider = CountingProvider(composite)

    for value in (snapshot.root, snapshot, overlay, composite, provider):
        capture_input(value, options=load_options() if value is snapshot.root else None)
    assert calls == 2
    assert provider.calls == 1


def test_source_target_bridge_capture_retains_every_shared_object() -> None:
    options = load_options()
    source = pyowl_core.load_snapshot(
        functional("urn:source", body=("Declaration(Class(:Source))",)),
        options=options,
    )
    target = pyowl_core.load_snapshot(
        functional("urn:target", body=("Declaration(Class(:Target))",)),
        options=options,
    )
    bridge = pyowl_core.SubClassOf(
        pyowl_core.Class(pyowl_core.IRI("urn:test#Source")),
        pyowl_core.Class(pyowl_core.IRI("urn:test#Target")),
    )
    delta = pyowl_core.OntologyDelta(
        add_axioms=pyowl_core.CanonicalSet((bridge,)),
    )
    composite = pyowl_core.compose_views(
        source,
        target,
        delta=delta,
        roles=("source", "target"),
    )
    captured = capture_input(composite)
    assert captured.ontology.view is composite
    assert tuple(member.view for member in composite.members) == (source, target)
    assert composite.requested_delta is delta
    assert composite.delta == delta
    assert captured.imports.manifests[0] is source.import_manifest
    assert captured.imports.manifests[1] is target.import_manifest
