from __future__ import annotations

import gc
import io
import weakref
from collections.abc import Iterator
from typing import TypeVar, cast

import pyowl_core
import pytest

from pyelk.inputs import capture_input

from ._support import CountingProvider, functional, load_options, snapshot

V = TypeVar("V")


class _CountingBinary(io.BytesIO):
    def __init__(self, value: bytes) -> None:
        super().__init__(value)
        self.read_calls = 0
        self.bytes_read = 0
        self.seek_calls = 0

    def read(self, size: int | None = -1) -> bytes:
        self.read_calls += 1
        value = super().read(size)
        self.bytes_read += len(value)
        return value

    def seek(self, offset: int, whence: int = 0) -> int:
        self.seek_calls += 1
        raise AssertionError("root streams must not be rewound or retried")


class _CountingText(io.StringIO):
    def __init__(self, value: str) -> None:
        super().__init__(value)
        self.read_calls = 0
        self.codepoints_read = 0
        self.seek_calls = 0

    def read(self, size: int | None = -1) -> str:
        self.read_calls += 1
        value = super().read(size)
        self.codepoints_read += len(value)
        return value

    def seek(self, offset: int, whence: int = 0) -> int:
        self.seek_calls += 1
        raise AssertionError("root streams must not be rewound or retried")


class _WeakView:
    def __init__(self, source: pyowl_core.OntologySnapshot, closed: list[str]) -> None:
        self.source = source
        self.closed = closed
        self.iteration_calls = 0

    @property
    def capabilities(self) -> pyowl_core.CoreCapabilities:
        return self.source.capabilities

    def iter_axioms(
        self,
        axiom_type: type[pyowl_core.AxiomNode] | None = None,
        *,
        scope: pyowl_core.AxiomScope = pyowl_core.AxiomScope.CLOSURE,
        document_key: str | None = None,
    ) -> Iterator[pyowl_core.AxiomNode]:
        self.iteration_calls += 1
        return self.source.iter_axioms(
            axiom_type,
            scope=scope,
            document_key=document_key,
        )

    def iter_extensions(
        self,
        namespace: str | None = None,
        *,
        scope: pyowl_core.AxiomScope = pyowl_core.AxiomScope.CLOSURE,
        document_key: str | None = None,
    ) -> Iterator[pyowl_core.StructuralNode]:
        self.iteration_calls += 1
        return self.source.iter_extensions(
            namespace,
            scope=scope,
            document_key=document_key,
        )

    def contains(
        self,
        axiom: pyowl_core.AxiomNode,
        *,
        scope: pyowl_core.AxiomScope = pyowl_core.AxiomScope.CLOSURE,
        document_key: str | None = None,
    ) -> bool:
        return self.source.contains(axiom, scope=scope, document_key=document_key)

    def ontology_annotations(
        self,
        *,
        scope: pyowl_core.AxiomScope = pyowl_core.AxiomScope.CLOSURE,
        document_key: str | None = None,
    ) -> pyowl_core.CanonicalSet[pyowl_core.Annotation]:
        return self.source.ontology_annotations(scope=scope, document_key=document_key)

    def signature(
        self,
        kind: pyowl_core.EntityKind | None = None,
        *,
        scope: pyowl_core.AxiomScope = pyowl_core.AxiomScope.CLOSURE,
        document_key: str | None = None,
        include_builtins: bool = True,
    ) -> tuple[pyowl_core.Entity, ...]:
        return self.source.signature(
            kind,
            scope=scope,
            document_key=document_key,
            include_builtins=include_builtins,
        )

    def view(self, view_type: type[V], /, **options: object) -> V:
        return self.source.view(view_type, **options)

    @property
    def origin_index(self) -> pyowl_core.OriginIndex:
        return self.source.origin_index

    @property
    def is_complete(self) -> bool:
        return self.source.is_complete

    @property
    def structural_fingerprint(self) -> pyowl_core.Fingerprint:
        return self.source.structural_fingerprint

    @property
    def logical_fingerprint(self) -> pyowl_core.Fingerprint:
        return self.source.logical_fingerprint

    @property
    def signature_fingerprint(self) -> pyowl_core.Fingerprint:
        return self.source.signature_fingerprint

    @property
    def report(self) -> pyowl_core.LoadReport:
        return self.source.report

    def close(self) -> None:
        self.closed.append("closed")


def test_binary_and_text_streams_are_single_pass_and_caller_owned() -> None:
    payload = functional("urn:stream", body=("Declaration(Class(:A))",))
    binary = _CountingBinary(payload)
    binary_capture = capture_input(
        binary,
        document_iri="urn:stream-document",
        options=load_options(format=pyowl_core.DocumentFormat.FUNCTIONAL),
    )
    assert binary_capture.ontology.view.logical_fingerprint
    assert binary.bytes_read == len(payload)
    assert binary.read_calls == 2
    assert binary.seek_calls == 0
    assert not binary.closed

    decoded = payload.decode("utf-8")
    text = _CountingText(decoded)
    text_capture = capture_input(
        text,
        document_iri=pyowl_core.IRI("urn:stream-document"),
        options=load_options(format=pyowl_core.DocumentFormat.FUNCTIONAL),
    )
    assert text_capture.ontology.logical_fingerprint == binary_capture.ontology.logical_fingerprint
    assert text.codepoints_read == len(decoded)
    assert text.read_calls == 2
    assert text.seek_calls == 0
    assert not text.closed


def test_capture_keeps_view_alive_but_not_provider_and_never_closes_view() -> None:
    closed: list[str] = []
    view = _WeakView(snapshot("lifetime", "A"), closed)
    provider = CountingProvider(cast(pyowl_core.OntologyView, view))
    view_reference = weakref.ref(view)
    provider_reference = weakref.ref(provider)

    captured = capture_input(provider)
    assert view.iteration_calls == 0
    del provider
    del view
    gc.collect()
    assert provider_reference() is None
    assert view_reference() is captured.ontology.view
    assert closed == []

    del captured
    gc.collect()
    assert view_reference() is None
    assert closed == []


def test_overlay_and_composite_are_never_materialized_or_compacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = snapshot("no-copy-source", "A")
    target = snapshot("no-copy-target", "B")
    overlay = pyowl_core.apply_delta(source, pyowl_core.OntologyDelta())
    composite = pyowl_core.compose_views(overlay, target, roles=("source", "target"))

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("input capture must not materialize or compact a shared view")

    monkeypatch.setattr(pyowl_core.OntologyOverlay, "materialize", forbidden)
    monkeypatch.setattr(pyowl_core.OntologyOverlay, "compact", forbidden)
    monkeypatch.setattr(pyowl_core.OntologyComposite, "materialize", forbidden)
    overlay_capture = capture_input(overlay)
    composite_capture = capture_input(composite)
    assert overlay_capture.ontology.view is overlay
    assert overlay.base is source
    assert composite_capture.ontology.view is composite
    assert tuple(member.view for member in composite.members) == (overlay, target)


def test_shared_capture_uses_no_text_rdf_or_wire_intermediate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = snapshot("no-serialization", "A")

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("shared capture must not serialize")

    monkeypatch.setattr(pyowl_core, "render_document", forbidden)
    monkeypatch.setattr(pyowl_core, "write_document", forbidden)
    captured = capture_input(view)
    assert captured.ontology.view is view
