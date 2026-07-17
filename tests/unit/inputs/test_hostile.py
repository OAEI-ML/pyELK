from __future__ import annotations

import pyowl_core
import pytest

from pyelk.inputs import capture_input

from ._support import load_options, snapshot


class _InvalidProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.path_calls = 0

    def owl_snapshot(self) -> object:
        self.calls += 1
        return object()

    def __fspath__(self) -> str:
        self.path_calls += 1
        raise AssertionError("invalid provider must not fall back to a path")


class _ErroringProvider:
    def __init__(self) -> None:
        self.calls = 0

    def owl_snapshot(self) -> pyowl_core.OntologyView:
        self.calls += 1
        raise RuntimeError("provider failed")


class _ErroringStream:
    def __init__(self) -> None:
        self.calls = 0
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        self.calls += 1
        raise OSError("stream failed")


class _DuckOntology:
    def __init__(self) -> None:
        self.iterations = 0
        self.stringifications = 0

    def __iter__(self):  # type: ignore[no-untyped-def]
        self.iterations += 1
        return iter(())

    def __str__(self) -> str:
        self.stringifications += 1
        return "Ontology()"


def test_invalid_provider_is_called_once_and_never_reinterpreted() -> None:
    provider = _InvalidProvider()
    with pytest.raises(pyowl_core.AdapterCompatibilityError) as caught:
        capture_input(provider)
    assert caught.value.code == "ADAPTER_PROVIDER_RESULT"
    assert provider.calls == 1
    assert provider.path_calls == 0


def test_provider_exception_is_preserved_without_retry() -> None:
    provider = _ErroringProvider()
    with pytest.raises(RuntimeError, match="provider failed"):
        capture_input(provider)
    assert provider.calls == 1


def test_erroring_stream_is_read_once_and_remains_caller_owned() -> None:
    stream = _ErroringStream()
    with pytest.raises(OSError, match="stream failed"):
        capture_input(
            stream,  # type: ignore[arg-type]
            document_iri="urn:error-stream",
            options=load_options(format=pyowl_core.DocumentFormat.FUNCTIONAL),
        )
    assert stream.calls == 1
    assert not stream.closed


def test_duck_typed_legacy_ontology_is_not_traversed_or_serialized() -> None:
    value = _DuckOntology()
    with pytest.raises(TypeError):
        capture_input(value, options=load_options())  # type: ignore[arg-type]
    assert value.iterations == 0
    assert value.stringifications == 0


def test_existing_view_resolver_conflict_does_not_call_resolver() -> None:
    class Resolver:
        def __init__(self) -> None:
            self.calls = 0

        def resolve(
            self,
            request: pyowl_core.ImportRequest,
        ) -> pyowl_core.ResolvedDocument | None:
            self.calls += 1
            return None

    resolver = Resolver()
    with pytest.raises(pyowl_core.OptionConflictError) as caught:
        capture_input(snapshot("resolver-conflict", "A"), resolver=resolver)
    assert caught.value.code == "VIEW_RESOLVER_CONFLICT"
    assert resolver.calls == 0
