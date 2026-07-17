from __future__ import annotations

from collections.abc import Iterable

import pyowl_core


def functional(
    ontology_iri: str,
    *,
    imports: Iterable[str] = (),
    body: Iterable[str] = (),
    whitespace: str = " ",
) -> bytes:
    components = [*(f"Import(<{item}>)" for item in imports), *body]
    content = whitespace.join(components)
    return (
        f"Prefix(:=<urn:test#>){whitespace}"
        f"Ontology(<{ontology_iri}>{whitespace}{content})"
    ).encode()


def load_options(
    policy: pyowl_core.ImportPolicy = pyowl_core.ImportPolicy.IGNORE,
    *,
    format: pyowl_core.DocumentFormat | None = None,
) -> pyowl_core.LoadOptions:
    return pyowl_core.LoadOptions(
        format=format,
        imports=policy,
        backend=pyowl_core.BackendPreference.PYTHON,
    )


def snapshot(identity: str, *names: str) -> pyowl_core.OntologySnapshot:
    body = tuple(f"Declaration(Class(:{name}))" for name in names)
    return pyowl_core.load_snapshot(
        functional(f"urn:{identity}", body=body),
        options=load_options(),
    )


class CountingProvider:
    def __init__(self, view: pyowl_core.OntologyView) -> None:
        self.view = view
        self.calls = 0
        self.path_fallback_calls = 0

    def owl_snapshot(self) -> pyowl_core.OntologyView:
        self.calls += 1
        return self.view

    def __fspath__(self) -> str:
        self.path_fallback_calls += 1
        raise AssertionError("SnapshotProvider must never fall back to a path")


class CountingResolver:
    def __init__(self, values: dict[str, bytes] | None = None) -> None:
        self.values = values or {}
        self.calls: list[str] = []

    def resolve(
        self,
        request: pyowl_core.ImportRequest,
    ) -> pyowl_core.ResolvedDocument | None:
        self.calls.append(request.import_iri.value)
        value = self.values.get(request.import_iri.value)
        if value is None:
            return None
        return pyowl_core.ResolvedDocument(value, request.import_iri)
