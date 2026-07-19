from __future__ import annotations

from collections.abc import Iterator
from hashlib import sha256
from typing import TypeVar, cast

import pyowl_core as owl
import pytest

from pyelk.indexing.encoded import (
    ENCODED_SCHEMA_NAME,
    ENCODED_SCHEMA_VERSION,
    negotiate_encoded_structural_view,
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


class _EncodedStructuralView:
    def __init__(self, owner: _View) -> None:
        self.schema_name = ENCODED_SCHEMA_NAME
        self.schema_version = ENCODED_SCHEMA_VERSION
        self.model_schema = 1
        self.owner = owner
        self.scope = owl.AxiomScope.CLOSURE
        self.descriptor = b'{"schema":"pyowl-core/structural-columns","version":1}'
        self.descriptor_digest = sha256(self.descriptor).digest()
        self.buffers = {
            "axiom_tags": memoryview(b"\x00"),
            "axiom_arguments": memoryview(b"\x00\x00\x00\x00"),
        }
        self.segments: tuple[object, ...] = ()
        self.structural_fingerprint = owner.structural_fingerprint


class _View:
    def __init__(self, *, advertise: bool = True) -> None:
        self.capabilities = owl.CoreCapabilities(
            adapter_protocol=1,
            model_schema=1,
            wire_format=(1, 0),
            features=_FEATURES,
            encoded_view_schemas=({ENCODED_SCHEMA_NAME: 1} if advertise else {}),
        )
        self.structural_fingerprint = owl.Fingerprint("sha256", 1, b"s" * 32)
        self.logical_fingerprint = owl.Fingerprint("sha256", 1, b"l" * 32)
        self.signature_fingerprint = owl.Fingerprint("sha256", 1, b"g" * 32)
        self.report = object()
        self.origin_index = owl.OriginIndex()
        self.is_complete = True
        self.encoded = _EncodedStructuralView(self)
        self.requests: list[tuple[type[object], dict[str, object]]] = []

    def iter_axioms(
        self,
        axiom_type: type[owl.AxiomNode] | None = None,
        *,
        scope: owl.AxiomScope = owl.AxiomScope.CLOSURE,
        document_key: str | None = None,
    ) -> Iterator[owl.AxiomNode]:
        return iter(())

    def iter_extensions(
        self,
        namespace: str | None = None,
        *,
        scope: owl.AxiomScope = owl.AxiomScope.CLOSURE,
        document_key: str | None = None,
    ) -> Iterator[owl.StructuralNode]:
        return iter(())

    def contains(
        self,
        axiom: owl.AxiomNode,
        *,
        scope: owl.AxiomScope = owl.AxiomScope.CLOSURE,
        document_key: str | None = None,
    ) -> bool:
        return False

    def ontology_annotations(
        self,
        *,
        scope: owl.AxiomScope = owl.AxiomScope.CLOSURE,
        document_key: str | None = None,
    ) -> owl.CanonicalSet[owl.Annotation]:
        return owl.CanonicalSet()

    def signature(
        self,
        kind: owl.EntityKind | None = None,
        *,
        scope: owl.AxiomScope = owl.AxiomScope.CLOSURE,
        document_key: str | None = None,
        include_builtins: bool = True,
    ) -> tuple[owl.Entity, ...]:
        return ()

    def view(self, view_type: type[V], /, **options: object) -> V:
        self.requests.append((cast(type[object], view_type), dict(options)))
        return cast(V, self.encoded)


def _as_view(value: _View) -> owl.OntologyView:
    return cast(owl.OntologyView, value)


def test_capability_absence_is_a_scalar_fallback_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(owl, "EncodedStructuralView", raising=False)
    view = _View(advertise=False)
    result = negotiate_encoded_structural_view(_as_view(view))
    assert result.available is False
    assert result.handoff is None
    assert result.advertised_schema is None
    assert "does not advertise" in (result.reason or "")
    assert view.requests == []


def test_false_advertising_fails_closed_before_scalar_traversal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(owl, "EncodedStructuralView", raising=False)
    view = _View()
    with pytest.raises(owl.AdapterCompatibilityError, match="exports no"):
        negotiate_encoded_structural_view(_as_view(view))
    assert view.requests == []


def test_valid_handoff_retains_exact_owner_and_read_only_buffers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owl, "EncodedStructuralView", _EncodedStructuralView, raising=False)
    view = _View()
    result = negotiate_encoded_structural_view(_as_view(view))
    assert result.available is True
    handoff = result.handoff
    assert handoff is not None
    assert cast(object, handoff.owner) is view
    assert handoff.encoded_view is view.encoded
    assert handoff.structural_fingerprint is view.structural_fingerprint
    assert handoff.buffer_count == 2
    assert handoff.buffer_bytes == 5
    assert tuple(handoff.buffers) == ("axiom_arguments", "axiom_tags")
    assert all(buffer.readonly for buffer in handoff.buffers.values())
    assert len(handoff.descriptor_digest) == 32
    assert view.requests == [
        (
            _EncodedStructuralView,
            {
                "schema_version": ENCODED_SCHEMA_VERSION,
                "scope": owl.AxiomScope.CLOSURE,
            },
        )
    ]


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("schema_name", "wrong/schema"),
        ("schema_version", 2),
        ("model_schema", 2),
        ("descriptor", b""),
        ("buffers", {}),
        ("buffers", {"writable": bytearray(b"bad")}),
        ("scope", owl.AxiomScope.ROOT),
    ],
)
def test_malformed_advertised_envelope_never_silently_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    invalid: object,
) -> None:
    monkeypatch.setattr(owl, "EncodedStructuralView", _EncodedStructuralView, raising=False)
    view = _View()
    setattr(view.encoded, field, invalid)
    with pytest.raises(owl.BackendProtocolError, match=field):
        negotiate_encoded_structural_view(_as_view(view))
    assert len(view.requests) == 1


def test_owner_fingerprint_and_descriptor_digest_are_bound_to_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owl, "EncodedStructuralView", _EncodedStructuralView, raising=False)

    owner_mismatch = _View()
    owner_mismatch.encoded.owner = _View()
    with pytest.raises(owl.BackendProtocolError, match="owner"):
        negotiate_encoded_structural_view(_as_view(owner_mismatch))

    fingerprint_mismatch = _View()
    fingerprint_mismatch.encoded.structural_fingerprint = owl.Fingerprint("sha256", 1, b"x" * 32)
    with pytest.raises(owl.BackendProtocolError, match="structural_fingerprint"):
        negotiate_encoded_structural_view(_as_view(fingerprint_mismatch))

    digest_mismatch = _View()
    digest_mismatch.encoded.descriptor_digest = b"x" * 32
    with pytest.raises(owl.BackendProtocolError, match="descriptor_digest"):
        negotiate_encoded_structural_view(_as_view(digest_mismatch))


def test_advertised_acquisition_failure_is_a_compatibility_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owl, "EncodedStructuralView", _EncodedStructuralView, raising=False)
    view = _View()

    def fail(view_type: type[V], /, **options: object) -> V:
        raise RuntimeError("provider failed")

    view.view = fail  # type: ignore[method-assign]
    with pytest.raises(owl.AdapterCompatibilityError, match="provider failed"):
        negotiate_encoded_structural_view(_as_view(view))
